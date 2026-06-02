"""Tier-1: drop trailing zeros from numeric literals (`1.500` → `1.5`, `2.0` → `2`)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_FRAC_RE = re.compile(r'^(\d*)\.(\d+)(?:([eE][+-]?\d+))?$')


def _trim(text: str) -> str | None:
    m = _FRAC_RE.match(text)
    if m is None:
        return None
    whole, frac, exp = m.group(1), m.group(2), m.group(3) or ''
    trimmed = frac.rstrip('0')
    if trimmed == frac:
        return None
    if trimmed:
        return f'{whole}.{trimmed}{exp}'
    return f'{whole or "0"}{exp}'


class TrailingZeroRule(Rule):
    id = 'trailing-zero'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Drop trailing zeros from numeric literals.'
    token_kinds = (Kind.NUMBER,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        new = _trim(tok.text)
        if new is None or new == tok.text:
            return
        start = tok.index
        end = start + len(tok.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'`{tok.text}` simplifies to `{new}`',
            location=ctx.location_at(start),
            span=(start, end),
            fix=Fix(replacement=new, safety='safe', description='trim trailing zeros'),
        )
