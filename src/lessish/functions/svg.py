"""`svg-gradient(direction, color [position]?, ..., color [position]?)` —
generates a `url("data:image/svg+xml,...")` value.

less.js validates the direction keyword and the stop sequence; bad
input raises `ArgumentError` with a specific message that the evaluator
wraps with an `Error evaluating function 'svg-gradient':` prefix. The
validation mirrors less.js for parity.
"""

from __future__ import annotations

import urllib.parse

from ..ast_nodes import Color, Dimension, Expression, Keyword, Node, Paren, Url, Value
from ..colors import parse_color, to_hex
from ..errors import ArgumentError
from ..functions import register

# Direction keyword → (gradient_type, x1y1x2y2_or_cx_cy_r). All linear except
# `ellipse at center` which is radial. Coords match less.js's `svg.js`:
# linear takes four percentages, radial takes (cx,cy,r) with r=75% so the
# gradient bleeds past the viewBox.
_DIRECTIONS: dict[str, tuple[str, str]] = {
    'to bottom': ('linear', '0%,0%,0%,100%'),
    'to right': ('linear', '0%,0%,100%,0%'),
    'to bottom right': ('linear', '0%,0%,100%,100%'),
    'to top right': ('linear', '0%,100%,100%,0%'),
    'ellipse at center': ('radial', '50%,50%,75%'),
}

_DIRECTION_ERR = (
    "svg-gradient direction must be 'to bottom', 'to right', 'to bottom right', 'to top right' or 'ellipse at center'"
)
_STOPS_ERR = (
    'svg-gradient expects direction, start_color [start_position], '
    '[color position,]..., end_color [end_position] or direction, color list'
)


@register('svg-gradient')
def svg_gradient(args: list[Node], _ctx: object) -> Url:
    if not args:
        # Lenient on count-only: less.js's validator hits the direction
        # check first. Raise the direction error to match.
        raise _direction_err()

    # The first arg is the direction keyword, possibly a multi-word
    # phrase: `to bottom right` parses as Expression([Keyword('to'),
    # Keyword('bottom'), Keyword('right')]).
    direction_text = _stringify_keywords(args[0])
    if direction_text not in _DIRECTIONS:
        raise _direction_err()

    stops_args = args[1:]
    if not stops_args:
        raise _stops_err()

    # Flatten the stop arguments into (Color, position?) pairs. Each
    # remaining arg can be:
    #   * a bare Color
    #   * an Expression([Color, Dimension/percent]) — `red 50%`
    #   * a comma-list (Value) — when invoked as `svg-gradient(dir, @list)`
    flat_stops: list[Node] = []
    for a in stops_args:
        flat_stops.extend(_flatten_arg(a))
    stops = _build_stops(flat_stops)
    if len(stops) < 2:
        raise _stops_err()

    grad_kind, coords = _DIRECTIONS[direction_text]
    svg = _render_svg(grad_kind, coords, stops)
    # less.js uses `encodeURIComponent` which keeps `!~*'()` literal —
    # match by passing those as `safe`. Parens specifically need to stay
    # unescaped so `fill="url(#g)"` round-trips.
    encoded = urllib.parse.quote(svg, safe="!~*'()")
    return Url(index=0, value=f"'data:image/svg+xml,{encoded}'")


def _direction_err() -> ArgumentError:
    err = ArgumentError(_DIRECTION_ERR)
    err._fatal = True
    return err


def _stops_err() -> ArgumentError:
    err = ArgumentError(_STOPS_ERR)
    err._fatal = True
    return err


def _stringify_keywords(node: Node) -> str:
    """`to bottom right` may come in as Expression of Keywords; a single
    keyword as bare Keyword. Anything else (e.g. a Color) yields a
    non-matching string so the validator complains.
    """
    if isinstance(node, Keyword):
        return node.value
    if isinstance(node, Expression):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, Keyword):
                parts.append(v.value)
            else:
                return ''  # non-keyword inside — direction is invalid
        return ' '.join(parts)
    return ''


def _flatten_arg(node: Node) -> list[Node]:
    """Walk an arg that might itself be a comma-list (Value) — produced
    when `svg-gradient(dir, @colorList)` is called and @colorList is a
    comma-list variable. Returns one entry per comma-separated piece.
    """
    if isinstance(node, Value):
        return list(node.expressions)
    return [node]


