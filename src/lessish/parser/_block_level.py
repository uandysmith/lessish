"""Block-level parser mixin.

The top of the recursive-descent dispatcher: `parse` (entry),
`_primary` (rule-list loop), `_block_item` (classifier),
`_block_lookahead` (peek-and-classify), `_at_rule`
(`@name <prelude> { body }`), `_harvest_block_comments`, and
`_unexpected_at_top`.

Hosts the `_AT_RULES_WITH_PSEUDO_PRELUDE` constant (only consumer).
"""

from __future__ import annotations

from typing import NoReturn

from ..ast_nodes import AtRule, Comment, Node, Ruleset
from ..errors import ParseError
from ..lexer import Kind, Token
from ._state import _ParserState
from ._utils import _MEDIA_LIKE_ATRULES
from .helpers import _strip_block_comments

_AT_RULES_WITH_PSEUDO_PRELUDE: frozenset[str] = frozenset(
    # `@page :left { … }` — `:left` is a page-pseudo, not a value, so
    # the surface form `@name :foo { … }` for these names always parses
    # as an at-rule rather than a `@var: value;` declaration.
    {'@page'}
)


class _BlockLevelMethods(_ParserState):
    """Top-level dispatcher + at-rule consumer."""

    def parse(self) -> Ruleset:
        rules = self._primary()
        if not self.stream.at_end():
            tok = self.stream.peek()
            self._unexpected_at_top(tok)
        return Ruleset(index=0, selectors=[], rules=rules, root=True)

    def _unexpected_at_top(self, tok: Token) -> NoReturn:
        """Report an unexpected token at root / block-item position.

        A stray `}` is the common case (one closing brace too many) —
        less.js's wording is `Unrecognised input. Possibly missing
        opening '{'`. Other surprise tokens get the generic
        `unexpected <kind>` wording.
        """
        if tok.kind is Kind.RBRACE:
            self._error("Unrecognised input. Possibly missing opening '{'", tok.index)
        self._error(f'unexpected {tok.kind.name} ({tok.text!r})', tok.index)

    def _primary(self) -> list[Node]:
        with self._descend():
            rules: list[Node] = []
            while True:
                tok = self.stream.peek()
                # Harvest block-form comments from the upcoming token's
                # leading trivia. They become `Comment` AST nodes inserted
                # at this point in the rule list so emit can preserve them
                # at their source position. Line comments (`//`) remain
                # trivia (they're silent in CSS output).
                for c in self._harvest_block_comments(tok):
                    rules.append(c)
                if tok.kind is Kind.EOF or tok.kind is Kind.RBRACE:
                    break
                if tok.kind is Kind.SEMICOLON:
                    self.stream.consume()
                    continue
                node = self._block_item()
                if node is None:
                    self._error(f'unexpected {tok.kind.name} ({tok.text!r})', tok.index)
                rules.append(node)
            return rules

    def _harvest_block_comments(self, tok: Token) -> list[Comment]:
        """Convert any `COMMENT_BLOCK` tokens in `tok.leading_trivia` into
        `Comment` AST nodes. Called at block-item boundaries so block
        comments survive to the output. The trivia field is *consumed*
        — subsequent peeks return the same trivia, but `_harvest_*`
        callers shouldn't double-collect. We avoid that by only calling
        it once per loop iteration at known positions.
        """
        # Use the leading_trivia as a stable identity key — comments
        # tokens have stable indices, so we use those to dedupe.
        if not tok.leading_trivia:
            return []
        seen = self._seen_comment_indices
        out: list[Comment] = []
        for t in tok.leading_trivia:
            if t.kind is Kind.COMMENT_BLOCK and t.index not in seen:
                seen.add(t.index)
                out.append(Comment(index=t.index, text=t.text, silent=False))
        return out

    def _block_item(self) -> Node | None:
        tok = self.stream.peek()

        # @-rule vs. variable declaration: distinguish by whether `:` follows.
        if tok.kind in (Kind.AT_NAME, Kind.AT_AT_NAME):
            if self.stream.peek_kind(1) is Kind.COLON:
                # less.js rejects `@@name:` decls — `@@x` is only valid
                # in *reference* position (RHS), never as a declaration LHS.
                if tok.kind is Kind.AT_AT_NAME:
                    self._error('Unrecognised input', tok.index)
                # `@page :left { … }` parses as an at-rule, not a
                # variable declaration: the leading `:` is the start of
                # the prelude's pseudo-class. Same for other known
                # at-rule names that legitimately accept a pseudo-class
                # in their prelude. Without this check `@page :left`
                # would tokenize as `@page: left { margin: … }`.
                if tok.text.lower() in _AT_RULES_WITH_PSEUDO_PRELUDE:
                    return self._at_rule()
                return self._declaration()
            return self._at_rule()

        kind = self._block_lookahead()
        if kind == 'declaration':
            return self._declaration()
        if kind == 'ruleset':
            return self._ruleset()
        if kind == 'statement':
            return self._statement()
        return None

    def _block_lookahead(self) -> str:
        """Scan tokens ahead to classify the next block item.

        Returns 'ruleset', 'declaration', or 'statement'. Tracks paren,
        bracket, and interpolation depth so that COLON inside `[a:b]` or
        `(a:b)` doesn't fool the classifier and `}` closing a `@{x}` /
        `${x}` interpolation isn't mistaken for a block terminator.

        At top-level depth:
          `{` wins as ruleset (nested-ruleset start) — except when a
            CSS custom property `--foo:` has already taken the colon,
            in which case `{` is part of the permissive value
            (`--this: () => { … };` and friends);
          `;` or `}` ends the scan (declaration if a `:`/`+:`/`+_:` was
            seen, otherwise statement);
          `:` / `+:` / `+_:` flips the declaration flag.
        """
        snap = self.stream.save()
        paren = 0
        bracket = 0
        interp = 0
        saw_colon = False
        is_custom_property = False
        # Tracks whether the most recent (or any in-flight) `(...)` group
        # contained a top-level detached-ruleset body (`{...}`). When set,
        # closing the outer `)` makes this item a statement — `each(list,
        # { body })` and the like are call-statements, not ruleset heads.
        saw_dr_arg_in_call = False
        inner_brace = 0
        # True when the *only* token consumed before the first `:` is a
        # plain `IDENT` (or `STAR`). Used to disambiguate `prop: { … };`
        # (declaration with a detached-ruleset value — invalid for
        # non-variables, but still parsed by `_declaration` so the
        # proper error fires) from `.x:hover { … }` (a CSS
        # pseudo-class selector that must remain a ruleset).
        simple_decl_head = False
        tokens_before_colon = 0
        first_head_kind: Kind | None = None
        try:
            while True:
                tok = self.stream.peek()
                if tok.kind is Kind.EOF:
                    return 'declaration' if saw_colon else 'statement'
                if paren == 0 and bracket == 0 and interp == 0:
                    if tok.kind is Kind.LBRACE:
                        if is_custom_property:
                            # Skip the entire `{ … }` block as part of
                            # the permissive value text.
                            self.stream.consume()
                            depth = 1
                            while depth > 0:
                                t2 = self.stream.peek()
                                if t2.kind is Kind.EOF:
                                    return 'declaration'
                                if t2.kind is Kind.LBRACE:
                                    depth += 1
                                elif t2.kind is Kind.RBRACE:
                                    depth -= 1
                                self.stream.consume()
                            continue
                        if simple_decl_head:
                            # `prop: { … }` shape: the leading `IDENT :`
                            # is a declaration head even though `{`
                            # normally starts a ruleset body.
                            # `_declaration` rejects the DR-literal value.
                            return 'declaration'
                        return 'ruleset'
                    if tok.kind is Kind.SEMICOLON or tok.kind is Kind.RBRACE:
                        return 'declaration' if saw_colon else 'statement'
                    if tok.kind in (Kind.COLON, Kind.PLUS_COLON, Kind.PLUS_UNDER_COLON):
                        if (
                            not saw_colon
                            and tokens_before_colon == 1
                            and first_head_kind in (Kind.IDENT, Kind.STAR)
                            and self.stream.peek_kind(1) is Kind.LBRACE
                        ):
                            # `name: { … }` — a declaration head with a
                            # DR-literal value. (Pseudo-classes look
                            # like `:hover` — IDENT after the COLON —
                            # and stay as 'ruleset'.)
                            simple_decl_head = True
                        saw_colon = True
                    elif not saw_colon:
                        if tokens_before_colon == 0:
                            first_head_kind = tok.kind
                        tokens_before_colon += 1
                if tok.kind is Kind.LPAREN:
                    paren += 1
                elif tok.kind is Kind.RPAREN:
                    paren = max(0, paren - 1)
                    if paren == 0 and saw_dr_arg_in_call and not saw_colon:
                        # A call whose args contained a `{…}` (DR) and
                        # whose `)` is *not* immediately followed by
                        # `{`/`when` is a statement — `each(list, { body
                        # })` followed by another rule, vs. a mixin
                        # definition `.mixin(@a: {}) { body }` where the
                        # trailing `{` opens the body.
                        nxt = self.stream.peek(1)
                        if nxt.kind is not Kind.LBRACE:
                            if not (nxt.kind is Kind.IDENT and nxt.text == 'when'):
                                return 'statement'
                elif tok.kind is Kind.LBRACKET:
                    bracket += 1
                elif tok.kind is Kind.RBRACKET:
                    bracket = max(0, bracket - 1)
                elif tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                    interp += 1
                elif tok.kind is Kind.RBRACE and interp > 0:
                    interp -= 1
                elif tok.kind is Kind.LBRACE and paren > 0 and interp == 0:
                    inner_brace += 1
                    saw_dr_arg_in_call = True
                elif tok.kind is Kind.RBRACE and paren > 0 and interp == 0 and inner_brace > 0:
                    inner_brace -= 1
                # Detect a `--foo` CSS custom property head before
                # consuming, so the LBRACE handling above can treat the
                # value permissively.
                if not saw_colon and not is_custom_property and tok.kind is Kind.IDENT and tok.text.startswith('--'):
                    is_custom_property = True
                self.stream.consume()
        finally:
            self.stream.restore(snap)

    def _at_rule(self) -> AtRule:
        name_tok = self.stream.consume()  # AT_NAME / AT_AT_NAME
        # RBRACE is in the stop set too: a statement-form @-rule (`@x();`
        # missing the `;`) at the end of an enclosing block must not eat
        # the enclosing block's closing `}`.
        prelude_text, _ = self._read_until_block_or_end(
            stop_kinds=(Kind.LBRACE, Kind.SEMICOLON, Kind.RBRACE),
            at_name=name_tok.text,
        )
        # Extract block comments from the prelude. less.js drops them
        # from the prelude text and — for at-rules with a body —
        # prepends them as Comment nodes inside the body. For body-less
        # at-rules (`@import`, `@charset`) the comments end up next to
        # the at-rule at root level (carried as `trailing_comments`).
        cleaned_prelude, prelude_comments = _strip_block_comments(prelude_text)
        body: list[Node] | None = None
        trailing: list[Comment] = []
        if self.stream.peek_kind() is Kind.LBRACE:
            self.stream.consume()
            body = self._primary()
            # less.js wording for `@media` (and `@supports`) when the
            # closing `}` is missing is escalated to a SyntaxError —
            # `media definitions require block statements after any
            # features`. Other at-rules keep the generic message.
            if self.stream.peek_kind() is not Kind.RBRACE and name_tok.text in _MEDIA_LIKE_ATRULES:
                anchor = self.stream.peek().index
                err = ParseError('media definitions require block statements after any features')
                err._less_js_name = 'SyntaxError'
                err._base_index = anchor
                err.location = self.source.location_at(anchor)
                err.snippet = self.source.snippet_around(anchor)
                raise err
            self.stream.expect(Kind.RBRACE, "missing '}' for @-rule body")
            if prelude_comments:
                # Tag hoisted prelude comments so the emit-time empty-body
                # filter can ignore them — `@media all and/*! */(x:y) {}`
                # is still an empty rule despite the bang-comment landing
                # in the body.
                for c in prelude_comments:
                    c._from_prelude = True
                body = [*prelude_comments, *body]
        else:
            # `@media` (and friends) without a body — less.js requires
            # the block-form. An empty / body-less media at-rule with
            # no SEMICOLON is anchored at the position past the
            # at-name.
            if (
                name_tok.text in _MEDIA_LIKE_ATRULES
                and not cleaned_prelude.strip()
                and self.stream.peek_kind() is not Kind.SEMICOLON
            ):
                anchor = name_tok.index + len(name_tok.text)
                err = ParseError('media definitions require block statements after any features')
                err._less_js_name = 'SyntaxError'
                err._base_index = anchor
                err.location = self.source.location_at(anchor)
                err.snippet = self.source.snippet_around(anchor)
                raise err
            # `@import "..."` requires a trailing `;`. Less.js's wording
            # is `missing semi-colon or unrecognised media features on
            # import`, anchored at the at-rule's start column.
            if name_tok.text == '@import' and cleaned_prelude.strip() and self.stream.peek_kind() is not Kind.SEMICOLON:
                err = ParseError('missing semi-colon or unrecognised media features on import')
                err._less_js_name = 'SyntaxError'
                err._base_index = name_tok.index
                err.location = self.source.location_at(name_tok.index)
                err.snippet = self.source.snippet_around(name_tok.index)
                raise err
            self.stream.match(Kind.SEMICOLON)
            trailing = prelude_comments
        return AtRule(
            index=name_tok.index,
            name=name_tok.text,
            prelude=cleaned_prelude.strip(),
            body=body,
            trailing_comments=trailing,
        )
