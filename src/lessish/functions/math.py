"""Math functions: ceil/floor/round/sqrt/abs/sin/cos/tan/.../min/max/
pow/mod/percentage/pi/convert.

The unary functions delegate to a small helper that preserves or
overrides the unit per less.js's `math-helper.js` semantics: `ceil(10px)
→ 10px` (unit preserved), `sin(2)` → unitless (override to '').

`min`/`max` use the unit of the first dimension and convert subsequent
arguments through the unit table when possible.
"""

from __future__ import annotations

import math as _math
from collections.abc import Callable

from ..ast_nodes import Anonymous, Dimension, Node
from ..context import EvalContext
from ..errors import ArgumentError
from ..units import convert, convertible
from ..visitors import value_to_css
from . import register
from ._helpers import as_dimension, keyword_value, unwrap


def _math_helper(
    fn: Callable[[float], float],
    unit_override: str | None,
    n: Node,
    fn_name: str,
    *,
    convert_to: str | None = None,
) -> Dimension:
    d = as_dimension(n, fn_name=fn_name)
    unit = d.unit if unit_override is None else unit_override
    value = d.value
    # Choose the unit we coerce the input to: explicit `convert_to`
    # wins (used by `sin`/`cos`/`tan` to consume rad/deg/grad
    # interchangeably while still emitting a unitless result); else
    # fall back to `unit_override` when it names a real unit.
    target = convert_to if convert_to is not None else (unit_override or None)
    if target:
        # less.js calls .unify() before applying — convert to the canonical
        # unit so trig/etc. get radians/seconds/pixels regardless of input.
        if d.unit and convertible(d.unit, target):
            value = convert(d.value, d.unit, target)
    return Dimension(index=d.index, value=fn(value), unit=unit)


@register('ceil')
def fn_ceil(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.ceil, None, args[0], 'ceil')


@register('floor')
def fn_floor(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.floor, None, args[0], 'floor')


@register('sqrt')
def fn_sqrt(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.sqrt, None, args[0], 'sqrt')


@register('abs')
def fn_abs(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(abs, None, args[0], 'abs')


@register('tan')
def fn_tan(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.tan, '', args[0], 'tan', convert_to='rad')


@register('sin')
def fn_sin(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.sin, '', args[0], 'sin', convert_to='rad')


@register('cos')
def fn_cos(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.cos, '', args[0], 'cos', convert_to='rad')


@register('atan')
def fn_atan(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.atan, 'rad', args[0], 'atan')


@register('asin')
def fn_asin(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.asin, 'rad', args[0], 'asin')


@register('acos')
def fn_acos(args: list[Node], ctx: EvalContext) -> Node:
    return _math_helper(_math.acos, 'rad', args[0], 'acos')


@register('round')
def fn_round(args: list[Node], ctx: EvalContext) -> Node:
    """less.js uses `n.toFixed(f)` which rounds-half-away-from-zero in JS
    for typical inputs. Match that with our `js_round`-style logic, scaled.
    """
    if not args:
        raise ArgumentError('round() expects 1 or 2 arguments')
    d = as_dimension(args[0], fn_name='round')
    fraction = int(as_dimension(args[1], fn_name='round').value) if len(args) >= 2 else 0
    # JS `toFixed` accepts 0..100; outside that `10**fraction` is either
    # meaningless (negative) or a multi-million-digit int that pins CPU
    # and memory — a single-call DoS. Bound it before the exponentiation.
    if not 0 <= fraction <= 100:
        err = ArgumentError('round(): the precision (second argument) must be between 0 and 100')
        err._fatal = True
        raise err
    factor = 10**fraction
    rounded = _math.floor(abs(d.value) * factor + 0.5) / factor
    if d.value < 0:
        rounded = -rounded
    return Dimension(index=d.index, value=rounded, unit=d.unit)


@register('percentage')
def fn_percentage(args: list[Node], ctx: EvalContext) -> Node:
    a = unwrap(args[0])
    if not isinstance(a, Dimension):
        # less.js raises `argument must be a number` for any non-Dimension
        # input (e.g. unevaluated `16/17` Operation, Anonymous `var(...)`).
        # Mark fatal so eval_call wraps with the `Error evaluating function
        # 'percentage':` prefix and propagates instead of passing through.
        err = ArgumentError('argument must be a number')
        err._fatal = True
        raise err
    return Dimension(index=a.index, value=a.value * 100, unit='%')


@register('pi')
def fn_pi(args: list[Node], ctx: EvalContext) -> Node:
    return Dimension(index=0, value=_math.pi, unit='')


@register('mod')
def fn_mod(args: list[Node], ctx: EvalContext) -> Node:
    a = as_dimension(args[0], fn_name='mod')
    b = as_dimension(args[1], fn_name='mod')
    return Dimension(index=a.index, value=_math.fmod(a.value, b.value), unit=a.unit)


@register('pow')
def fn_pow(args: list[Node], ctx: EvalContext) -> Node:
    a = as_dimension(args[0], fn_name='pow')
    b = as_dimension(args[1], fn_name='pow')
    return Dimension(index=a.index, value=a.value**b.value, unit=a.unit)


@register('min')
def fn_min(args: list[Node], ctx: EvalContext) -> Node:
    return _min_max(args, is_min=True)


@register('max')
def fn_max(args: list[Node], ctx: EvalContext) -> Node:
    return _min_max(args, is_min=False)


def _min_max(args: list[Node], *, is_min: bool) -> Node:
    if not args:
        raise ArgumentError('min/max requires one or more arguments')
    dims: list[Dimension] = []
    for a in args:
        unwrapped = unwrap(a)
        if not isinstance(unwrapped, Dimension):
            # less.js falls back to emitting `min(...)`/`max(...)` literally
            # when an argument isn't numeric (so the CSS-native function
            # round-trips). Stay close to that behavior.
            inside = ', '.join(value_to_css(x) for x in args)
            return Anonymous(index=args[0].index, value=f'{"min" if is_min else "max"}({inside})')
        dims.append(unwrapped)
    base_unit = dims[0].unit
    best = dims[0]
    for d in dims[1:]:
        v = d.value
        if d.unit != base_unit:
            if convertible(d.unit, base_unit):
                v = convert(d.value, d.unit, base_unit)
            else:
                inside = ', '.join(value_to_css(x) for x in args)
                return Anonymous(index=args[0].index, value=f'{"min" if is_min else "max"}({inside})')
        if (is_min and v < best.value) or (not is_min and v > best.value):
            best = Dimension(index=d.index, value=v, unit=base_unit)
    return best


@register('convert')
def fn_convert(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) != 2:
        raise ArgumentError('convert() expects 2 arguments')
    d = as_dimension(args[0], fn_name='convert')
    target_kw = keyword_value(args[1])
    if target_kw is None:
        raise ArgumentError('convert() second argument must be a unit name')
    target = target_kw.strip()
    if not convertible(d.unit, target):
        return d
    return Dimension(index=d.index, value=convert(d.value, d.unit, target), unit=target)
