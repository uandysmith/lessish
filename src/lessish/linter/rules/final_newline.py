"""Tier-0: file should end with exactly one newline."""

from __future__ import annotations

from collections.abc import Iterable

from .._findings import Finding, Fix
from ._base import LintContext, Rule


class FinalNewlineRule(Rule):
    id = 'final-newline'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'File must end with exactly one newline.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        text = ctx.text
        if not text:
            return
        if not text.endswith('\n'):
            n = len(text)
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='file does not end with a newline',
                location=ctx.location_at(max(0, n - 1)),
                span=(n, n),
                fix=Fix(replacement='\n', safety='safe', description='append newline'),
            )
            return
        # Trailing newlines beyond one.
        i = len(text)
        while i > 0 and text[i - 1] == '\n':
            i -= 1
        extra = len(text) - i - 1
        if extra > 0:
            start = i + 1
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='file ends with multiple newlines',
                location=ctx.location_at(start),
                span=(start, len(text)),
                fix=Fix(replacement='', safety='safe', description='collapse trailing newlines'),
            )
