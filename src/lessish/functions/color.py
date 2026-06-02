"""Color functions: rgb/rgba/hsl/hsla constructors, channel queries,
HSL adjustments (lighten/darken/saturate/desaturate/fadein/fadeout/fade/
spin), and the Sass-derived `mix`/`tint`/`shade`/`contrast` family.

All formulas are ported operation-for-operation from less.js's
`functions/color.js` so HSL round trips agree byte-exact with the
reference oracle.
"""

from __future__ import annotations

import math as _math
from collections.abc import Callable

from ..ast_nodes import Anonymous, Color, Dimension, Expression, Node, Operation
from ..colors import (
    clamp_channel,
    hsl_to_rgb,
    luma,
    parse_color,
    rgb_to_hsl,
    rgb_to_hsv,
)
from ..context import EvalContext
from ..errors import ArgumentError
from . import register
from ._helpers import as_color, as_dimension, keyword_value, number_value, quoted_value, scaled, unwrap


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _make_color(rgb: tuple[float, float, float], alpha: float, color_function: str = '', index: int = 0) -> Color:
    return Color(index=index, value='', rgb=rgb, alpha=alpha, color_function=color_function)


def _modern_color_components(args: list[Node], min_count: int) -> tuple[list[Node], Node | None] | None:
    """Detect CSS Color 4 modern syntax: a single Expression argument
    containing space-separated channels with an optional `/ alpha`.

    Returns (channel_nodes, alpha_node_or_None) on match, or None if the
    arg shape isn't modern syntax. Callers use the channel nodes the
    same way they'd use comma-separated positional args.
    """
    if len(args) != 1:
        return None
    a = args[0]
    if not isinstance(a, Expression):
        return None
    values = list(a.values)
    if len(values) < min_count:
        return None
    last = values[-1]
    alpha: Node | None = None
    if isinstance(last, Operation) and last.op == '/':
        # Split `<last-channel> / <alpha>` — the lhs is the actual final
        # channel, the rhs is alpha.
        values[-1] = last.lhs
        alpha = last.rhs
    if len(values) != min_count:
        return None
    return values, alpha


@register('rgb')
def fn_rgb(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) == 1:
        # rgb(r g b) space-separated form: argument is a single Expression
        # already unwrapped by the caller, but check just in case it's a
        # plain Color (no-op).
        a = unwrap(args[0])
        if isinstance(a, Color):
            return _make_color(a.rgb, a.alpha, 'rgb', a.index)
    # CSS Color 4 modern syntax: `rgb(R G B)` / `rgb(R G B / A)` —
    # single Expression arg with space-separated channels. less.js
    # emits these as hex when alpha=1 / rgba(...) otherwise.
    modern = _modern_color_components(args, 3)
    if modern is not None:
        channels, alpha_node = modern
        r = scaled(channels[0], 255, fn_name='rgb')
        g = scaled(channels[1], 255, fn_name='rgb')
        b = scaled(channels[2], 255, fn_name='rgb')
        if alpha_node is not None:
            alpha_val = _clamp01(number_value(alpha_node, fn_name='rgb'))
            return _make_color((r, g, b), alpha_val, 'rgba', args[0].index)
        return _make_color((r, g, b), 1.0, 'rgb', args[0].index)
    if len(args) != 3:
        raise ArgumentError('rgb() expects 3 arguments')
    r = scaled(args[0], 255, fn_name='rgb')
    g = scaled(args[1], 255, fn_name='rgb')
    b = scaled(args[2], 255, fn_name='rgb')
    return _make_color((r, g, b), 1.0, 'rgb', args[0].index)


