"""Tier-0: there should be a space before `{` in a ruleset."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class SpaceBeforeLBraceRule(Rule):
    id = 'space-before-lbrace'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Insert a space between selector and `{`.'
    token_kinds = (Kind.LBRACE,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if idx == 0:
            return
        if tok.leading_trivia:
            return
        prev = ctx.tokens[idx - 1]
        prev_end = prev.index + len(prev.text)
        if prev_end != tok.index:
            return
        if prev.kind is Kind.EOF:
            return
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='missing space before `{`',
            location=ctx.location_at(tok.index),
            span=(tok.index, tok.index),
            fix=Fix(replacement=' ', safety='safe', description='insert space'),
        )
