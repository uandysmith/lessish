"""M3: mixin guard / CSS guard that always evaluates to false.

Static-only detection: compare literal-literal conditions like
`when (1 = 2)` or `when (1 > 2)`. For richer cases involving
variables, --full mode runs the evaluator and tests the guard.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import (
    Condition,
    Dimension,
    Keyword,
    MixinDefinition,
    Node,
    Ruleset,
)
from .._findings import Finding
from ._base import LintContext, Rule


class UnreachableMixinBranchRule(Rule):
    id = 'unreachable-mixin-branch'
    severity = 'warning'
    fix_tier = 'none'
    description = 'Mixin / CSS guard with a statically-false condition.'
    requires_eval = False  # the rule's static cases don't need eval
    node_types = (MixinDefinition, Ruleset)

    def on_node(self, node: Node, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        guard: Condition | None
        if isinstance(node, MixinDefinition):
            guard = node.guard
        elif isinstance(node, Ruleset):
            guard = node.condition
        else:
            return
        if guard is None:
            return
        if _statically_false(guard):
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message='guard `when (...)` is always false; block is unreachable',
                location=ctx.location_at(node.index),
                span=(node.index, node.index),
            )


def _statically_false(cond: Condition) -> bool:
    """Best-effort: only handles comparison ops between dimensional or
    keyword literals. `op` is one of `=`, `<`, `<=`, `>`, `>=`, `<>`.
    """
    if cond.op == 'not' and cond.lhs is not None and isinstance(cond.lhs, Condition):
        return _statically_true(cond.lhs)
    if cond.op in ('and', 'or'):
        return False
    a = _literal_value(cond.lhs)
    b = _literal_value(cond.rhs) if cond.rhs is not None else None
    if a is None or b is None:
        return False
    result = _compare(a, b, cond.op)
    if result is None:
        return False
    if cond.negate:
        result = not result
    return not result


def _statically_true(cond: Condition) -> bool:
    a = _literal_value(cond.lhs)
    b = _literal_value(cond.rhs) if cond.rhs is not None else None
    if a is None or b is None:
        return False
    result = _compare(a, b, cond.op)
    if result is None:
        return False
    if cond.negate:
        result = not result
    return result


_Literal = tuple[Any, ...]  # heterogeneous: ('dim', float, str) | ('kw', str)


def _literal_value(node: Node | None) -> _Literal | None:
    if isinstance(node, Dimension):
        return ('dim', node.value, node.unit)
    if isinstance(node, Keyword):
        return ('kw', node.value)
    return None


def _compare(a: _Literal, b: _Literal, op: str) -> bool | None:
    if a[0] != b[0]:
        return None
    if a[0] == 'dim':
        # Different units: incomparable.
        if a[2] != b[2]:
            return None
        av: float = a[1]
        bv: float = b[1]
        if op == '=':
            return av == bv
        if op == '<':
            return av < bv
        if op == '<=':
            return av <= bv
        if op == '>':
            return av > bv
        if op == '>=':
            return av >= bv
        if op == '<>':
            return av != bv
    if a[0] == 'kw':
        if op == '=':
            return bool(a[1] == b[1])
        if op == '<>':
            return bool(a[1] != b[1])
    return None
