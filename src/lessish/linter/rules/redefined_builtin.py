"""M3: user-declared mixin whose stem name matches a less.js built-in
function. Confusing for readers — `.lighten` mixin invoked as
`.lighten(@c)` won't reach the built-in `lighten(@c)` function but
the names suggest otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import MixinDefinition
from .._findings import Finding
from ._base import LintContext, Rule

_BUILTIN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # color
        'rgb',
        'rgba',
        'hsl',
        'hsla',
        'hsv',
        'hsva',
        'argb',
        'red',
        'green',
        'blue',
        'alpha',
        'hue',
        'saturation',
        'lightness',
        'lighten',
        'darken',
        'saturate',
        'desaturate',
        'spin',
        'mix',
        'fade',
        'fadein',
        'fadeout',
        'tint',
        'shade',
        'greyscale',
        'contrast',
        'luma',
        'luminance',
        # numeric
        'percentage',
        'unit',
        'round',
        'ceil',
        'floor',
        'abs',
        'min',
        'max',
        'pi',
        'pow',
        'mod',
        'sqrt',
        'sin',
        'cos',
        'tan',
        'asin',
        'acos',
        'atan',
        # string
        'escape',
        'e',
        'replace',
        'format',
        # type / misc
        'iscolor',
        'isnumber',
        'isstring',
        'iskeyword',
        'isurl',
        'ispixel',
        'isem',
        'ispercentage',
        'isunit',
        'isdefined',
        'isruleset',
        'image-size',
        'image-width',
        'image-height',
        'data-uri',
        'default',
        'get-unit',
        'svg-gradient',
    }
)


class RedefinedBuiltinRule(Rule):
    id = 'redefined-builtin'
    severity = 'error'
    fix_tier = 'none'
    description = 'Mixin name matches a less.js built-in function.'
    node_types = (MixinDefinition,)

    def on_node(  # type: ignore[override]
        self, node: MixinDefinition, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        stem = node.name.lstrip('.#')
        if stem in _BUILTIN_FUNCTIONS:
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'mixin `{node.name}` shadows built-in function `{stem}`',
                location=ctx.location_at(node.index),
                span=(node.index, node.index),
            )
