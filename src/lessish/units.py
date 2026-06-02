"""Unit-conversion tables and helpers.

Less.js groups units into three families — length, duration, angle — each
keyed by a base unit (meter, second, radian). Values within a family are
convertible; values in different families are not.

The table mirrors `node_modules/less/lib/less/data/unit-conversions.js`
verbatim so that e.g. `1in + 1cm` round-trips byte-exact with less.js.
"""

from __future__ import annotations

import math

# Each table maps unit -> factor toward the family's base unit
# (length: 1 m, duration: 1 s, angle: 1 turn).
LENGTH: dict[str, float] = {
    'm': 1.0,
    'cm': 0.01,
    'mm': 0.001,
    'in': 0.0254,
    'px': 0.0254 / 96,
    'pt': 0.0254 / 72,
    'pc': 0.0254 / 72 * 12,
}

DURATION: dict[str, float] = {
    's': 1.0,
    'ms': 0.001,
}

ANGLE: dict[str, float] = {
    'rad': 1.0 / (2.0 * math.pi),
    'deg': 1.0 / 360.0,
    'grad': 1.0 / 400.0,
    'turn': 1.0,
}

_GROUPS: tuple[dict[str, float], ...] = (LENGTH, DURATION, ANGLE)


def group_of(unit: str) -> dict[str, float] | None:
    """Return the conversion group that owns `unit`, or None if unknown."""
    for g in _GROUPS:
        if unit in g:
            return g
    return None


def convertible(a: str, b: str) -> bool:
    """True iff `a` and `b` belong to the same conversion group."""
    if a == b:
        return True
    if not a or not b:
        return False
    ga = group_of(a)
    return ga is not None and b in ga


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert `value` from `from_unit` to `to_unit`. Both units must
    belong to the same group; returns the raw float (no rounding).
    """
    if from_unit == to_unit:
        return value
    g = group_of(from_unit)
    if g is None or to_unit not in g:
        raise ValueError(f'cannot convert {from_unit!r} to {to_unit!r}')
    return value * g[from_unit] / g[to_unit]
