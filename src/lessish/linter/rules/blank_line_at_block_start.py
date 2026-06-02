"""Tier-0: blank line directly after `{` (block opener) is noise."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_BLANK_RUN = re.compile(r'\n([ \t]*\n)+')


class BlankLineAtBlockStartRule(Rule):
    id = 'blank-line-at-block-start'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'No blank line directly after `{`.'
    token_kinds = (Kind.LBRACE,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        toks = ctx.tokens
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        between = ctx.text[tok.index + 1 : nxt.index]
        m = _BLANK_RUN.match(between)
        if m:
            start = tok.index + 1
            end = start + m.end()
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='blank line directly after `{`',
                location=ctx.location_at(start),
                span=(start, end),
                fix=Fix(replacement='\n', safety='safe', description='drop blank line'),
            )
