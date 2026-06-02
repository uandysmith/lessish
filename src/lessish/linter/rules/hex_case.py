"""Tier-1: hex literals should be lowercase."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


def _is_hex_literal(text: str) -> bool:
    if not text.startswith('#'):
        return False
    body = text[1:]
    if len(body) not in (3, 4, 6, 8):
        return False
    return all(c in '0123456789abcdefABCDEF' for c in body)


class HexCaseRule(Rule):
    id = 'hex-case'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Hex literals should be lowercase.'
    token_kinds = (Kind.HASH,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if not _is_hex_literal(tok.text):
            return
        if tok.text == tok.text.lower():
            return
        start = tok.index
        end = start + len(tok.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'`{tok.text}` should be lowercase',
            location=ctx.location_at(start),
            span=(start, end),
            fix=Fix(
                replacement=tok.text.lower(),
                safety='safe',
                description=f'lowercase {tok.text!r}',
            ),
        )
