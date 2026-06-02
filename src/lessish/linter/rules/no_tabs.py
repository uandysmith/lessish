"""Tier-0: no tab characters in indentation.

Option `width` (default 2) controls how many spaces a tab expands to.
"""

from __future__ import annotations

from collections.abc import Iterable

from .._findings import Finding, Fix
from ._base import LintContext, Rule


class NoTabsRule(Rule):
    id = 'no-tabs'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Tab character in indentation; use spaces.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        text = ctx.text
        width = int(ctx.options.get('width', 2))
        replacement = ' ' * width
        line_start = 0
        i = 0
        n = len(text)
        while i < n:
            if text[i] == '\n':
                line_start = i + 1
                i += 1
                continue
            if i == line_start and text[i] in (' ', '\t'):
                # Walk the indent run; emit one finding per tab run.
                run_start = i
                while i < n and text[i] in (' ', '\t'):
                    i += 1
                indent = text[run_start:i]
                if '\t' in indent:
                    yield Finding(
                        rule_id=self.id,
                        severity=self.severity,
                        message='tab character in indentation',
                        location=ctx.location_at(run_start),
                        span=(run_start, i),
                        fix=Fix(
                            replacement=indent.replace('\t', replacement),
                            safety='safe',
                            description='replace tabs with spaces',
                        ),
                    )
                continue
            i += 1
