"""Selector / element / ruleset-head parser mixin.

Covers six tightly-coupled methods:

* `_ruleset`      — `<selectors> { <body> }` consumer.
* `_selectors`    — comma-separated list of selectors.
* `_selector`     — single selector: combinators + elements + tail.
* `_element`      — one element (`.foo`, `> .bar`, `[attr]`, ...).
* `_read_element_body` — element-body token decoder.
* `_parse_extends_in_elements` — pull `:extend(...)` pieces out of
  a parsed element list into structured `Extend` nodes.

Hosts the `_SELECTOR_END` and `_SIMPLE_ELEMENT_HEAD` constants (no
other consumer outside this mixin).
"""

from __future__ import annotations

from ..ast_nodes import Element, Extend, Ruleset, Selector
from ..errors import ParseError
from ..lexer import Kind
from ._state import _ParserState
from .helpers import _parse_extend_args

# Selector terminators at the top of a block.
_SELECTOR_END: frozenset[Kind] = frozenset({Kind.COMMA, Kind.LBRACE, Kind.SEMICOLON, Kind.RBRACE, Kind.EOF})

# Tokens that can start a single element body (no combinator, no extras).
_SIMPLE_ELEMENT_HEAD: frozenset[Kind] = frozenset(
    {
        Kind.DOT_IDENT,
        Kind.HASH,
        Kind.IDENT,
        Kind.AMPERSAND,
        Kind.STAR,
        Kind.DOT,
        Kind.HASH_BARE,
    }
)


