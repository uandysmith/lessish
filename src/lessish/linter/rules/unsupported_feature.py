"""Tier-3: detect Less features lessish refuses to compile.

These all raise `UnsupportedFeatureError` at compile time. The
linter surfaces them as findings so a CI lint job doesn't crash.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import AtRule
from ...lexer import Kind, Token
from .._findings import Finding
from ._base import LintContext, Rule


class UnsupportedFeatureRule(Rule):
    id = 'unsupported-feature'
    severity = 'error'
    fix_tier = 'none'
    description = 'Less feature not implemented by lessish (JS plugins, backticks).'
    token_kinds = (Kind.BACKTICK_STRING, Kind.TILDE_BACKTICK_STRING)
    node_types = (AtRule,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        start = tok.index
        end = start + len(tok.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='JavaScript backtick expression is not supported',
            location=ctx.location_at(start),
            span=(start, end),
        )

    def on_node(  # type: ignore[override]
        self, node: AtRule, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        if node.name == '@plugin':
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='`@plugin` requires JavaScript evaluation',
                location=ctx.location_at(node.index),
                span=(node.index, node.index),
            )
