"""Tier-0: blank line directly before `}` (block closer) is noise."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_TRAILING_BLANK = re.compile(r'(\n([ \t]*\n)+)([ \t]*)\Z')


class BlankLineAtBlockEndRule(Rule):
    id = 'blank-line-at-block-end'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'No blank line directly before `}`.'
    token_kinds = (Kind.RBRACE,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if idx == 0:
            return
        prev = ctx.tokens[idx - 1]
        prev_end = prev.index + len(prev.text)
        between = ctx.text[prev_end : tok.index]
        m = _TRAILING_BLANK.search(between)
        if m:
            start = prev_end + m.start()
            end = prev_end + m.end()
            indent_before_brace = m.group(3)
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='blank line directly before `}`',
                location=ctx.location_at(start),
                span=(start, end),
                fix=Fix(
                    replacement='\n' + indent_before_brace,
                    safety='safe',
                    description='drop blank line',
                ),
            )
