"""Tier-3: too many `!important` declarations in one file."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...lexer import Kind, Token
from .._findings import Finding
from ._base import LintContext, Rule


@dataclass
class _State:
    positions: list[int] = field(default_factory=list)


class ImportantOveruseRule(Rule):
    id = 'important-overuse'
    severity = 'warning'
    fix_tier = 'none'
    description = '`!important` should be used sparingly.'
    token_kinds = (Kind.IMPORTANT,)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:  # noqa: ARG002
        state.positions.append(tok.index)
        return ()

    def on_file_end(self, ctx: LintContext, state: _State) -> Iterable[Finding]:
        threshold = int(ctx.options.get('max-per-file', 0))
        if len(state.positions) <= threshold:
            return
        for pos in state.positions:
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='avoid `!important` outside utility classes',
                location=ctx.location_at(pos),
                span=(pos, pos + len('!important')),
            )
