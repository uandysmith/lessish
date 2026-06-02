"""Tier-0: declarations should have a space after the `:`.

A `:` is a *declaration* colon when it sits at the "name-position"
inside a block — the token chain back to the last `{` / `;` (at
brace-balanced depth zero) is exactly one IDENT-like token.
Pseudo-classes / pseudo-elements like `a:hover` sit at *selector
position* and don't qualify.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_NAME_KINDS = {Kind.IDENT, Kind.AT_NAME, Kind.DOLLAR_NAME, Kind.AT_AT_NAME}


@dataclass
class _State:
    brace_depth: int = 0
    region_start: int = 0


class SpaceAfterColonRule(Rule):
    id = 'space-after-colon'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Declarations should have one space after `:`.'
    token_kinds = (Kind.LBRACE, Kind.RBRACE, Kind.SEMICOLON, Kind.COLON)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind is Kind.LBRACE:
            state.brace_depth += 1
            state.region_start = idx + 1
            return
        if tok.kind is Kind.RBRACE:
            state.brace_depth = max(0, state.brace_depth - 1)
            state.region_start = idx + 1
            return
        if tok.kind is Kind.SEMICOLON:
            state.region_start = idx + 1
            return
        # COLON
        if state.brace_depth == 0:
            return
        toks = ctx.tokens
        region = toks[state.region_start : idx]
        if len(region) != 1 or region[0].kind not in _NAME_KINDS:
            return
        name_tok = region[0]
        if name_tok.kind is Kind.AT_NAME:
            if tok.index != name_tok.index + len(name_tok.text):
                return
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        colon_end = tok.index + 1
        if not nxt.leading_trivia and nxt.index == colon_end:
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='missing space after `:`',
                location=ctx.location_at(colon_end),
                span=(colon_end, colon_end),
                fix=Fix(replacement=' ', safety='safe', description='insert space'),
            )
