"""Tier-0: collapse 3+ consecutive blank lines into at most `max`."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .._findings import Finding, Fix
from ._base import LintContext, Rule


class NoMultipleBlankLinesRule(Rule):
    id = 'no-multiple-blank-lines'
    severity = 'info'
    fix_tier = 'safe'
    description = 'Avoid more than `max` consecutive blank lines.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        max_blanks = int(ctx.options.get('max', 2))
        text = ctx.text
        # Match (max_blanks + 1) or more consecutive blank lines.
        # A blank line is `\n` followed by optional whitespace + `\n`.
        # We rewrite the inner stretch to exactly `max_blanks` blank lines.
        pat = re.compile(r'(\n)((?:[ \t]*\n){' + str(max_blanks + 1) + r',})')
        for m in pat.finditer(text):
            blanks = m.group(2)
            start = m.start(2)
            end = m.end(2)
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'more than {max_blanks} consecutive blank lines',
                location=ctx.location_at(start),
                span=(start, end),
                fix=Fix(
                    replacement='\n' * max_blanks,
                    safety='safe',
                    description=f'collapse to {max_blanks} blank line(s)',
                ),
            )
            _ = blanks
