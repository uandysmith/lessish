"""Tier-0: a file's indent must use either tabs OR spaces, not both."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .._findings import Finding, Fix
from ._base import LintContext, Rule


class IndentConsistencyRule(Rule):
    id = 'indent-consistency'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Indentation must use tabs OR spaces, not both.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        text = ctx.text
        prefer = ctx.options.get('prefer', 'spaces')
        width = int(ctx.options.get('width', 2))
        n = len(text)

        had_tab = False
        had_space = False
        for indent_start, indent_end in _line_indents(text):
            indent = text[indent_start:indent_end]
            if '\t' in indent:
                had_tab = True
            if ' ' in indent:
                had_space = True

        if not (had_tab and had_space):
            return

        tab_replacement = ' ' * width if prefer == 'spaces' else '\t'
        for indent_start, indent_end in _line_indents(text):
            indent = text[indent_start:indent_end]
            if prefer == 'spaces' and '\t' in indent:
                yield Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    message='mix of tab/space indent; normalising to spaces',
                    location=ctx.location_at(indent_start),
                    span=(indent_start, indent_end),
                    fix=Fix(
                        replacement=indent.replace('\t', tab_replacement),
                        safety='safe',
                        description='normalise indent',
                    ),
                )
            elif prefer == 'tabs' and ' ' in indent:
                yield Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    message='mix of tab/space indent; normalising to tabs',
                    location=ctx.location_at(indent_start),
                    span=(indent_start, indent_end),
                    fix=Fix(
                        replacement=indent.replace(' ' * width, '\t').replace(' ', ''),
                        safety='safe',
                        description='normalise indent',
                    ),
                )
        _ = n


def _line_indents(text: str) -> Iterator[tuple[int, int]]:
    """Yield (start, end) spans for each line's leading whitespace run."""
    n = len(text)
    pos = 0
    for line in text.splitlines(keepends=True):
        end = pos
        while end < pos + len(line) and end < n and text[end] in (' ', '\t'):
            end += 1
        if end > pos:
            yield pos, end
        pos += len(line)
