"""Color helpers: named-color table, hex parsing, RGB↔HSL/HSV math, and
the format function used to render a `Color` back to CSS text.

Color storage in this package uses the AST `Color` node directly:
  * `value` — the original textual form (`#fff`, `red`, `rgba(...)`).
    Preserved so that a `Color` that didn't undergo arithmetic round-trips
    verbatim.
  * `rgb` — float triple in 0..255 range. Floats (not ints) because color
    operations produce fractional channels; rounding happens only at emit.
  * `alpha` — 0..1.
  * `color_function` — '', 'rgb', 'rgba', 'hsl', or 'hsla'. Set by
    constructor functions (`rgba(...)`, `hsl(...)`); read by `format_color`
    to decide which CSS form to emit. Empty string means "hex/keyword
    form", which is what hex literals start out as.

Operations on colors clear the original `value` (we no longer have a
verbatim form to preserve) and clear `color_function` to '' so the result
emits as hex. less.js does the same.
"""

from __future__ import annotations

import re

# CSS named colors (verbatim from `node_modules/less/lib/less/data/colors.js`).
# Lowercase keys. Values are 6-digit hex without leading `#`.
CSS_NAMED_COLORS: dict[str, str] = {
    'aliceblue': 'f0f8ff',
    'antiquewhite': 'faebd7',
    'aqua': '00ffff',
    'aquamarine': '7fffd4',
    'azure': 'f0ffff',
    'beige': 'f5f5dc',
    'bisque': 'ffe4c4',
    'black': '000000',
    'blanchedalmond': 'ffebcd',
    'blue': '0000ff',
    'blueviolet': '8a2be2',
    'brown': 'a52a2a',
    'burlywood': 'deb887',
    'cadetblue': '5f9ea0',
    'chartreuse': '7fff00',
    'chocolate': 'd2691e',
    'coral': 'ff7f50',
    'cornflowerblue': '6495ed',
    'cornsilk': 'fff8dc',
    'crimson': 'dc143c',
    'cyan': '00ffff',
    'darkblue': '00008b',
    'darkcyan': '008b8b',
    'darkgoldenrod': 'b8860b',
    'darkgray': 'a9a9a9',
    'darkgrey': 'a9a9a9',
    'darkgreen': '006400',
    'darkkhaki': 'bdb76b',
    'darkmagenta': '8b008b',
    'darkolivegreen': '556b2f',
    'darkorange': 'ff8c00',
    'darkorchid': '9932cc',
    'darkred': '8b0000',
    'darksalmon': 'e9967a',
    'darkseagreen': '8fbc8f',
    'darkslateblue': '483d8b',
    'darkslategray': '2f4f4f',
    'darkslategrey': '2f4f4f',
    'darkturquoise': '00ced1',
    'darkviolet': '9400d3',
    'deeppink': 'ff1493',
    'deepskyblue': '00bfff',
    'dimgray': '696969',
    'dimgrey': '696969',
    'dodgerblue': '1e90ff',
    'firebrick': 'b22222',
    'floralwhite': 'fffaf0',
    'forestgreen': '228b22',
    'fuchsia': 'ff00ff',
    'gainsboro': 'dcdcdc',
    'ghostwhite': 'f8f8ff',
    'gold': 'ffd700',
    'goldenrod': 'daa520',
    'gray': '808080',
    'grey': '808080',
    'green': '008000',
    'greenyellow': 'adff2f',
    'honeydew': 'f0fff0',
    'hotpink': 'ff69b4',
    'indianred': 'cd5c5c',
    'indigo': '4b0082',
    'ivory': 'fffff0',
    'khaki': 'f0e68c',
    'lavender': 'e6e6fa',
    'lavenderblush': 'fff0f5',
    'lawngreen': '7cfc00',
    'lemonchiffon': 'fffacd',
    'lightblue': 'add8e6',
    'lightcoral': 'f08080',
    'lightcyan': 'e0ffff',
    'lightgoldenrodyellow': 'fafad2',
    'lightgray': 'd3d3d3',
    'lightgrey': 'd3d3d3',
    'lightgreen': '90ee90',
    'lightpink': 'ffb6c1',
    'lightsalmon': 'ffa07a',
    'lightseagreen': '20b2aa',
    'lightskyblue': '87cefa',
    'lightslategray': '778899',
    'lightslategrey': '778899',
    'lightsteelblue': 'b0c4de',
    'lightyellow': 'ffffe0',
    'lime': '00ff00',
    'limegreen': '32cd32',
    'linen': 'faf0e6',
    'magenta': 'ff00ff',
    'maroon': '800000',
    'mediumaquamarine': '66cdaa',
    'mediumblue': '0000cd',
    'mediumorchid': 'ba55d3',
    'mediumpurple': '9370db',
    'mediumseagreen': '3cb371',
    'mediumslateblue': '7b68ee',
    'mediumspringgreen': '00fa9a',
    'mediumturquoise': '48d1cc',
    'mediumvioletred': 'c71585',
    'midnightblue': '191970',
    'mintcream': 'f5fffa',
    'mistyrose': 'ffe4e1',
    'moccasin': 'ffe4b5',
    'navajowhite': 'ffdead',
    'navy': '000080',
    'oldlace': 'fdf5e6',
    'olive': '808000',
    'olivedrab': '6b8e23',
    'orange': 'ffa500',
    'orangered': 'ff4500',
    'orchid': 'da70d6',
    'palegoldenrod': 'eee8aa',
    'palegreen': '98fb98',
    'paleturquoise': 'afeeee',
    'palevioletred': 'db7093',
    'papayawhip': 'ffefd5',
    'peachpuff': 'ffdab9',
    'peru': 'cd853f',
    'pink': 'ffc0cb',
    'plum': 'dda0dd',
    'powderblue': 'b0e0e6',
    'purple': '800080',
    'rebeccapurple': '663399',
    'red': 'ff0000',
    'rosybrown': 'bc8f8f',
    'royalblue': '4169e1',
    'saddlebrown': '8b4513',
    'salmon': 'fa8072',
    'sandybrown': 'f4a460',
    'seagreen': '2e8b57',
    'seashell': 'fff5ee',
    'sienna': 'a0522d',
    'silver': 'c0c0c0',
    'skyblue': '87ceeb',
    'slateblue': '6a5acd',
    'slategray': '708090',
    'slategrey': '708090',
    'snow': 'fffafa',
    'springgreen': '00ff7f',
    'steelblue': '4682b4',
    'tan': 'd2b48c',
    'teal': '008080',
    'thistle': 'd8bfd8',
    'tomato': 'ff6347',
    'turquoise': '40e0d0',
    'violet': 'ee82ee',
    'wheat': 'f5deb3',
    'white': 'ffffff',
    'whitesmoke': 'f5f5f5',
    'yellow': 'ffff00',
    'yellowgreen': '9acd32',
}


