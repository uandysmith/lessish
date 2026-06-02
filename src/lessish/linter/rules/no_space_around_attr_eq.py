"""Tier-0: drop spaces around `=` inside `[attr = "val"]`.

CSS attribute selectors don't accept whitespace around the operator
per the selector spec; less.js does parse with spaces but the output
is the unspaced form. Strip to `[attr="val"]`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


@dataclass
class _State:
    bracket_depth: int = 0


class NoSpaceAroundAttrEqRule(Rule):
    id = 'no-space-around-attr-eq'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Drop spaces around attribute-selector operators.'
    token_kinds = (Kind.LBRACKET, Kind.RBRACKET, Kind.EQUALS)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind is Kind.LBRACKET:
            state.bracket_depth += 1
            return
        if tok.kind is Kind.RBRACKET:
            state.bracket_depth = max(0, state.bracket_depth - 1)
            return
        # EQUALS
        if state.bracket_depth == 0:
            return
        toks = ctx.tokens
        if idx == 0 or idx + 1 >= len(toks):
            return
        prev = toks[idx - 1]
        nxt = toks[idx + 1]
        prev_end = prev.index + len(prev.text)
        if tok.index != prev_end:
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='drop space before `=` in attribute selector',
                location=ctx.location_at(prev_end),
                span=(prev_end, tok.index),
                fix=Fix(replacement='', safety='safe', description='strip space'),
            )
        eq_end = tok.index + 1
        if nxt.index != eq_end:
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='drop space after `=` in attribute selector',
                location=ctx.location_at(eq_end),
                span=(eq_end, nxt.index),
                fix=Fix(replacement='', safety='safe', description='strip space'),
            )
