"""Guard comparison: `compare(a, b) -> -1 | 0 | 1 | None`.

`None` is the "cannot compare" signal — used when types are incompatible
(`red < 5px` is neither true nor false, it's "no match" and a `when`
clause containing it falls through). The evaluator wraps `compare`-based
operators (`<`, `<=`, `>`, `>=`, `=`, `<>`) and turns the integer result
into a boolean per the requested op.

Matches less.js's `Node.compare` family op-for-op: Dimension compares
after `unify()` (so `1in = 2.54cm` is true); Color compares per-channel;
Keyword/Quoted compare string-equal; cross-type returns None.
"""

from __future__ import annotations

from collections.abc import Sequence

from .ast_nodes import Anonymous, Color, Dimension, Expression, Keyword, Node, Quoted, Url, Value
from .units import convert, convertible
from .visitors import value_to_css


def compare(a: Node, b: Node) -> int | None:
    """Three-way ordered compare. -1/0/1 like `cmp`; None means "the
    types can't be compared, so no truth value".

    Order matters: `Dimension` first (the common case in guards),
    then Color (only equality), then Keyword / Quoted / Url.
    """
    if isinstance(a, Value) and isinstance(b, Value):
        return _compare_lists(a.expressions, b.expressions)
    if isinstance(a, Expression) and isinstance(b, Expression):
        return _compare_lists(a.values, b.values)
    if isinstance(a, Dimension) and isinstance(b, Dimension):
        return _compare_dimensions(a, b)
    if isinstance(a, Color) and isinstance(b, Color):
        return _compare_colors(a, b)
    # Dimension vs Anonymous — less.js Dimension.compare returns
    # undefined for non-Dimension other; the fallback string-equals the
    # serialised values (so `3 = ~"3"` is true but `5 < ~"4"` is not).
    if isinstance(a, Dimension) and isinstance(b, Anonymous):
        return _compare_dim_anon(a, b, swap=False)
    if isinstance(a, Anonymous) and isinstance(b, Dimension):
        return _compare_dim_anon(b, a, swap=True)
    result = _compare_textlike(a, b)
    if result is not None:
        return result
    # less.js Node.compare fallback: string-equal the rendered CSS text
    # (`a.toCSS() === b.toCSS()`). Covers cross-type equality like
    # `3 = ~"3"` (Dimension vs Quoted) and list-vs-escaped-string
    # (`@comma-list = ~"1, 2, 3"`). Skip pairs where neither side has
    # a meaningful CSS form (e.g. internal AST nodes without a `value`).
    if _has_css_form(a) and _has_css_form(b):
        return _css_eq(a, b)
    return None


def _has_css_form(n: Node) -> bool:
    return isinstance(n, Dimension | Color | Keyword | Quoted | Anonymous | Url | Expression | Value)


def _compare_lists(a: Sequence[Node], b: Sequence[Node]) -> int | None:
    """Element-wise list equality. Returns 0 when same length and every
    pair compares equal; None otherwise. less.js's Expression/Value
    compare delegates to `Node.compare(a[i], b[i])` for each pair —
    ordering between lists isn't defined, only equality.
    """
    if len(a) != len(b):
        return None
    for x, y in zip(a, b, strict=True):
        result = compare(x, y)
        if result != 0:
            return None
    return 0


def _compare_dim_anon(dim: Dimension, anon: Anonymous, *, swap: bool) -> int | None:
    """Equality-only fallback per less.js: ordering between Dimension
    and Anonymous returns None; equality is string-match on the
    serialised value.
    """
    return 0 if value_to_css(dim) == anon.value else None


def _compare_textlike(a: Node, b: Node) -> int | None:
    """Keyword/Quoted/Anonymous/Url equality. Less.js's per-node `compare`
    is asymmetric:
      * Quoted vs Quoted (both non-escaped) → string compare values
        (`'x' = "x"` is true).
      * Quoted with mismatched escape, or against a non-Quoted: fall
        back to `toCSS()` equality (`~"theme1" = theme1` is true since
        both render `theme1`; `"theme1" = theme1` is false because the
        quoted form keeps its quotes).
      * Keyword vs Keyword → string match on `value`.
      * Anonymous vs textlike → CSS-form equality.
    Anything else returns None ("not orderable / not equal").
    """
    if isinstance(a, Quoted) and isinstance(b, Quoted):
        if not a.escaped and not b.escaped:
            return _string_compare(a.value, b.value)
        return _css_eq(a, b)
    if isinstance(a, Quoted) or isinstance(b, Quoted):
        return _css_eq(a, b)
    if isinstance(a, Keyword) and isinstance(b, Keyword):
        # less.js Keyword.compare returns 0 or undefined (no ordering),
        # so `a < b` between two keywords is never true even if their
        # string forms differ.
        return 0 if a.value == b.value else None
    if _is_textlike(a) and _is_textlike(b):
        return _css_eq(a, b)
    return None


def _css_eq(a: Node, b: Node) -> int | None:
    return 0 if _to_css(a) == _to_css(b) else None


def _string_compare(a: str, b: str) -> int:
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _to_css(n: Node) -> str:
    if isinstance(n, Quoted):
        if n.escaped:
            return n.value
        return f'{n.quote}{n.value}{n.quote}'
    if isinstance(n, Dimension | Value | Expression):
        # Use the emitter's serialiser so `3 = ~"3"` matches less.js's
        # `Node.compare` fallback (string-equality on rendered CSS).
        return value_to_css(n)
    return _text(n)


def evaluate_op(op: str, cmp: int | None) -> bool:
    """Translate a `compare` result into a boolean for the given operator.

    None always means false — less.js's leaf condition returns false
    whenever the two sides can't be ordered. `<>`/`!=` is the only op
    where None counts as true (they aren't equal).
    """
    if op == '<>' or op == '!=':
        return cmp is None or cmp != 0
    if cmp is None:
        return False
    if op == '=':
        return cmp == 0
    if op == '<':
        return cmp < 0
    if op == '<=' or op == '=<':
        return cmp <= 0
    if op == '>':
        return cmp > 0
    if op == '>=' or op == '=>':
        return cmp >= 0
    return False


def _compare_dimensions(a: Dimension, b: Dimension) -> int | None:
    """Unit-aware compare. Unitless values match the other side. When
    units differ and both are in the same conversion group, convert `b`
    to `a`'s unit before comparing. Otherwise None.
    """
    if a.unit == b.unit or not a.unit or not b.unit:
        av = a.value
        bv = b.value
    elif convertible(a.unit, b.unit):
        av = a.value
        bv = convert(b.value, b.unit, a.unit)
    else:
        return None
    if av < bv:
        return -1
    if av > bv:
        return 1
    return 0


def _compare_colors(a: Color, b: Color) -> int | None:
    """Colors compare by exact RGB+alpha equality only. Ordering is
    undefined in Less and we follow suit."""
    if a.rgb == b.rgb and a.alpha == b.alpha:
        return 0
    return None


def _is_textlike(n: Node) -> bool:
    return isinstance(n, Keyword | Quoted | Anonymous | Url)


def _text(n: Node) -> str:
    if isinstance(n, Url):
        return n.value
    return n.value if hasattr(n, 'value') else ''
