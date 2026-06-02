"""Tier-0: each line's leading indent matches `depth * width` spaces.

Only checks lines that START a new statement — the previous non-trivia
token was `{`, `;`, or `}` (or the file's start). Continuation lines
inside multi-line at-rule preludes / mixin signatures / value
expressions are skipped because there's no objective "right" indent
for those — the user picks their own visual alignment.

The leading trivia of the token must be PURELY whitespace; a comment
between the newline and the token (e.g. `/* hint */ color: red;`) is a
signal that we'd clobber author content if we rewrote the run.

Tabs in indent are NOT touched — the `no-tabs` rule owns that.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...lexer import Kind
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_STATEMENT_BOUNDARIES = frozenset({Kind.LBRACE, Kind.SEMICOLON, Kind.RBRACE})


class IndentWidthRule(Rule):
    id = 'indent-width'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Each indent level should be `width` spaces.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        width = int(ctx.options.get('width', 2))
        text = ctx.text
        toks = ctx.tokens
        n = len(text)

        depth = 0
        last_boundary_was_statement = True
        seen_lines: set[int] = set()

        for tok in toks:
            if tok.kind is Kind.EOF:
                continue

            line = ctx.location_at(tok.index).line
            effective_depth = depth - 1 if tok.kind is Kind.RBRACE and depth > 0 else depth

            if line not in seen_lines and last_boundary_was_statement:
                seen_lines.add(line)
                col_start = tok.index
                while col_start > 0 and text[col_start - 1] != '\n':
                    col_start -= 1
                indent_end = col_start
                while indent_end < tok.index and indent_end < n and text[indent_end] in (' ', '\t'):
                    indent_end += 1
                # Only fire when nothing but whitespace sits between the
                # newline and the first token.
                if indent_end == tok.index:
                    indent = text[col_start:indent_end]
                    if '\t' not in indent:
                        expected = ' ' * (effective_depth * width)
                        if indent != expected:
                            yield Finding(
                                rule_id=self.id,
                                severity=self.severity,
                                message=(f'expected {len(expected)} spaces of indent, got {len(indent)}'),
                                location=ctx.location_at(col_start),
                                span=(col_start, indent_end),
                                fix=Fix(
                                    replacement=expected,
                                    safety='safe',
                                    description='normalize indent',
                                ),
                            )

            if tok.kind is Kind.LBRACE:
                depth += 1
                last_boundary_was_statement = True
            elif tok.kind is Kind.RBRACE:
                depth = max(0, depth - 1)
                last_boundary_was_statement = True
            elif tok.kind is Kind.SEMICOLON:
                last_boundary_was_statement = True
            else:
                last_boundary_was_statement = False
