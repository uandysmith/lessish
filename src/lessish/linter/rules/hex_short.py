"""Tier-1: `#RRGGBB` shortens to `#RGB` when both halves match."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


def _shortenable(text: str) -> bool:
    if not text.startswith('#') or len(text) != 7:
        return False
    body = text[1:]
    if not all(c in '0123456789abcdefABCDEF' for c in body):
        return False
    return (
        body[0].lower() == body[1].lower() and body[2].lower() == body[3].lower() and body[4].lower() == body[5].lower()
    )


def _shorten(text: str) -> str:
    return '#' + text[1] + text[3] + text[5]


class HexShortRule(Rule):
    id = 'hex-short'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Shorten `#RRGGBB` to `#RGB` when both halves match.'
    token_kinds = (Kind.HASH,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if not _shortenable(tok.text):
            return
        short = _shorten(tok.text).lower()
        start = tok.index
        end = start + len(tok.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'`{tok.text}` can be shortened to `{short}`',
            location=ctx.location_at(start),
            span=(start, end),
            fix=Fix(
                replacement=short,
                safety='safe',
                description=f'shorten {tok.text!r} to {short!r}',
            ),
        )