@register('rgba')
def fn_rgba(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) == 1:
        # `rgba(<color>)` — pass through. Matches less.js where wrapping
        # a color in `rgba(...)` is a no-op: the color already carries
        # its alpha (e.g. `rgba(#55FF5599)` → `rgba(85, 255, 85, 0.6)`).
        # If the single arg isn't a Color, fall through to modern-syntax
        # parsing.
        a_unw = unwrap(args[0])
        if isinstance(a_unw, Color):
            return _make_color(a_unw.rgb, a_unw.alpha, 'rgba', a_unw.index)
    modern = _modern_color_components(args, 3)
    if modern is not None:
        channels, alpha_node = modern
        r = scaled(channels[0], 255, fn_name='rgba')
        g = scaled(channels[1], 255, fn_name='rgba')
        b = scaled(channels[2], 255, fn_name='rgba')
        a = 1.0 if alpha_node is None else _clamp01(number_value(alpha_node, fn_name='rgba'))
        return _make_color((r, g, b), a, 'rgba', args[0].index)
    if len(args) == 2:
        c = as_color(args[0], fn_name='rgba')
        a = _clamp01(number_value(args[1], fn_name='rgba'))
        return _make_color(c.rgb, a, 'rgba', c.index)
    if len(args) != 4:
        raise ArgumentError('rgba() expects 1, 2, or 4 arguments')
    r = scaled(args[0], 255, fn_name='rgba')
    g = scaled(args[1], 255, fn_name='rgba')
    b = scaled(args[2], 255, fn_name='rgba')
    a = _clamp01(number_value(args[3], fn_name='rgba'))
    return _make_color((r, g, b), a, 'rgba', args[0].index)


@register('hsl')
def fn_hsl(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) == 1:
        # `hsl(<color>)` — re-emit the color in hsl form. Alpha is taken
        # from the color itself (so alpha<1 round-trips through hsla).
        a_unw = unwrap(args[0])
        if isinstance(a_unw, Color):
            cf = 'hsla' if a_unw.alpha < 1 else 'hsl'
            return _make_color(a_unw.rgb, a_unw.alpha, cf, a_unw.index)
    modern = _modern_color_components(args, 3)
    if modern is not None:
        channels, alpha_node = modern
        h = number_value(channels[0], fn_name='hsl')
        s = _clamp01(number_value(channels[1], fn_name='hsl'))
        lum = _clamp01(number_value(channels[2], fn_name='hsl'))
        rgb = hsl_to_rgb(h, s, lum)
        if alpha_node is not None:
            a = _clamp01(number_value(alpha_node, fn_name='hsl'))
            return _make_color(rgb, a, 'hsla', args[0].index)
        return _make_color(rgb, 1.0, 'hsl', args[0].index)
    if len(args) != 3:
        raise ArgumentError('hsl() expects 3 arguments')
    h = number_value(args[0], fn_name='hsl')
    s = _clamp01(number_value(args[1], fn_name='hsl'))
    lum = _clamp01(number_value(args[2], fn_name='hsl'))
    rgb = hsl_to_rgb(h, s, lum)
    return _make_color(rgb, 1.0, 'hsl', args[0].index)


@register('hsla')
def fn_hsla(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) == 1:
        # `hsla(<color>)` — re-emit the color in hsla form, preserving the
        # color's own alpha (e.g. `hsla(#5F59)` →
        # `hsla(120, 100%, 66.66666667%, 0.6)`).
        a_unw = unwrap(args[0])
        if isinstance(a_unw, Color):
            return _make_color(a_unw.rgb, a_unw.alpha, 'hsla', a_unw.index)
    modern = _modern_color_components(args, 3)
    if modern is not None:
        channels, alpha_node = modern
        h = number_value(channels[0], fn_name='hsla')
        s = _clamp01(number_value(channels[1], fn_name='hsla'))
        lum = _clamp01(number_value(channels[2], fn_name='hsla'))
        rgb = hsl_to_rgb(h, s, lum)
        a = 1.0 if alpha_node is None else _clamp01(number_value(alpha_node, fn_name='hsla'))
        return _make_color(rgb, a, 'hsla', args[0].index)
    if len(args) == 2:
        c = as_color(args[0], fn_name='hsla')
        a = _clamp01(number_value(args[1], fn_name='hsla'))
        return _make_color(c.rgb, a, 'hsla', c.index)
    if len(args) != 4:
        raise ArgumentError('hsla() expects 2 or 4 arguments')
    h = number_value(args[0], fn_name='hsla')
    s = _clamp01(number_value(args[1], fn_name='hsla'))
    lum = _clamp01(number_value(args[2], fn_name='hsla'))
    a = _clamp01(number_value(args[3], fn_name='hsla'))
    rgb = hsl_to_rgb(h, s, lum)
    return _make_color(rgb, a, 'hsla', args[0].index)


@register('hsv')
def fn_hsv(args: list[Node], ctx: EvalContext) -> Node:
    return fn_hsva([*args, _dim(1.0)], ctx)