def _build_stops(items: list[Node]) -> list[tuple[Color, str]]:
    """Parse the alternating-color/position list into a sequence of
    (Color, position-text) tuples. less.js's rule: each entry is either
    a Color, a Color followed by a position, or a position attached to
    the previous color. If the first item isn't a Color, raise.

    `Keyword('orange')` and other named-color identifiers are promoted
    via the CSS named-color table — less.js accepts them at stop
    positions interchangeably with hex/rgb forms.
    """
    stops: list[tuple[Color, str]] = []
    for item in items:
        unwrapped = _unwrap(item)
        unwrapped = _coerce_color(unwrapped)
        if isinstance(unwrapped, Color):
            stops.append((unwrapped, ''))
        elif isinstance(unwrapped, Expression):
            color_part: Color | None = None
            pos_part = ''
            for v in unwrapped.values:
                v = _coerce_color(_unwrap(v))
                if isinstance(v, Color) and color_part is None:
                    color_part = v
                elif isinstance(v, Dimension) and color_part is not None:
                    pos_part = _format_dim(v)
                else:
                    raise _stops_err()
            if color_part is None:
                raise _stops_err()
            stops.append((color_part, pos_part))
        elif isinstance(unwrapped, Dimension):
            # Bare position separated from its color — that's invalid in
            # less.js (a 45% between two colors with no attachment).
            raise _stops_err()
        else:
            raise _stops_err()
    return stops


def _coerce_color(node: Node) -> Node:
    """Promote a `Keyword` naming a CSS color to a `Color`. Other nodes
    pass through unchanged. Mirrors the evaluator's
    `_maybe_promote_color` without dragging in the eval module.
    """
    if not isinstance(node, Keyword):
        return node
    parsed = parse_color(node.value)
    if parsed is None:
        return node
    rgb, alpha = parsed
    return Color(index=node.index, value=node.value, rgb=rgb, alpha=alpha, color_function='')


def _unwrap(node: Node) -> Node:
    """Drop trivial wrappers around a value: single-expression `Value`,
    single-value `Expression`, and `Paren` (`(((@x)))` and `@x` are
    the same to a stop parser).
    """
    while True:
        if isinstance(node, Value) and len(node.expressions) == 1:
            node = node.expressions[0]
        elif isinstance(node, Expression) and len(node.values) == 1:
            node = node.values[0]
        elif isinstance(node, Paren):
            node = node.value
        else:
            return node


def _format_dim(d: Dimension) -> str:
    if d.value == int(d.value):
        return f'{int(d.value)}{d.unit}'
    return f'{d.value}{d.unit}'


def _render_svg(kind: str, coords: str, stops: list[tuple[Color, str]]) -> str:
    """Build the SVG text in the exact byte-shape less.js's `svg.js`
    produces — no XML prologue, no `<defs>` wrapper, hex stop colors
    with a separate `stop-opacity` attribute when alpha < 1, and a
    space between `fill="url(#g)"` and the rect's self-close `/>`.
    """
    n = len(stops)
    stop_lines: list[str] = []
    for i, (color, pos) in enumerate(stops):
        if pos:
            offset = pos
        elif n > 1:
            offset = f'{int(i / (n - 1) * 100)}%'
        else:
            offset = '0%'
        hex_color = to_hex(color.rgb)
        opacity_attr = '' if color.alpha == 1 else f' stop-opacity="{_format_alpha(color.alpha)}"'
        stop_lines.append(f'<stop offset="{offset}" stop-color="{hex_color}"{opacity_attr}/>')
    if kind == 'linear':
        x1, y1, x2, y2 = coords.split(',')
        body = f'<linearGradient id="g" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">{"".join(stop_lines)}</linearGradient>'
        rect = '<rect x="0" y="0" width="1" height="1" fill="url(#g)" />'
    else:
        cx, cy, r = coords.split(',')
        body = f'<radialGradient id="g" cx="{cx}" cy="{cy}" r="{r}">{"".join(stop_lines)}</radialGradient>'
        # Radial bleeds past the viewBox — less.js renders a 101×101 rect
        # offset by (-50, -50) so the gradient fills the entire shape.
        rect = '<rect x="-50" y="-50" width="101" height="101" fill="url(#g)" />'
    svg_open = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
    return f'{svg_open}{body}{rect}</svg>'


def _format_alpha(alpha: float) -> str:
    """Render alpha 0..1 like less.js: integers stay integers (`0`, `1`),
    fractions use the minimal decimal representation (`0.5`, `0.05`).
    """
    if alpha == int(alpha):
        return str(int(alpha))
    return f'{alpha:.6f}'.rstrip('0').rstrip('.')
