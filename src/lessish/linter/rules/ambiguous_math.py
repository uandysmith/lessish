"""M3: arithmetic expression in a value position where the result
depends on the configured `math` mode.

Without parens, `1px + 1` evaluates to `2px` under `math: always` but
stays literal under `math: parens-division` / `math: parens`. The
rule flags any `Operation` node found OUTSIDE a `Paren` wrapper at
value position so the author can decide whether parens make the
intent explicit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import (
    Anonymous,
    Declaration,
    Expression,
    Node,
    Operation,
    Paren,
    Value,
)
from ...parser import parse_value_text
from .._findings import Finding
from ._base import LintContext, Rule


class AmbiguousMathRule(Rule):
    id = 'ambiguous-math'
    severity = 'warning'
    fix_tier = 'none'
    description = 'Arithmetic without parens — result depends on `math` mode.'
    node_types = (Declaration,)

    def on_node(  # type: ignore[override]
        self, decl: Declaration, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        value = decl.value
        if isinstance(value, Anonymous):
            try:
                value = parse_value_text(value.value, base_offset=value.index)
            except Exception:  # noqa: BLE001
                return
        if not isinstance(value, (Value, Expression)):
            return
        for op in _find_unwrapped_operations(value, in_paren=False):
            if op.op not in ('+', '-', '*', '/'):
                continue
            yield Finding(
                rule_id='ambiguous-math',
                severity='warning',
                message=f'arithmetic `{op.op}` outside `(...)` — result depends on `math` mode',
                location=ctx.location_at(op.index),
                span=(op.index, op.index + 1),
            )


def _find_unwrapped_operations(node: Node, *, in_paren: bool) -> Iterable[Operation]:
    if isinstance(node, Paren):
        yield from _find_unwrapped_operations(node.value, in_paren=True)
        return
    if isinstance(node, Operation):
        if not in_paren:
            yield node
        yield from _find_unwrapped_operations(node.lhs, in_paren=in_paren)
        yield from _find_unwrapped_operations(node.rhs, in_paren=in_paren)
        return
    if isinstance(node, Value):
        for e in node.expressions:
            yield from _find_unwrapped_operations(e, in_paren=in_paren)
        return
    if isinstance(node, Expression):
        for v in node.values:
            yield from _find_unwrapped_operations(v, in_paren=in_paren)
        return
