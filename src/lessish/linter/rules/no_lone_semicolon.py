"""Tier-0: a `;` directly before `}` (i.e. trailing in an empty block
position) adds nothing — drop it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class NoLoneSemicolonRule(Rule):
    id = 'no-trailing-semicolon-in-empty-block'
    severity = 'info'
    fix_tier = 'safe'
    description = 'Empty `{ ; }` block — drop the lone semicolon.'
    token_kinds = (Kind.SEMICOLON,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        toks = ctx.tokens
        if idx == 0 or idx + 1 >= len(toks):
            return
        prev = toks[idx - 1]
        nxt = toks[idx + 1]
        if prev.kind is Kind.LBRACE and nxt.kind is Kind.RBRACE:
            start = tok.index
            end = start + 1
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='lone `;` inside otherwise-empty block',
                location=ctx.location_at(start),
                span=(start, end),
                fix=Fix(replacement='', safety='safe', description='drop `;`'),
            )