_HEX_RE = re.compile(r'^#?([0-9a-fA-F]+)$')


def parse_hex(text: str) -> tuple[tuple[float, float, float], float] | None:
    """Parse a `#RGB`, `#RGBA`, `#RRGGBB`, or `#RRGGBBAA` literal.

    Returns ((r, g, b), alpha) with channels in 0..255 and alpha in 0..1.
    Accepts the leading `#` optional. Returns None on malformed input.
    """
    m = _HEX_RE.match(text)
    if m is None:
        return None
    digits = m.group(1)
    if len(digits) == 3:
        r, g, b = (int(c * 2, 16) for c in digits)
        return ((float(r), float(g), float(b)), 1.0)
    if len(digits) == 4:
        r, g, b, a = (int(c * 2, 16) for c in digits)
        return ((float(r), float(g), float(b)), a / 255.0)
    if len(digits) == 6:
        r = int(digits[0:2], 16)
        g = int(digits[2:4], 16)
        b = int(digits[4:6], 16)
        return ((float(r), float(g), float(b)), 1.0)
    if len(digits) == 8:
        r = int(digits[0:2], 16)
        g = int(digits[2:4], 16)
        b = int(digits[4:6], 16)
        a = int(digits[6:8], 16)
        return ((float(r), float(g), float(b)), a / 255.0)
    return None


