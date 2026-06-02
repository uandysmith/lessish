"""List functions: extract, length, range.

A "list" in Less is one of:
  * `Value` with multiple `Expression` children (comma-separated);
  * `Expression` with multiple values (space-separated);
  * a single scalar — counts as a 1-element list.
"""

from __future__ import annotations

from ..ast_nodes import Dimension, Expression, Node, Value
from ..context import EvalContext
from ..errors import ArgumentError, EvalError
from . import register


def _items(node: Node) -> list[Node]:
    if isinstance(node, Value):
        return list(node.expressions)
    if isinstance(node, Expression):
        return list(node.values)
    return [node]


@register('length')
def fn_length(args: list[Node], ctx: EvalContext) -> Node:
    """Number of comma- or space-separated items in the (possibly trivial) list."""
    n = args[0]
    return Dimension(index=n.index, value=float(len(_items(n))), unit='')


@register('extract')
def fn_extract(args: list[Node], ctx: EvalContext) -> Node:
    """1-indexed list access. Raises if the index is out of range —
    less.js silently returns undefined, but lessish surfaces the bug
    rather than letting `None` propagate.
    """
    if len(args) != 2:
        raise ArgumentError('extract() expects 2 arguments')
    items = _items(args[0])
    idx_node = args[1]
    if not isinstance(idx_node, Dimension):
        raise ArgumentError('extract() second argument must be a number')
    idx = int(idx_node.value) - 1
    if idx < 0 or idx >= len(items):
        raise ArgumentError(f'extract() index {idx + 1} out of range for list of length {len(items)}')
    return items[idx]


@register('range')
def fn_range(args: list[Node], ctx: EvalContext) -> Node:
    """`range(end)` or `range(start, end[, step])` → Expression of Dimensions.
    The unit of `end` propagates to every generated element."""
    if not args:
        raise ArgumentError('range() expects 1 to 3 arguments')
    if len(args) == 1:
        end = args[0]
        if not isinstance(end, Dimension):
            raise ArgumentError('range() expects numeric arguments')
        from_ = 1.0
        to = end
        step = 1.0
    else:
        start = args[0]
        end = args[1]
        if not isinstance(start, Dimension) or not isinstance(end, Dimension):
            raise ArgumentError('range() expects numeric arguments')
        from_ = start.value
        to = end
        step = 1.0
        if len(args) >= 3:
            step_node = args[2]
            if not isinstance(step_node, Dimension):
                raise ArgumentError('range() step must be numeric')
            step = step_node.value
    values: list[Node] = []
    i = from_
    # Bound the element count: `range(1e9)` would otherwise exhaust
    # memory, and `step <= 0` with `to >= from_` would never terminate.
    # Both are caught by the same cap.
    limit = ctx.range_max_elements
    while i <= to.value + 1e-9:
        if len(values) >= limit:
            raise EvalError(
                f'range() would generate more than {limit} elements — refusing '
                '(raise the `range_max_elements` compile option if intended)'
            )
        values.append(Dimension(index=to.index, value=i, unit=to.unit))
        i += step
    return Expression(index=to.index, values=values)