class _SelectorsMethods(_ParserState):
    """Selector / element / ruleset-head parsing."""

    def _ruleset(self) -> Ruleset | None:
        snap = self.stream.save()
        selectors = self._selectors()
        if selectors is None or self.stream.peek_kind() is not Kind.LBRACE:
            self.stream.restore(snap)
            return None
        rs_index = selectors[0].index
        self.stream.consume()  # LBRACE
        rules = self._primary()
        # less.js's wording for "EOF before the matching '}'" is
        # `Unrecognised input. Possibly missing something`; reserve the
        # "missing '}' for ruleset body" message for the case where the
        # close-brace position contains some other punctuation.
        if self.stream.peek_kind() is Kind.EOF:
            self._error('Unrecognised input. Possibly missing something', self.stream.peek().index)
        self.stream.expect(Kind.RBRACE, "missing '}' for ruleset body")
        return Ruleset(index=rs_index, selectors=selectors, rules=rules)

    def _selectors(self) -> list[Selector] | None:
        first = self._selector()
        if first is None:
            return None
        sels = [first]
        while self.stream.match(Kind.COMMA):
            s = self._selector()
            if s is None:
                return None
            sels.append(s)
        return sels

    def _parse_extends_in_elements(self, elements: list[Element]) -> tuple[list[Element], list[Extend]]:
        """Split `:extend(...)` pieces out of selector elements into
        structured Extend nodes. Returns (remaining_elements, extends).

        Empty selectors that become "extends only" (statement-form
        through compound `&:extend`) keep a `&` element so they still
        carry positional info for the extend visitor.

        Strict check: `:extend(...)` MUST be the last element in the
        selector. less.js raises `Extend can only be used at the end
        of selector` for forms like `.a:extend(.b).c`, anchored at the
        position of the FOLLOWING token (typically the block `{`).
        """
        remaining: list[Element] = []
        extends: list[Extend] = []
        i = 0
        n = len(elements)
        while i < n:
            elem = elements[i]
            if elem.value.startswith(':extend('):
                # Anything after this :extend() on the same selector is
                # forbidden by less.js, EXCEPT a trailing `when (...)`
                # CSS-guard clause (`.x:extend(.y all) when (@cond) {}`
                # — used widely in uikit). The guard tail is peeled off
                # by `_extract_css_guard` later in transform_mixins.
                j = i + 1
                guard_start: int | None = None
                while j < n:
                    later = elements[j]
                    if later.value.startswith(':extend('):
                        j += 1
                        continue
                    if later.value == 'when':
                        guard_start = j
                        break
                    # Anchor at the upcoming block-open token (less.js
                    # convention), or fall back to the offending element.
                    anchor_tok = self.stream.peek()
                    anchor = anchor_tok.index if anchor_tok.kind is not Kind.EOF else later.index
                    err = ParseError('Extend can only be used at the end of selector')
                    err._less_js_name = 'SyntaxError'
                    err._base_index = anchor
                    raise err
                inside = elem.value[len(':extend(') : -1]
                for ext in _parse_extend_args(inside, base_index=elem.index):
                    extends.append(ext)
                if guard_start is not None:
                    # Collect any further `:extend(...)` between this one
                    # and the guard tail, then append the guard.
                    k = i + 1
                    while k < guard_start:
                        e2 = elements[k]
                        if e2.value.startswith(':extend('):
                            inside2 = e2.value[len(':extend(') : -1]
                            for ext in _parse_extend_args(inside2, base_index=e2.index):
                                extends.append(ext)
                        k += 1
                    remaining.extend(elements[guard_start:])
                    break
                i += 1
                continue
            remaining.append(elem)
            i += 1
        return remaining, extends

    def _selector(self) -> Selector | None:
        elements: list[Element] = []
        start_index = self.stream.peek().index
        while True:
            tok = self.stream.peek()
            if tok.kind in _SELECTOR_END:
                break
            elem = self._element(has_prev=bool(elements))
            if elem is None:
                break
            elements.append(elem)
        if not elements:
            return None

        # Selector tail absorption. Everything between the last
        # decomposed element and the next top-level block-structural
        # token (`{`, `;`, `}`, EOF) is captured as raw text on a final
        # tail element. This keeps the parser permissive enough to
        # swallow mixin definition signatures (`.mixin(@a, @b)`), CSS
        # guards (`.x when (lightness(@a) > 50%)`), and similar
        # constructs whose internals the evaluator refines later.
        # Top-level `,` is NOT a terminator here so guard OR-clauses
        # (`when (a), (b)`) stay attached to the current selector.
        tail_terminators: frozenset[Kind] = frozenset({Kind.LBRACE, Kind.SEMICOLON, Kind.RBRACE, Kind.EOF})
        if self.stream.peek_kind() not in tail_terminators and self.stream.peek_kind() is not Kind.COMMA:
            tail_start_tok = self.stream.peek()
            tail_start = tail_start_tok.index
            tail_end = tail_start
            paren = 0
            bracket = 0
            interp = 0
            # Whether we've already seen a `when` keyword anywhere in
            # this selector — as its own element (CSS-guarded
            # selector: `.a when (...)`) or absorbed into a prior
            # mixin-signature element (`.mixin (args) when (...)`).
            # When set, the tail is a guard region and top-level
            # commas keep absorbing (`when (a), (b)` and `when not (a),
            # not (b)` are both multi-clause OR guards). Updated
            # dynamically below as we eat tokens.
            in_guard_tail = any(e.value == 'when' or ' when ' in e.value or e.value.endswith(' when') for e in elements)
            while True:
                tok = self.stream.peek()
                if tok.kind is Kind.EOF:
                    break
                if paren == 0 and bracket == 0 and interp == 0 and tok.kind in tail_terminators:
                    break
                if paren == 0 and bracket == 0 and interp == 0 and tok.kind is Kind.COMMA:
                    # Top-level COMMA: usually a selector-list
                    # separator (`.a when (...), .b`). Inside a guard
                    # region (`.x when (a), (b)`) the comma continues
                    # the multi-clause OR if the next non-trivia token
                    # looks like another guard clause — `(`, `not (`,
                    # or a `default()` keyword. Otherwise stop here so
                    # `.b` parses as its own selector.
                    if in_guard_tail:
                        nxt = self.stream.peek(1)
                        nxt2 = self.stream.peek(2)
                        looks_like_guard_clause = nxt.kind is Kind.LPAREN or (
                            nxt.kind is Kind.IDENT and nxt.text == 'not' and nxt2.kind is Kind.LPAREN
                        )
                        if not looks_like_guard_clause:
                            break
                    else:
                        break
                if not in_guard_tail and tok.kind is Kind.IDENT and tok.text == 'when':
                    in_guard_tail = True
                if tok.kind is Kind.LPAREN:
                    paren += 1
                elif tok.kind is Kind.RPAREN:
                    paren = max(0, paren - 1)
                elif tok.kind is Kind.LBRACKET:
                    bracket += 1
                elif tok.kind is Kind.RBRACKET:
                    bracket = max(0, bracket - 1)
                elif tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                    interp += 1
                elif tok.kind is Kind.RBRACE and interp > 0:
                    interp -= 1
                tail_end = tok.index + len(tok.text)
                self.stream.consume()
            if tail_end > tail_start:
                combinator = ' ' if self._has_ws_before(tail_start_tok) else ''
                elements.append(
                    Element(
                        index=tail_start,
                        combinator=combinator,
                        value=self.source.text[tail_start:tail_end].strip(),
                    )
                )

        elements, extend_list = self._parse_extends_in_elements(elements)
        if not elements:
            if extend_list:
                # Standalone `:extend(...)` — no preceding compound selector
                # to extend FROM. less.js raises a SyntaxError anchored at
                # the upcoming block-open token.
                anchor_tok = self.stream.peek()
                anchor = anchor_tok.index if anchor_tok.kind is not Kind.EOF else start_index
                err = ParseError('Extend must be used to extend a selector, it cannot be used on its own')
                err._less_js_name = 'SyntaxError'
                err._base_index = anchor
                raise err
            # All elements were `:extend(...)` clauses with no real
            # selector left. Synthesize `&` so the selector still binds
            # to the enclosing rule's `paths` and the extend visitor
            # can see the chain.
            elements = [Element(index=start_index, combinator='', value='&')]
        return Selector(index=start_index, elements=elements, extend_list=extend_list)

    def _element(self, *, has_prev: bool) -> Element | None:
        tok = self.stream.peek()
        explicit_combinator = False
        combinator = ''
        elem_index = tok.index

        if tok.kind in (Kind.GT, Kind.PLUS, Kind.TILDE, Kind.PIPE):
            combinator = tok.text
            explicit_combinator = True
            self.stream.consume()
            tok = self.stream.peek()
            if tok.kind in _SELECTOR_END:
                return Element(index=elem_index, combinator=combinator, value='')
        elif has_prev and self._has_ws_before(tok):
            combinator = ' '

        body_index = tok.index
        value = self._read_element_body()
        if value is None:
            # If we consumed a combinator and nothing followed, emit an
            # empty-bodied element so the selector ends gracefully. With
            # an implicit (whitespace) combinator we did NOT consume
            # anything — returning an empty element would not advance the
            # stream and the caller would loop forever; return None
            # instead so the selector loop terminates and the tail-
            # absorption path can take over.
            if explicit_combinator:
                return Element(index=elem_index, combinator=combinator, value='')
            return None
        return Element(index=body_index, combinator=combinator, value=value)

    def _read_element_body(self) -> str | None:
        tok = self.stream.peek()

        if tok.kind in _SIMPLE_ELEMENT_HEAD:
            self.stream.consume()
            value = tok.text
            # Numbers don't head selectors directly, but `50%` in keyframe
            # selectors does — see special handling below if NUMBER is
            # encountered.
            return value

        if tok.kind is Kind.NUMBER:
            # `50%` keyframe selector — NUMBER followed by PERCENT without
            # whitespace combines into one element.
            self.stream.consume()
            nt = self.stream.peek()
            if nt.kind is Kind.PERCENT and not self._has_ws_before(nt):
                self.stream.consume()
                return tok.text + nt.text
            return tok.text

        if tok.kind in (Kind.COLON, Kind.DOUBLECOLON):
            value = tok.text
            self.stream.consume()
            nt = self.stream.peek()
            if nt.kind is Kind.IDENT and not self._has_ws_before(nt):
                value += nt.text
                self.stream.consume()
                after = self.stream.peek()
                if after.kind is Kind.LPAREN and not self._has_ws_before(after):
                    value += self._read_balanced(Kind.LPAREN, Kind.RPAREN)
            elif nt.kind is Kind.INTERP_OPEN and not self._has_ws_before(nt):
                value += self._read_interpolation()
            return value

        if tok.kind is Kind.EXTEND_KEYWORD:
            value = tok.text
            self.stream.consume()
            if self.stream.peek_kind() is Kind.LPAREN:
                value += self._read_balanced(Kind.LPAREN, Kind.RPAREN)
            return value

        if tok.kind is Kind.LBRACKET:
            return self._read_balanced(Kind.LBRACKET, Kind.RBRACKET)

        if tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
            return self._read_interpolation()

        return None
