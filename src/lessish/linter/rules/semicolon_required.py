"""Tier-0: declarations should end with `;` even when they're the last
declaration before `}`.

less.js accepts a missing trailing semicolon, but it's a footgun when
someone later appends another declaration. Detection: scan the token
stream for declarations (a name-position IDENT followed by COLON) and
check whether the value run terminates with SEMICOLON before `}` or
EOF.

Tracks a stack of opener kinds so RBRACE only triggers when its
opener was `{`, not `@{` / `${` interpolation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_INNOCENT = {Kind.SEMICOLON, Kind.LBRACE, Kind.RBRACE, Kind.COMMENT_BLOCK, Kind.COMMENT_LINE}


@dataclass
class _State:
    opener_stack: list[Kind] = field(default_factory=list)


class SemicolonRequiredRule(Rule):
    id = 'semicolon-required'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Declarations should end with `;` even when last in block.'
    token_kinds = (Kind.LBRACE, Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN, Kind.RBRACE)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind in (Kind.LBRACE, Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
            state.opener_stack.append(tok.kind)
            return
        # RBRACE
        opener = state.opener_stack.pop() if state.opener_stack else Kind.LBRACE
        if opener is not Kind.LBRACE:
            return
        if idx == 0:
            return
        toks = ctx.tokens
        prev = toks[idx - 1]
        if prev.kind in _INNOCENT:
            return
        if not _saw_colon_since_separator(toks, idx - 1):
            return
        insert_at = prev.index + len(prev.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='missing `;` at end of declaration',
            location=ctx.location_at(insert_at),
            span=(insert_at, insert_at),
            fix=Fix(replacement=';', safety='safe', description='append `;`'),
        )


def _saw_colon_since_separator(toks: list[Token], end_index: int) -> bool:
    i = end_index
    while i >= 0:
        k = toks[i].kind
        if k in (Kind.SEMICOLON, Kind.LBRACE, Kind.RBRACE):
            return False
        if k is Kind.COLON:
            return True
        i -= 1
    return False
