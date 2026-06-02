"""Tier-0: flag 2+ spaces between `{` and the next token on the same line."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class NoSpaceAfterLBraceRule(Rule):
    id = 'no-space-after-lbrace'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'No extra spaces after `{` on the same line.'
    token_kinds = (Kind.LBRACE,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        toks = ctx.tokens
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        start = tok.index + 1
        end = nxt.index
        between = ctx.text[start:end]
        if '\n' in between:
            return
        if between.count(' ') >= 2 and between.strip() == '':
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'{len(between)} spaces after `{{` (use 1)',
                location=ctx.location_at(start),
                span=(start, end),
                fix=Fix(replacement=' ', safety='safe', description='collapse to one space'),
            )