@register('hsva')
def fn_hsva(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) != 4:
        raise ArgumentError('hsva() expects 4 arguments')
    import math as _math

    h = ((number_value(args[0], fn_name='hsva') % 360) / 360) * 360
    s = number_value(args[1], fn_name='hsva')
    v = number_value(args[2], fn_name='hsva')
    a = number_value(args[3], fn_name='hsva')
    i = int(_math.floor((h / 60) % 6))
    f = (h / 60) - i
    vs = [v, v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)]
    perm = [(0, 3, 1), (2, 0, 1), (1, 0, 3), (1, 2, 0), (3, 1, 0), (0, 1, 2)]
    rgb = (vs[perm[i][0]] * 255, vs[perm[i][1]] * 255, vs[perm[i][2]] * 255)
    return _make_color(rgb, a, 'rgba' if a < 1 else 'rgb', args[0].index)


def _dim(v: float, unit: str = '') -> Dimension:
    return Dimension(index=0, value=v, unit=unit)


@register('hue')
def fn_hue(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='hue')
    h, _, _ = rgb_to_hsl(*c.rgb)
    return _dim(h)


@register('saturation')
def fn_saturation(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='saturation')
    _, s, _ = rgb_to_hsl(*c.rgb)
    return _dim(s * 100, '%')


@register('lightness')
def fn_lightness(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='lightness')
    _, _, lum = rgb_to_hsl(*c.rgb)
    return _dim(lum * 100, '%')


@register('hsvhue')
def fn_hsvhue(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='hsvhue')
    h, _, _ = rgb_to_hsv(*c.rgb)
    return _dim(h)


@register('hsvsaturation')
def fn_hsvsaturation(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='hsvsaturation')
    _, s, _ = rgb_to_hsv(*c.rgb)
    return _dim(s * 100, '%')


@register('hsvvalue')
def fn_hsvvalue(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='hsvvalue')
    _, _, v = rgb_to_hsv(*c.rgb)
    return _dim(v * 100, '%')


@register('red')
def fn_red(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='red')
    return _dim(c.rgb[0])


@register('green')
def fn_green(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='green')
    return _dim(c.rgb[1])


@register('blue')
def fn_blue(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='blue')
    return _dim(c.rgb[2])


@register('alpha')
def fn_alpha(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='alpha')
    return _dim(c.alpha)


@register('luma')
def fn_luma(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='luma')
    return _dim(luma(c.rgb) * c.alpha * 100, '%')


@register('luminance')
def fn_luminance(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='luminance')
    lum = 0.2126 * c.rgb[0] / 255 + 0.7152 * c.rgb[1] / 255 + 0.0722 * c.rgb[2] / 255
    return _dim(lum * c.alpha * 100, '%')


def _adjust_hsl(args: list[Node], channel: str, sign: int, fn_name: str) -> Node:
    """Common path for lighten/darken/saturate/desaturate/fadein/fadeout.

    `channel` is one of 'h', 's', 'l', 'a'. `sign` is +1 (lighten/saturate/
    fadein) or -1 (darken/desaturate/fadeout). A third arg `relative` (the
    Keyword `relative`) switches to a multiplicative delta — less.js
    behaves the same way.
    """
    if len(args) < 2:
        raise ArgumentError(f'{fn_name}() expects at least 2 arguments')
    # Color-CONSUMING helpers (lighten/darken/saturate/...) escalate
    # `var(--x)` / Anonymous inputs to a fatal RuntimeError so the call
    # surfaces a clear error rather than round-tripping silently.
    c = as_color(args[0], fn_name=fn_name, strict=True)
    amount = as_dimension(args[1], fn_name=fn_name)
    relative = len(args) >= 3 and keyword_value(args[2]) == 'relative'
    h, s, lum = rgb_to_hsl(*c.rgb)
    a = c.alpha
    delta = sign * amount.value / 100.0
    if channel == 's':
        s = _clamp01(s + (s * delta if relative else delta))
    elif channel == 'l':
        lum = _clamp01(lum + (lum * delta if relative else delta))
    elif channel == 'a':
        a = _clamp01(a + (a * delta if relative else delta))
    rgb = hsl_to_rgb(h, s, lum)
    return _hsla_like(c, rgb, a)


def _hsla_like(orig: Color, rgb: tuple[float, float, float], alpha: float) -> Color:
    """Build a new color with channel form matching `orig`. less.js's `hsla`
    helper preserves rgb/hsl-form distinction; this mirrors that logic.
    """
    cf = orig.color_function
    if cf in ('rgb', 'rgba'):
        out_cf = cf
    elif cf in ('hsl', 'hsla'):
        out_cf = cf
    else:
        out_cf = 'rgb'
    return _make_color(rgb, alpha, out_cf, orig.index)


