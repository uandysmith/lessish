"""Tier-3: mixin signatures that mix variadic-tail (`@rest...`) with
default-valued params bind in surprising ways. Flag any definition
that has both shapes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import MixinDefinition
from .._findings import Finding
from ._base import LintContext, Rule


class MixedRestAndDefaultRule(Rule):
    id = 'mixed-rest-and-default'
    severity = 'warning'
    fix_tier = 'none'
    description = 'Mixin parameter list mixes variadic and default-valued params.'
    node_types = (MixinDefinition,)

    def on_node(  # type: ignore[override]
        self, node: MixinDefinition, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        has_variadic = any(p.variadic for p in node.params)
        has_default = any(p.default is not None for p in node.params)
        if has_variadic and has_default:
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'mixin `{node.name}` mixes `...` with default-valued params',
                location=ctx.location_at(node.index),
                span=(node.index, node.index),
            )