def parse_keyword(name: str) -> tuple[tuple[float, float, float], float] | None:
    """Resolve a CSS named-color keyword. Returns None if unknown."""
    key = name.lower()
    if key in CSS_NAMED_COLORS:
        hex6 = CSS_NAMED_COLORS[key]
        return ((float(int(hex6[0:2], 16)), float(int(hex6[2:4], 16)), float(int(hex6[4:6], 16))), 1.0)
    if key == 'transparent':
        return ((0.0, 0.0, 0.0), 0.0)
    return None


def parse_color(text: str) -> tuple[tuple[float, float, float], float] | None:
    """Try hex first, then keyword. Returns None if neither applies."""
    if text.startswith('#'):
        return parse_hex(text)
    return parse_keyword(text)


def clamp_channel(c: float) -> int:
    """Round, then clamp to 0..255. Mirrors less.js's `clamp(Math.round(c), 255)`.
    Uses half-away-from-zero rounding (`js_round`) for byte-exact parity.
    """
    return min(255, max(0, js_round(c)))


def js_round(x: float) -> int:
    """JavaScript's `Math.round`: half rounds toward +∞ (not banker's).
    Python's `round()` is banker's, so we re-implement.
    """
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert 0..255 RGB to HSL with H in 0..360, S/L in 0..1.

    Port of less.js `Color.toHSL` (formula-by-formula, same operation
    order) so that HSL-cycle round trips agree byte-exact.
    """
    r1, g1, b1 = r / 255.0, g / 255.0, b / 255.0
    mx = max(r1, g1, b1)
    mn = min(r1, g1, b1)
    h = 0.0
    s = 0.0
    lum = (mx + mn) / 2.0
    if mx != mn:
        d = mx - mn
        s = d / (2.0 - mx - mn) if lum > 0.5 else d / (mx + mn)
        if mx == r1:
            h = (g1 - b1) / d + (6.0 if g1 < b1 else 0.0)
        elif mx == g1:
            h = (b1 - r1) / d + 2.0
        else:
            h = (r1 - g1) / d + 4.0
        h /= 6.0
    return (h * 360.0, s, lum)


def hsl_to_rgb(h: float, s: float, lum: float) -> tuple[float, float, float]:
    """Inverse of `rgb_to_hsl`. H in 0..360, S/L in 0..1; returns 0..255 RGB.

    Port of less.js `colorFunctions.hsla` operation-for-operation.
    """
    h_norm = (h % 360.0) / 360.0
    s = max(0.0, min(1.0, s))
    lum = max(0.0, min(1.0, lum))
    m2 = lum * (s + 1.0) if lum <= 0.5 else lum + s - lum * s
    m1 = lum * 2.0 - m2

    def hue(hh: float) -> float:
        if hh < 0:
            hh += 1.0
        elif hh > 1:
            hh -= 1.0
        if hh * 6.0 < 1.0:
            return m1 + (m2 - m1) * hh * 6.0
        if hh * 2.0 < 1.0:
            return m2
        if hh * 3.0 < 2.0:
            return m1 + (m2 - m1) * (2.0 / 3.0 - hh) * 6.0
        return m1

    r = hue(h_norm + 1.0 / 3.0) * 255.0
    g = hue(h_norm) * 255.0
    b = hue(h_norm - 1.0 / 3.0) * 255.0
    return (r, g, b)


def rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert 0..255 RGB to HSV. H in 0..360, S/V in 0..1.

    Port of less.js `Color.toHSV` operation-for-operation.
    """
    r1, g1, b1 = r / 255.0, g / 255.0, b / 255.0
    mx = max(r1, g1, b1)
    mn = min(r1, g1, b1)
    v = mx
    s = 0.0 if mx == 0 else (mx - mn) / mx
    h = 0.0
    if mx != mn:
        d = mx - mn
        if mx == r1:
            h = (g1 - b1) / d + (6.0 if g1 < b1 else 0.0)
        elif mx == g1:
            h = (b1 - r1) / d + 2.0
        else:
            h = (r1 - g1) / d + 4.0
        h /= 6.0
    return (h * 360.0, s, v)


