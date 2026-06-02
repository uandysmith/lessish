"""Tier-0: enforce single OR double quotes consistently.

Off by default — opt in via `[tool.lessish.lint.rules.quote-pref]`.

Options:
  prefer = 'single' | 'double'   (default 'single')
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class QuotePrefRule(Rule):
    id = 'quote-pref'
    severity = 'info'
    fix_tier = 'safe'
    description = 'String literals should use the preferred quote style.'
    token_kinds = (Kind.STRING,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if not ctx.options.get('enabled', False):
            return
        prefer = ctx.options.get('prefer', 'single')
        target = "'" if prefer == 'single' else '"'
        opposite = '"' if prefer == 'single' else "'"
        if not tok.text.startswith(opposite):
            return
        inner = tok.text[1:-1]
        if target in inner:
            return
        rewritten = target + inner + target
        start = tok.index
        end = start + len(tok.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'use {prefer} quotes',
            location=ctx.location_at(start),
            span=(start, end),
            fix=Fix(replacement=rewritten, safety='safe', description=f'swap to {prefer} quotes'),
        )