@register('lighten')
def fn_lighten(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl(args, 'l', +1, 'lighten')


@register('darken')
def fn_darken(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl(args, 'l', -1, 'darken')


@register('saturate')
def fn_saturate(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl(args, 's', +1, 'saturate')


@register('desaturate')
def fn_desaturate(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl(args, 's', -1, 'desaturate')


@register('fadein')
def fn_fadein(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl(args, 'a', +1, 'fadein')


@register('fadeout')
def fn_fadeout(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl(args, 'a', -1, 'fadeout')


@register('fade')
def fn_fade(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) != 2:
        raise ArgumentError('fade() expects 2 arguments')
    c = as_color(args[0], fn_name='fade')
    amount = as_dimension(args[1], fn_name='fade')
    a = _clamp01(amount.value / 100.0)
    return _hsla_like(c, c.rgb, a)


@register('spin')
def fn_spin(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) != 2:
        raise ArgumentError('spin() expects 2 arguments')
    c = as_color(args[0], fn_name='spin')
    amount = as_dimension(args[1], fn_name='spin')
    h, s, lum = rgb_to_hsl(*c.rgb)
    new_h = (h + amount.value) % 360
    if new_h < 0:
        new_h += 360
    rgb = hsl_to_rgb(new_h, s, lum)
    return _hsla_like(c, rgb, c.alpha)


@register('mix')
def fn_mix(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) < 2 or len(args) > 3:
        raise ArgumentError('mix() expects 2 or 3 arguments')
    c1 = as_color(args[0], fn_name='mix')
    c2 = as_color(args[1], fn_name='mix')
    if len(args) == 3:
        weight = as_dimension(args[2], fn_name='mix').value
    else:
        weight = 50.0
    p = weight / 100.0
    w = p * 2 - 1
    a = c1.alpha - c2.alpha
    w1 = ((w if w * a == -1 else (w + a) / (1 + w * a)) + 1) / 2.0
    w2 = 1 - w1
    rgb = (
        c1.rgb[0] * w1 + c2.rgb[0] * w2,
        c1.rgb[1] * w1 + c2.rgb[1] * w2,
        c1.rgb[2] * w1 + c2.rgb[2] * w2,
    )
    alpha = c1.alpha * p + c2.alpha * (1 - p)
    return _make_color(rgb, alpha, '', c1.index)


@register('greyscale')
def fn_greyscale(args: list[Node], ctx: EvalContext) -> Node:
    return _adjust_hsl([args[0], _dim(100)], 's', -1, 'greyscale')


@register('grayscale')
def fn_grayscale(args: list[Node], ctx: EvalContext) -> Node:
    return fn_greyscale(args, ctx)


@register('tint')
def fn_tint(args: list[Node], ctx: EvalContext) -> Node:
    white = _make_color((255, 255, 255), 1.0, 'rgb')
    return fn_mix([white, *args], ctx)


@register('shade')
def fn_shade(args: list[Node], ctx: EvalContext) -> Node:
    black = _make_color((0, 0, 0), 1.0, 'rgb')
    return fn_mix([black, *args], ctx)


@register('contrast')
def fn_contrast(args: list[Node], ctx: EvalContext) -> Node:
    if not args:
        raise ArgumentError('contrast() expects at least 1 argument')
    color = as_color(args[0], fn_name='contrast')
    dark = as_color(args[1], fn_name='contrast') if len(args) >= 2 else _make_color((0, 0, 0), 1.0, 'rgb')
    light = as_color(args[2], fn_name='contrast') if len(args) >= 3 else _make_color((255, 255, 255), 1.0, 'rgb')
    if luma(dark.rgb) > luma(light.rgb):
        dark, light = light, dark
    threshold = number_value(args[3], fn_name='contrast') if len(args) >= 4 else 0.43
    return light if luma(color.rgb) * color.alpha < threshold else dark


# Color blending: ported from less.js's `functions/color-blending.js`
# (W3C compositing-1 spec plus the two non-W3C additions `average` and
# `negation`).


def _color_blend(mode: Callable[[float, float], float], c1: Color, c2: Color) -> Color:
    ab = c1.alpha  # backdrop alpha
    as_ = c2.alpha  # source alpha
    ar = as_ + ab * (1 - as_)  # result alpha
    r: list[float] = []
    for i in range(3):
        cb = c1.rgb[i] / 255
        cs = c2.rgb[i] / 255
        cr = mode(cb, cs)
        if ar:
            cr = (as_ * cs + ab * (cb - as_ * (cb + cs - cr))) / ar
        r.append(cr * 255)
    return _make_color((r[0], r[1], r[2]), ar, '', c1.index)


def _bm_multiply(cb: float, cs: float) -> float:
    return cb * cs


def _bm_screen(cb: float, cs: float) -> float:
    return cb + cs - cb * cs


def _bm_overlay(cb: float, cs: float) -> float:
    cb *= 2
    return _bm_multiply(cb, cs) if cb <= 1 else _bm_screen(cb - 1, cs)


def _bm_softlight(cb: float, cs: float) -> float:
    d = 1.0
    e = cb
    if cs > 0.5:
        e = 1.0
        d = _math.sqrt(cb) if cb > 0.25 else ((16 * cb - 12) * cb + 4) * cb
    return cb - (1 - 2 * cs) * e * (d - cb)


def _bm_hardlight(cb: float, cs: float) -> float:
    return _bm_overlay(cs, cb)


def _bm_difference(cb: float, cs: float) -> float:
    return abs(cb - cs)


def _bm_exclusion(cb: float, cs: float) -> float:
    return cb + cs - 2 * cb * cs


def _bm_average(cb: float, cs: float) -> float:
    return (cb + cs) / 2


def _bm_negation(cb: float, cs: float) -> float:
    return 1 - abs(cb + cs - 1)


def _register_blend(name: str, mode: Callable[[float, float], float]) -> None:
    def fn(args: list[Node], ctx: EvalContext, _mode: Callable[[float, float], float] = mode) -> Node:
        if len(args) != 2:
            raise ArgumentError(f'{name}() expects 2 arguments')
        c1 = as_color(args[0], fn_name=name)
        c2 = as_color(args[1], fn_name=name)
        return _color_blend(_mode, c1, c2)

    fn.__name__ = f'fn_{name}'
    register(name)(fn)


_register_blend('multiply', _bm_multiply)
_register_blend('screen', _bm_screen)
_register_blend('overlay', _bm_overlay)
_register_blend('softlight', _bm_softlight)
_register_blend('hardlight', _bm_hardlight)
_register_blend('difference', _bm_difference)
_register_blend('exclusion', _bm_exclusion)
_register_blend('average', _bm_average)
_register_blend('negation', _bm_negation)


@register('argb')
def fn_argb(args: list[Node], ctx: EvalContext) -> Node:
    c = as_color(args[0], fn_name='argb')
    a = int(round(c.alpha * 255))
    channels = (a, clamp_channel(c.rgb[0]), clamp_channel(c.rgb[1]), clamp_channel(c.rgb[2]))
    hex_str = '#' + ''.join(f'{x:02x}' for x in channels)
    return Anonymous(index=c.index, value=hex_str)


@register('color')
def fn_color(args: list[Node], ctx: EvalContext) -> Node:
    if len(args) != 1:
        raise ArgumentError('color() expects 1 argument')
    a = unwrap(args[0])
    # Quoted hex literal: `color("#fff")`. Preserve the source hex form
    # by carrying it through Color.value — format_color() rounds back to
    # the original text when channels match (G3). For a named-color
    # input (`color("plum")`) less.js emits the hex form (`#dda0dd`),
    # not the name — drop `value` so the formatter renders from `rgb`.
    if hasattr(a, 'value') and isinstance(getattr(a, 'value'), str):
        text = quoted_value(a, fn_name='color').value if a.__class__.__name__ == 'Quoted' else None
        if text is not None:
            parsed = parse_color(text)
            if parsed is not None:
                rgb, alpha = parsed
                preserve = text if text.startswith('#') else ''
                return Color(index=a.index, value=preserve, rgb=rgb, alpha=alpha, color_function='')
            # Quoted input that doesn't parse as a CSS color — fatal.
            err = ArgumentError('argument must be a color keyword or 3|4|6|8 digit hex e.g. #FFF')
            err._fatal = True
            raise err
    c = as_color(args[0], fn_name='color')
    return _make_color(c.rgb, c.alpha, '', c.index)
