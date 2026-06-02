"""Statement-form mixin-call parser mixin.

Single method, `_statement`, that handles the
`.name(args);` / `.name(args)!important;` / `&:extend(...)` /
`@varcall();` shapes at block-item position. The actual structural
classification (mixin call vs extend vs variable call) happens in
`mixins.transform_mixins` after parsing; this method only captures
the raw text up to the next `;` / `}` boundary, tracking paren /
bracket / interpolation depth.

Hosts `_SIMPLE_IDENT_RE` (the only regex used here) so it lives
next to its sole consumer.
"""

from __future__ import annotations

import re

from ..ast_nodes import MixinCallStatement
from ..lexer import Kind
from ._state import _ParserState

_SIMPLE_IDENT_RE = re.compile(r'[a-zA-Z_][\w-]*')


class _StatementsMethods(_ParserState):
    """Statement-form mixin-call capture."""

    def _statement(self) -> MixinCallStatement:
        start = self.stream.peek().index
        end = start
        paren = 0
        bracket = 0
        interp = 0
        # Mirror `_block_lookahead`: if we close a top-level `(...)`
        # whose body contained a `{...}` (a DR argument), the call
        # statement ends at that `)` even without a trailing `;` — the
        # next token starts a new block item.
        saw_dr_arg_in_call = False
        inner_brace = 0
        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.EOF:
                break
            if paren == 0 and bracket == 0 and interp == 0:
                if tok.kind is Kind.SEMICOLON or tok.kind is Kind.RBRACE:
                    break
            if tok.kind is Kind.LPAREN:
                paren += 1
            elif tok.kind is Kind.RPAREN:
                paren = max(0, paren - 1)
                if paren == 0 and saw_dr_arg_in_call:
                    nxt = self.stream.peek(1)
                    if nxt.kind is not Kind.LBRACE and not (nxt.kind is Kind.IDENT and nxt.text == 'when'):
                        end = tok.index + len(tok.text)
                        self.stream.consume()
                        break
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
            end = tok.index + len(tok.text)
            self.stream.consume()
        # A "statement" whose entire body is a single bare identifier
        # isn't a mixin call. less.js rejects it, anchored at the
        # column immediately after the identifier. Wording:
        #   * `nonsense;` → `Unrecognised input`.
        #   * `x` (EOF) / `x }` → `Unrecognised input. Possibly
        #                          missing something`.
        had_semi = self.stream.peek_kind() is Kind.SEMICOLON
        self.stream.match(Kind.SEMICOLON)
        text = self.source.text[start:end].strip()
        if text and _SIMPLE_IDENT_RE.fullmatch(text):
            msg = 'Unrecognised input' if had_semi else 'Unrecognised input. Possibly missing something'
            self._error(msg, start + len(text))
        return MixinCallStatement(index=start, text=text)