def luma(rgb: tuple[float, float, float]) -> float:
    """Linearized luminance per WCAG 2.0. Used by `contrast()`."""

    def linearize(c: float) -> float:
        c1 = c / 255.0
        return c1 / 12.92 if c1 <= 0.03928 else ((c1 + 0.055) / 1.055) ** 2.4

    r, g, b = (linearize(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def to_hex(rgb: tuple[float, float, float]) -> str:
    """Format `rgb` as `#rrggbb`. Channels are rounded and clamped first."""
    return '#' + ''.join(f'{clamp_channel(c):02x}' for c in rgb)


def format_color(rgb: tuple[float, float, float], alpha: float, color_function: str, original: str) -> str:
    """Render a color back to CSS text.

    Mirrors less.js's `Color.toCSS`: `color_function` records *which*
    constructor (rgb/hsl) produced the color, but it doesn't directly
    pick the emit form — that's chosen by alpha. The rules:
      * rgb-family + alpha=1 → hex; alpha<1 → rgba(...).
      * hsl-family + alpha=1 → hsl(...); alpha<1 → hsla(...).
      * no family (hex literal or arithmetic result) + alpha=1 → if the
        original text still maps to these channels exactly, keep it
        verbatim; otherwise hex. alpha<1 → rgba(...).
    """
    alpha = round(alpha, 6)
    if color_function in ('rgb', 'rgba'):
        if alpha < 1:
            return _format_rgba(rgb, alpha)
        return to_hex(rgb)
    if color_function in ('hsl', 'hsla'):
        if alpha < 1:
            return _format_hsla(rgb, alpha)
        return _format_hsl(rgb)
    if original:
        # Round-trip source-preserved hex (incl. 8-digit `#RRGGBBAA`)
        # when the channels and alpha still match — covers
        # `color('#55FF5599')` and unmodified short-hex literals.
        parsed = parse_color(original)
        if parsed is not None:
            (orig_rgb, orig_a) = parsed
            if _channels_close(orig_rgb, rgb) and abs(orig_a - alpha) < 1e-9:
                return original
    if alpha < 1:
        return _format_rgba(rgb, alpha)
    return to_hex(rgb)


def _format_rgba(rgb: tuple[float, float, float], alpha: float) -> str:
    return f'rgba({clamp_channel(rgb[0])}, {clamp_channel(rgb[1])}, {clamp_channel(rgb[2])}, {_format_alpha(alpha)})'


def _format_hsl(rgb: tuple[float, float, float]) -> str:
    h, s, lum = rgb_to_hsl(*rgb)
    return f'hsl({_format_num(h)}, {_format_pct(s * 100)}%, {_format_pct(lum * 100)}%)'


def _format_hsla(rgb: tuple[float, float, float], alpha: float) -> str:
    h, s, lum = rgb_to_hsl(*rgb)
    return f'hsla({_format_num(h)}, {_format_pct(s * 100)}%, {_format_pct(lum * 100)}%, {_format_alpha(alpha)})'


def _format_alpha(alpha: float) -> str:
    """Render alpha 0..1 with up to 6 decimals; drop trailing zeros."""
    if alpha == int(alpha):
        return str(int(alpha))
    return f'{alpha:.6f}'.rstrip('0').rstrip('.')


def _format_num(n: float) -> str:
    """Render a float with up to 6 decimals; drop trailing zeros."""
    if n == int(n):
        return str(int(n))
    return f'{n:.6f}'.rstrip('0').rstrip('.')


def _format_pct(n: float) -> str:
    """Render an HSL saturation / lightness percentage (n already in 0..100)
    with up to 8 decimals; drop trailing zeros. less.js emits these
    channels at higher precision than hue or alpha — e.g.
    `hsla(120, 100%, 66.66666667%, 0.6)`.
    """
    if n == int(n):
        return str(int(n))
    return f'{n:.8f}'.rstrip('0').rstrip('.')


def _channels_close(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b, strict=True))
