"""Tier-3: mixin signatures with too many parameters are hard to call
correctly. Default threshold matches the plan: ≥ 6 parameters fires."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import MixinDefinition
from .._findings import Finding
from ._base import LintContext, Rule


class ExcessiveMixinArgsRule(Rule):
    id = 'excessive-mixin-args'
    severity = 'warning'
    fix_tier = 'none'
    description = 'Mixin definitions with too many parameters are hard to read.'
    node_types = (MixinDefinition,)

    def on_node(  # type: ignore[override]
        self, node: MixinDefinition, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        threshold = int(ctx.options.get('max-args', 6))
        n = len(node.params)
        if n < threshold:
            return
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'mixin `{node.name}` has {n} parameters (max {threshold - 1})',
            location=ctx.location_at(node.index),
            span=(node.index, node.index),
        )
