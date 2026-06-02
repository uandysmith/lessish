"""Tier-0: top-level rulesets should be separated by a blank line.

Off by default — opt in via `[tool.lessish.lint.rules.blank-line-before-block]`
with `enabled = true`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


@dataclass
class _State:
    depth: int = 0


class BlankLineBeforeBlockRule(Rule):
    id = 'blank-line-before-block'
    severity = 'info'
    fix_tier = 'safe'
    description = 'Top-level rulesets should be separated by a blank line.'
    token_kinds = (Kind.LBRACE, Kind.RBRACE)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if not ctx.options.get('enabled', False):
            return
        toks = ctx.tokens
        if tok.kind is Kind.LBRACE:
            state.depth += 1
            return
        # RBRACE
        state.depth = max(0, state.depth - 1)
        if state.depth != 0 or idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        if nxt.kind is Kind.EOF:
            return
        between = ctx.text[tok.index + 1 : nxt.index]
        if between.count('\n') < 2:
            insert_at = tok.index + 1
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='missing blank line between top-level blocks',
                location=ctx.location_at(insert_at),
                span=(insert_at, insert_at),
                fix=Fix(
                    replacement='\n',
                    safety='safe',
                    description='insert blank line',
                ),
            )
