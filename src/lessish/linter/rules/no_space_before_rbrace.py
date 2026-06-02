"""Tier-0: flag 2+ spaces between previous token and `}` on the same line."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class NoSpaceBeforeRBraceRule(Rule):
    id = 'no-space-before-rbrace'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'No extra spaces before `}` on the same line.'
    token_kinds = (Kind.RBRACE,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if idx == 0:
            return
        prev = ctx.tokens[idx - 1]
        prev_end = prev.index + len(prev.text)
        between = ctx.text[prev_end : tok.index]
        if '\n' in between:
            return
        if between.count(' ') >= 2 and between.strip() == '':
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'{len(between)} spaces before `}}` (use 1)',
                location=ctx.location_at(prev_end),
                span=(prev_end, tok.index),
                fix=Fix(replacement=' ', safety='safe', description='collapse to one space'),
            )
