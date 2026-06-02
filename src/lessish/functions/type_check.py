"""Type predicate functions: iscolor/isnumber/isstring/iskeyword/isurl,
unit-specific predicates (ispixel/ispercentage/isem/isunit), `isruleset`.

The keyword-valued predicates return `Keyword('true')` /
`Keyword('false')` exactly like less.js's `Keyword.True` /
`Keyword.False`.

Also includes `unit(...)` (replace/clear the unit of a Dimension) and
`get-unit(...)` (extract the unit as an Anonymous string).
"""

from __future__ import annotations

from ..ast_nodes import Anonymous, Color, DetachedRuleset, Dimension, Keyword, Node, Quoted, Url
from ..colors import parse_color
from ..context import EvalContext
from ..errors import ArgumentError
from ..visitors import value_to_css
from . import register
from ._helpers import unwrap


def _truthy(node: type[Node], n: Node) -> Keyword:
    matched = isinstance(unwrap(n), node)
    return Keyword(index=n.index, value='true' if matched else 'false')


@register('isruleset')
def fn_isruleset(args: list[Node], ctx: EvalContext) -> Node:
    return _truthy(DetachedRuleset, args[0])


@register('iscolor')
def fn_iscolor(args: list[Node], ctx: EvalContext) -> Node:
    a = unwrap(args[0])
    if isinstance(a, Color):
        return Keyword(index=a.index, value='true')
    # Named color keyword counts as a color in less.js (`iscolor(red)` is true).
    if isinstance(a, Keyword) and parse_color(a.value) is not None:
        return Keyword(index=a.index, value='true')
    return Keyword(index=a.index, value='false')


@register('isnumber')
def fn_isnumber(args: list[Node], ctx: EvalContext) -> Node:
    return _truthy(Dimension, args[0])


@register('isstring')
def fn_isstring(args: list[Node], ctx: EvalContext) -> Node:
    return _truthy(Quoted, args[0])


@register('iskeyword')
def fn_iskeyword(args: list[Node], ctx: EvalContext) -> Node:
    return _truthy(Keyword, args[0])


@register('isurl')
def fn_isurl(args: list[Node], ctx: EvalContext) -> Node:
    return _truthy(Url, args[0])


def _isunit(n: Node, unit: str) -> Keyword:
    n = unwrap(n)
    val = 'true' if isinstance(n, Dimension) and n.unit == unit else 'false'
    return Keyword(index=n.index, value=val)


@register('ispixel')
def fn_ispixel(args: list[Node], ctx: EvalContext) -> Node:
    return _isunit(args[0], 'px')


@register('ispercentage')
def fn_ispercentage(args: list[Node], ctx: EvalContext) -> Node:
    return _isunit(args[0], '%')


@register('isem')
def fn_isem(args: list[Node], ctx: EvalContext) -> Node:
    return _isunit(args[0], 'em')


@register('isunit')
def fn_isunit(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) < 2:
        raise ArgumentError('isunit() expects 2 arguments')
    unit_node = unwrap(args[1])
    if isinstance(unit_node, Quoted | Keyword | Anonymous):
        unit_text = unit_node.value
    else:
        unit_text = value_to_css(unit_node)
    return _isunit(args[0], unit_text)


@register('unit')
def fn_unit(args: list[Node], ctx: EvalContext) -> Node:
    if not args:
        raise ArgumentError('unit() expects 1 or 2 arguments')
    val = unwrap(args[0])
    if not isinstance(val, Dimension):
        err = ArgumentError('the first argument to unit must be a number. Have you forgotten parenthesis?')
        err._fatal = True
        raise err
    if len(args) >= 2:
        unit_node = unwrap(args[1])
        if isinstance(unit_node, Keyword | Anonymous | Quoted):
            unit_text = unit_node.value
        else:
            unit_text = value_to_css(unit_node)
    else:
        unit_text = ''
    return Dimension(index=val.index, value=val.value, unit=unit_text)


@register('get-unit')
def fn_get_unit(args: list[Node], ctx: EvalContext) -> Node:
    val = unwrap(args[0])
    if not isinstance(val, Dimension):
        raise ArgumentError('get-unit() expects a number')
    return Anonymous(index=val.index, value=val.unit)
