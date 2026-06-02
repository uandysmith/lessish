"""Tier-1: numeric literals like `.5` should be written `0.5`."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class DecimalLeadingZeroRule(Rule):
    id = 'decimal-leading-zero'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Decimals smaller than 1 should have a leading zero.'
    token_kinds = (Kind.NUMBER,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if not tok.text.startswith('.'):
            return
        start = tok.index
        end = start + len(tok.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'`{tok.text}` should be `0{tok.text}`',
            location=ctx.location_at(start),
            span=(start, end),
            fix=Fix(replacement='0' + tok.text, safety='safe', description='prepend 0'),
        )
