"""Parser utilities mixin: low-level token-reading + error raising.

Leaf mixin — these methods are called from every other mixin but
themselves call nothing in this package outside `self.stream` /
`self.source`. Place new utility methods here only when they fit
the "small, generic, called from many places" shape.

`_MEDIA_LIKE_ATRULES` lives here because the only consumer is
`_read_until_block_or_end`'s prelude-error path; moving it
elsewhere would force the leaf mixin to import upward.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

from ..errors import ParseError, UnsupportedFeatureError
from ..lexer import Kind, Token
from ._state import MAX_PARSE_DEPTH, _ParserState

# At-rules whose parse-time failures less.js reports with the
# `media definitions require block statements after any features`
# wording — they share a single grammar in less.js's media-query
# state machine.
_MEDIA_LIKE_ATRULES: frozenset[str] = frozenset({'@media'})


class _UtilsMethods(_ParserState):
    """Token-reading helpers and error-raising methods.

    Every other mixin depends on these. The mixin itself depends on
    nothing in the parser package — it's a leaf in the dependency
    graph.
    """

    def _has_ws_before(self, tok: Token) -> bool:
        return any(t.kind is Kind.WS for t in tok.leading_trivia)

    @contextmanager
    def _descend(self) -> Iterator[None]:
        """Bound recursive-descent nesting.

        Wraps the two recursion gates — block nesting (`_primary`) and
        value/paren nesting (`_parse_primary`). Each level bumps
        `self._depth`; exceeding `MAX_PARSE_DEPTH` raises a clean
        `ParseError` anchored at the current token instead of letting
        the call stack run into Python's `RecursionError` (which is a
        `RuntimeError`, outside the `LessError` hierarchy). The limit
        sits well below the interpreter's stack budget yet far above
        any real stylesheet's nesting. The public-API methods keep a
        `RecursionError` backstop for any path this guard doesn't gate.
        """
        self._depth += 1
        if self._depth > MAX_PARSE_DEPTH:
            self._depth -= 1
            self._error('maximum nesting depth exceeded', self.stream.peek().index)
        try:
            yield
        finally:
            self._depth -= 1

    def _error(self, msg: str, index: int) -> NoReturn:
        raise ParseError(
            msg,
            location=self.source.location_at(index),
            snippet=self.source.snippet_around(index),
        )

    def _reject_backtick(self, tok: Token) -> NoReturn:
        """Raise `UnsupportedFeatureError` for a JavaScript backtick
        expression. The lexer tokenizes backticks normally (so a
        subclass / external tooling can intercept them); this default
        parser surfaces the unsupported-feature diagnostic the moment
        the token reaches a syntactic position.
        """
        raise UnsupportedFeatureError(
            f'JavaScript backtick expression {tok.text} is not supported by lessish',
            location=self.source.location_at(tok.index),
            snippet=self.source.snippet_around(tok.index),
        )

    def _read_interpolation(self) -> str:
        """Read an `@{...}` or `${...}` interpolation as raw text.

        Permissive about contents: less.js allows non-IDENT variable names
        like `@{-}`, nested interpolation like `@{box-@{suffix}}`, etc.
        We track interp depth to find the matching `}` and slice the
        original source.
        """
        start_tok = self.stream.peek()
        if start_tok.kind not in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
            self._error(
                f'expected @{{ or ${{ but got {start_tok.kind.name}',
                start_tok.index,
            )
        start = start_tok.index
        self.stream.consume()
        depth = 1
        end = start_tok.index + len(start_tok.text)
        while depth > 0:
            tok = self.stream.peek()
            if tok.kind is Kind.EOF:
                self._error("unmatched '@{' / '${'", start_tok.index)
            if tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                depth += 1
            elif tok.kind is Kind.RBRACE:
                depth -= 1
            end = tok.index + len(tok.text)
            self.stream.consume()
        return self.source.text[start:end]

    def _read_balanced(self, open_kind: Kind, close_kind: Kind) -> str:
        start_tok = self.stream.peek()
        if start_tok.kind is not open_kind:
            self._error(
                f'expected {open_kind.name} but got {start_tok.kind.name}',
                start_tok.index,
            )
        start = start_tok.index
        self.stream.consume()
        depth = 1
        end = start + len(start_tok.text)
        while depth > 0:
            tok = self.stream.peek()
            if tok.kind is Kind.EOF:
                self._error(f'unmatched {open_kind.name}', start_tok.index)
            if tok.kind is open_kind:
                depth += 1
            elif tok.kind is close_kind:
                depth -= 1
            end = tok.index + len(tok.text)
            self.stream.consume()
        return self.source.text[start:end]

    def _read_until_block_or_end(
        self,
        *,
        stop_kinds: tuple[Kind, ...],
        at_name: str = '',
    ) -> tuple[str, Token]:
        """Read tokens until a top-level stop kind. Returns (text, stop_token).

        Tracks paren/bracket/interp depth so the `}` of `@{x}` / `${x}`
        interpolation doesn't terminate a prelude prematurely.

        Block comments lexed as trivia are still in the source between
        tokens, so we slice up to the terminator's `index` (not the end
        of the last consumed structural token) to capture them.

        Strict paren-depth checks (only when `at_name` is set —
        relied on by `_at_rule` for the at-rule prelude path):
        * LBRACE inside a media-feature `(name: value)` paren →
          `Missing closing ')'` at the `{`. The body-open can't
          legitimately fall inside an unclosed feature paren.
          A function-call paren (`url(`, …) is *not* affected — its
          arg text may contain `{` legitimately, and the imbalance
          surfaces as EOF instead.
        * RPAREN at depth 0 (extra `)`) → media-class at-rules get the
          `media definitions require block statements after any
          features` wording; other at-rules silently clamp the depth
          (permissive prelude parsing).
        * EOF with paren-depth > 0 → `expected ')' got ''` at the EOF
          position.

        `paren_stack` entries: True = function-call paren (`name(`),
        False = bare/feature paren.
        """
        start_tok = self.stream.peek()
        start = start_tok.index
        end = start
        paren_stack: list[bool] = []
        bracket = 0
        interp = 0
        prev_tok: Token | None = None
        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.EOF:
                if at_name and paren_stack:
                    err = ParseError("expected ')' got ''")
                    err._less_js_name = 'SyntaxError'
                    err._base_index = tok.index
                    err.location = self.source.location_at(tok.index)
                    err.snippet = self.source.snippet_around(tok.index)
                    raise err
                return self.source.text[start:end].rstrip(), tok
            if not paren_stack and bracket == 0 and interp == 0 and tok.kind in stop_kinds:
                # Use the terminator's leading edge so any comments /
                # whitespace sitting between the last consumed token
                # and the terminator are captured in the prelude text.
                return self.source.text[start : tok.index].rstrip(), tok
            if tok.kind is Kind.LPAREN:
                is_call = prev_tok is not None and prev_tok.kind is Kind.IDENT and not self._has_ws_before(tok)
                paren_stack.append(is_call)
            elif tok.kind is Kind.RPAREN:
                if at_name and not paren_stack and bracket == 0 and interp == 0:
                    if at_name in _MEDIA_LIKE_ATRULES:
                        err = ParseError('media definitions require block statements after any features')
                        err._less_js_name = 'SyntaxError'
                        err._base_index = tok.index
                        err.location = self.source.location_at(tok.index)
                        err.snippet = self.source.snippet_around(tok.index)
                        raise err
                if paren_stack:
                    paren_stack.pop()
            elif tok.kind is Kind.LBRACE:
                # Inside a feature paren (innermost paren not a
                # function call) the `{` is a parse error. Inside a
                # function-call paren we let the imbalance surface at
                # EOF — the `{` could legitimately be part of the
                # call arg text.
                if at_name and paren_stack and not paren_stack[-1] and bracket == 0 and interp == 0:
                    err = ParseError("Missing closing ')'")
                    err._less_js_name = 'ParseError'
                    err._base_index = tok.index
                    err.location = self.source.location_at(tok.index)
                    err.snippet = self.source.snippet_around(tok.index)
                    raise err
            elif tok.kind is Kind.LBRACKET:
                bracket += 1
            elif tok.kind is Kind.RBRACKET:
                bracket = max(0, bracket - 1)
            elif tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
                interp += 1
            elif tok.kind is Kind.RBRACE and interp > 0:
                interp -= 1
            end = tok.index + len(tok.text)
            self.stream.consume()
            prev_tok = tok
