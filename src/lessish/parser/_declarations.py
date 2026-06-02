"""Declaration parser mixin.

Three methods: `_declaration` (entry), `_value_started_after_newline`
(small leading-trivia check), and `_read_declaration_value` (the
big text-capture loop with bracket/paren/brace/interp depth
tracking and the `!important` anonymousValue-fast-path
whitespace-preservation hack — full comments inline below).
"""

from __future__ import annotations

from ..ast_nodes import Anonymous, Declaration
from ..lexer import Kind, Token
from ._state import _ParserState


class _DeclarationsMethods(_ParserState):
    """`prop: value [!important];` and the variable-decl form
    `@name: value;`. Lookahead-restorable: returns `None` (after
    restoring the stream) when the head doesn't look like a
    declaration, so `_block_lookahead`'s caller can fall through to
    `_ruleset` / `_statement`.
    """

    def _declaration(self) -> Declaration | None:
        snap = self.stream.save()
        name_tokens: list[Token] = []
        merge = ''
        first_index = self.stream.peek().index

        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.COLON:
                break
            if tok.kind in (Kind.PLUS_COLON, Kind.PLUS_UNDER_COLON):
                merge = '+' if tok.kind is Kind.PLUS_COLON else '+_'
                break
            # Whitespace-separated merge form: `name +: value` or `name + : value`.
            # less.js accepts these even though the strict lexer pattern requires
            # `+:` adjacent; recognise it here by peeking past the `+`.
            if tok.kind is Kind.PLUS and self.stream.peek_kind(1) is Kind.COLON:
                merge = '+'
                self.stream.consume()
                break
            if tok.kind in (Kind.SEMICOLON, Kind.RBRACE, Kind.EOF, Kind.LBRACE, Kind.COMMA):
                self.stream.restore(snap)
                return None
            if name_tokens and self._has_ws_before(tok):
                self.stream.restore(snap)
                return None
            # Interpolation as part of name: @{foo}-color, ${prop},
            # @{-}, @{a-b}, etc. Be permissive about what's inside `{...}`:
            # accept any tokens up to the matching `}` (with nesting).
            if tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                name_tokens.append(self.stream.consume())
                depth = 1
                while depth > 0:
                    nt = self.stream.peek()
                    if nt.kind is Kind.EOF:
                        self.stream.restore(snap)
                        return None
                    if nt.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                        depth += 1
                    elif nt.kind is Kind.RBRACE:
                        depth -= 1
                    name_tokens.append(self.stream.consume())
                continue
            name_tokens.append(tok)
            self.stream.consume()

        if not name_tokens:
            self.stream.restore(snap)
            return None

        # less.js rejects `*` as a sole property name — `*` is the CSS
        # universal selector, only valid at selector position. Anchor at
        # the `:` (the upcoming token) — matches less.js's column.
        if len(name_tokens) == 1 and name_tokens[0].kind is Kind.STAR:
            self._error('Unrecognised input', self.stream.peek().index)

        self.stream.consume()  # COLON / PLUS_COLON / PLUS_UNDER_COLON

        name = ''.join(t.text for t in name_tokens)
        # `@varname` is a Less variable; `@{varname}` is a CSS property name
        # with embedded interpolation. Only the former is filtered at emit.
        variable = name.startswith('@') and not name.startswith('@{')

        # A detached-ruleset literal (`{ … }`) is only valid as the value
        # of a *variable* (`@x: { … };`). On a CSS property (`prop: { … };`)
        # less.js rejects it at parse time with `Unrecognised input`
        # anchored at the `{` — the value-eval side can't recover.
        # CSS custom properties (`--name: …;`) are exempt: their values
        # are permissive text and `{ … }` is a legitimate fragment
        # (e.g. `--anchor: () => { … }`).
        if not variable and not name.startswith('--') and self.stream.peek_kind() is Kind.LBRACE:
            self._error('Unrecognised input', self.stream.peek().index)

        value_text, important, value_start, important_ws = self._read_declaration_value()
        self.stream.match(Kind.SEMICOLON)

        return Declaration(
            index=first_index,
            name=name,
            value=Anonymous(index=value_start, value=value_text),
            important=important,
            variable=variable,
            merge=merge,
            important_origin='source' if important else '',
            important_prefix_ws=important_ws,
        )

    def _value_started_after_newline(self, start_tok: Token) -> bool:
        """True when the declaration's value text was preceded by a
        newline in the source — i.e. the user wrote `prop:\\n    val`.
        Detected from the start token's leading trivia.
        """
        return any(t.kind is Kind.WS and '\n' in t.text for t in start_tok.leading_trivia)

    def _read_declaration_value(self) -> tuple[str, bool, int, str]:
        """Read tokens until top-level `;` or `}`. Returns
        (text, important, value_start, important_prefix_ws).

        Tracks paren/bracket/brace and interpolation depth so that `(...)`,
        `[...]`, detached-ruleset `{ ... }` values, and `@{x}` / `${x}`
        interpolation closers don't end the scan prematurely. `!important`
        at top level is consumed and removed from the captured text.

        `important_prefix_ws` captures the literal whitespace string that
        sat between the value's last token and the `!important` marker
        (`' '` by default; non-trivial only when source had multi-space or
        comment trivia in between). The emitter reuses it to round-trip
        the source spacing (uikit's `position: relative  !important`
        keeps its two spaces). Empty / no-important case returns `' '`.

        Detached-ruleset special case: if the value starts with `{`, we
        stop as soon as the matching `}` closes (whether or not a trailing
        `;` follows). Less.js accepts `@var: { ... }` without a terminator.

        Strict: an unclosed `[` reaches EOF as `Expected ']'` (anchored
        at the offending `[`). Matches less.js's wording for malformed
        CSS-custom-property values where the structured parser bails.
        """
        start_tok = self.stream.peek()
        start = start_tok.index
        end = start
        paren = 0
        bracket = 0
        brace = 0
        interp = 0
        # Position of the first RBRACE/RPAREN seen while inside an
        # unclosed `[`. less.js anchors `Expected ']'` at this token —
        # the first close-bracket-or-paren that the structured parser
        # saw when looking for the missing `]`.
        first_close_while_bracket: int = -1
        important = False
        important_prefix_ws = ' '
        started_with_brace = start_tok.kind is Kind.LBRACE
        saw_open_brace = False

        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.EOF:
                break
            if paren == 0 and bracket == 0 and brace == 0 and interp == 0:
                if started_with_brace and saw_open_brace:
                    break
                if tok.kind is Kind.SEMICOLON or tok.kind is Kind.RBRACE:
                    break
                if tok.kind is Kind.IMPORTANT:
                    important = True
                    # less.js's emit-time spacing before `!important` is
                    # NOT the standard `' !important'` it claims to be —
                    # it's a side-effect of the `anonymousValue` parser
                    # fast-path (`parser.js#L1681`):
                    #
                    #   /^([^.#@$+/'"*`(;{}-]*);/
                    #
                    # When a declaration's value contains none of those
                    # characters AND ends with `;`, less.js captures the
                    # WHOLE `value!important;` as one opaque Anonymous
                    # string — `!important` rides inside the value text,
                    # never gets re-emitted via the hardcoded space.
                    # Result: `red!important;` round-trips with no space,
                    # `#444!important;` falls through to structured parse
                    # → emitted as `#444 !important` (space).
                    #
                    # We emulate that artifact by capturing the source
                    # whitespace before `!important` ONLY when the value
                    # text would have qualified for the same fast-path.
                    # The terminator check is below (after the loop).
                    value_text_so_far = self.source.text[start:end]
                    qualifies_for_anonymous_fast_path = not any(c in value_text_so_far for c in '.#@$+/\'"*`(;{}-')
                    ws_text = ''.join(t.text for t in tok.leading_trivia if t.kind is Kind.WS)
                    if qualifies_for_anonymous_fast_path and '\n' not in ws_text:
                        important_prefix_ws = ws_text
                    self.stream.consume()
                    break
            if tok.kind is Kind.LPAREN:
                paren += 1
            elif tok.kind is Kind.RPAREN:
                if bracket > 0 and first_close_while_bracket == -1:
                    first_close_while_bracket = tok.index
                paren = max(0, paren - 1)
            elif tok.kind is Kind.LBRACKET:
                bracket += 1
            elif tok.kind is Kind.RBRACKET:
                bracket = max(0, bracket - 1)
            elif tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                interp += 1
            elif tok.kind is Kind.LBRACE:
                brace += 1
                saw_open_brace = True
            elif tok.kind is Kind.RBRACE:
                if interp > 0:
                    interp -= 1
                else:
                    if bracket > 0 and first_close_while_bracket == -1:
                        first_close_while_bracket = tok.index
                    brace = max(0, brace - 1)
            end = tok.index + len(tok.text)
            self.stream.consume()

        if bracket > 0:
            anchor = first_close_while_bracket if first_close_while_bracket != -1 else start
            self._error("Expected ']'", anchor)

        # Trailing block comments + whitespace live in the terminator
        # token's leading_trivia / between `end` and `term_tok.index`.
        # We extend `end` to capture them only when:
        #   * the terminator is `;` (explicit terminator → user-written
        #     whitespace is intentional, e.g. `color: white ;`); and
        #   * no `!important` was consumed (otherwise we'd re-include
        #     `!important` text and the emitter double-adds it).
        # When terminator is `}` (implicit), we strip trailing ws —
        # less.js's behaviour.
        term_tok = self.stream.peek()
        for triv in term_tok.leading_trivia:
            if triv.kind is Kind.COMMENT_BLOCK and triv.index >= end:
                end = triv.index + len(triv.text)
                self._seen_comment_indices.add(triv.index)
        if not important and term_tok.kind is Kind.SEMICOLON and term_tok.index > end:
            end = term_tok.index
        # Second half of the anonymousValue-fast-path emulation: the
        # less.js regex `/^([^...]*);/` ALSO requires a `;` terminator.
        # `.x { red!important }` (no `;`) falls through to structured
        # parse in less.js → emitted with the canonical ` !important`
        # space. If our terminator isn't `;`, drop the preserved
        # whitespace and let `_important_suffix` use the default.
        if important and term_tok.kind is not Kind.SEMICOLON:
            important_prefix_ws = ' '
        text = self.source.text[start:end]
        # Mark `prop:\n   value` shape so the evaluator can re-emit
        # rather than verbatim-preserve. The start token's leading_trivia
        # holds the whitespace between `:` and the first token; if it
        # contains a newline, prepend `\n` to the value text — this
        # leading marker is consumed by `_starts_with_newline` and
        # cleaned by `lstrip` in the evaluator.
        if self._value_started_after_newline(start_tok):
            text = '\n' + text
        return text, important, start, important_prefix_ws
