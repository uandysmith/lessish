"""Tier-0: each declaration / statement inside a block sits on its
own line.

Detects `;` followed by a non-RBRACE token on the same line inside a
block and inserts `\\n + indent`. Combined with
`block-opening-brace-newline-after` and the existing closing-brace
rules, this forms the multi-line-block formatter.

Top-level statements (e.g. `@import ...;`) outside any block do NOT
fire — those are joined-or-broken via blank-line rules instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule
from ._comments import comments_in_gap


@dataclass
class _State:
    # Stack of opener kinds. Only LBRACE matters for block detection;
    # other openers (LPAREN, LBRACKET, INTERP_OPEN, …) are tracked
    # because `;` inside any of them is NOT a statement separator
    # (data-URIs, expression lists, etc.).
    stack: list[Kind] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.stack is None:
            self.stack = []


_OPENERS = (
    Kind.LBRACE,
    Kind.LPAREN,
    Kind.LBRACKET,
    Kind.INTERP_OPEN,
    Kind.DOLLAR_INTERP_OPEN,
)
_CLOSERS = (Kind.RBRACE, Kind.RPAREN, Kind.RBRACKET)


class SemicolonNewlineAfterRule(Rule):
    id = 'semicolon-newline-after'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Each statement should be on its own line.'
    token_kinds = (*_OPENERS, *_CLOSERS, Kind.SEMICOLON)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind in _OPENERS:
            state.stack.append(tok.kind)
            return
        if tok.kind in _CLOSERS:
            if state.stack:
                state.stack.pop()
            return
        # SEMICOLON
        # Only treat as a statement separator when the innermost open
        # container is `{` (a block) — `;` inside `(...)`, `[...]`,
        # `@{...}` is not a statement boundary.
        if not state.stack or state.stack[-1] is not Kind.LBRACE:
            return
        depth = sum(1 for k in state.stack if k is Kind.LBRACE)
        toks = ctx.tokens
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        if nxt.kind is Kind.RBRACE:
            return
        between = ctx.text[tok.index + 1 : nxt.index]
        if '\n' in between:
            return
        width = int(ctx.options.get('width', 2))
        indent = ' ' * (width * depth)
        # A comment between this `;` and the next statement (e.g.
        # `color: red; /* note */ width: 2px;`) trails the just-ended
        # declaration. Keep it on that declaration's line so a line-scoped
        # directive still governs it, then break the next statement off.
        comments = comments_in_gap(between)
        replacement = ''.join(' ' + c for c in comments) + '\n' + indent
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='statement should be on its own line',
            location=ctx.location_at(tok.index + 1),
            span=(tok.index + 1, nxt.index),
            fix=Fix(
                replacement=replacement,
                safety='safe',
                description='insert newline + indent',
            ),
        )
