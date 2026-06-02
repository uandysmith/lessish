"""Tier-3: detect rulesets nested deeper than `max_depth`."""

from __future__ import annotations

from collections.abc import Iterable

from ...ast_nodes import AtRule, Ruleset
from .._findings import Finding
from ._base import LintContext, Rule


class DeepNestingRule(Rule):
    id = 'deep-nesting'
    severity = 'warning'
    fix_tier = 'none'
    description = 'Rulesets nested deeper than `max-depth` are hard to read.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        root = ctx.ast()
        if root is None:
            return
        max_depth = int(ctx.options.get('max-depth', 4))
        for node, depth in _walk(root, 0):
            if depth > max_depth:
                yield Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    message=f'ruleset nested {depth} deep (max {max_depth})',
                    location=ctx.location_at(node.index),
                    span=(node.index, node.index),
                )


def _walk(node: Ruleset, depth: int) -> Iterable[tuple[Ruleset, int]]:
    for child in node.rules:
        if isinstance(child, Ruleset):
            yield child, depth + 1
            yield from _walk(child, depth + 1)
        elif isinstance(child, AtRule) and child.body is not None:
            for inner in child.body:
                if isinstance(inner, Ruleset):
                    yield inner, depth + 1
                    yield from _walk(inner, depth + 1)
