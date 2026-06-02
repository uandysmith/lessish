"""Tier-0: flag spaces / tabs at end of a line."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .._findings import Finding, Fix
from ._base import LintContext, Rule

_TRAILING_RE = re.compile(r'[ \t]+(?=\n)|[ \t]+\Z')


class TrailingWhitespaceRule(Rule):
    id = 'trailing-whitespace'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Lines should not end with whitespace.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        text = ctx.text
        for m in _TRAILING_RE.finditer(text):
            start, end = m.span()
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='trailing whitespace',
                location=ctx.location_at(start),
                span=(start, end),
                fix=Fix(replacement='', safety='safe', description='strip trailing whitespace'),
            )
