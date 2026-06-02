"""Cross-cutting helpers used by every function module.

Argument coercion lives here because every function needs to ask the same
questions: "is this a Dimension?", "can I coerce this to a Color?", etc.
Each function decides whether unmet expectations are errors (most do) or
silent passthroughs.
"""

from __future__ import annotations

from ..ast_nodes import (
    Anonymous,
    Call,
    Color,
    Dimension,
    Expression,
    Keyword,
    Node,
    Quoted,
    Value,
)
from ..colors import parse_color
from ..errors import ArgumentError


def unwrap(node: Node) -> Node:
    """Drop trivial Value/Expression wrappers so callers can pattern-match
    on the leaf node (Dimension, Color, ...). Mirrors the helper in
    `evaluator._unwrap_single`."""
    while True:
        if isinstance(node, Value) and len(node.expressions) == 1:
            node = node.expressions[0]
        elif isinstance(node, Expression) and len(node.values) == 1:
            node = node.values[0]
        else:
            return node


def as_dimension(node: Node, *, fn_name: str = '') -> Dimension:
    """Coerce `node` to a Dimension or raise ArgumentError."""
    node = unwrap(node)
    if isinstance(node, Dimension):
        return node
    raise ArgumentError(f'{fn_name or "argument"} expected a number, got {type(node).__name__}')


def as_color(node: Node, *, fn_name: str = '', strict: bool = False) -> Color:
    """Coerce `node` to a Color or raise ArgumentError.

    Accepts:
      * `Color` — returned as-is.
      * `Keyword('red')` etc. — resolved through the CSS named-color table.
      * `Quoted('#ff0000')` — parsed as hex.

    `strict=True` escalates failures on CSS-only Call values
    (`var(...)`) and Anonymous text to a *fatal* RuntimeError-class
    error matching less.js's `Argument cannot be evaluated to a color`
    wording. Used by color-CONSUMING functions (darken/lighten/...);
    color-CONSTRUCTORS (rgba/hsl/...) keep `strict=False` so the
    soft-failure passthrough can round-trip `rgba(var(--x), 0.2)`.
    """
    node = unwrap(node)
    if isinstance(node, Color):
        return node
    if isinstance(node, Keyword):
        parsed = parse_color(node.value)
        if parsed is not None:
            rgb, alpha = parsed
            return Color(
                index=node.index,
                value=node.value,
                rgb=rgb,
                alpha=alpha,
                color_function='',
            )
    if isinstance(node, Quoted):
        parsed = parse_color(node.value)
        if parsed is not None:
            rgb, alpha = parsed
            return Color(
                index=node.index,
                value=node.value,
                rgb=rgb,
                alpha=alpha,
                color_function='',
            )
    if strict and isinstance(node, (Call, Anonymous)):
        err = ArgumentError('Argument cannot be evaluated to a color')
        err._fatal = True
        err._less_js_name = 'RuntimeError'
        raise err
    raise ArgumentError(f'{fn_name or "argument"} expected a color, got {type(node).__name__}')


def number_value(node: Node, *, fn_name: str = '') -> float:
    """Extract the raw numeric value from a Dimension. Percent values
    are divided by 100 (mirrors less.js's `number()` helper, used by
    color functions where 0..100% should map to 0..1).
    """
    d = as_dimension(node, fn_name=fn_name)
    if d.unit == '%':
        return d.value / 100.0
    return d.value


def scaled(node: Node, size: float = 255.0, *, fn_name: str = '') -> float:
    """Like `number_value`, but treats percentages as a fraction of `size`.
    Used by RGB-channel inputs so `rgb(50%, 0, 0)` becomes `rgb(127.5, 0, 0)`.
    """
    d = as_dimension(node, fn_name=fn_name)
    if d.unit == '%':
        return d.value * size / 100.0
    return d.value


def keyword_value(node: Node) -> str | None:
    """Return the textual form of a Keyword/Anonymous/Quoted, or None."""
    node = unwrap(node)
    if isinstance(node, Keyword | Anonymous):
        return node.value
    if isinstance(node, Quoted):
        return node.value
    return None


def quoted_value(node: Node, *, fn_name: str = '') -> Quoted:
    """Coerce to a Quoted string or raise."""
    node = unwrap(node)
    if isinstance(node, Quoted):
        return node
    raise ArgumentError(f'{fn_name or "argument"} expected a string, got {type(node).__name__}')
