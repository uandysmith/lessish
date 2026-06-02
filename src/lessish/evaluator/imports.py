"""Deferred @import resolution.

`_resolve_deferred_import` reruns an `@import` whose path needed
variable substitution (the importer deferred it) now that the live
frame stack is populated.
"""

from __future__ import annotations

from ..ast_nodes import AtRule, MixinCall, Node, VariableCall
from ..context import EvalContext
from ..importer import Importer as _Importer
from .control_flow import _invoke_variable_call
from .dispatch import eval_node
from .mixins import _invoke_mixin_call


def _resolve_deferred_import(at: AtRule, ctx: EvalContext) -> list[Node]:
    """Re-resolve an `@import` whose path contained `@{var}` interpolation
    (deferred by the importer pass). Substitutes the path against the
    live frame stack, calls back to the importer to fetch the file, and
    runs the imported rules through the mixin-transform + eval pipeline
    so they behave exactly as if they'd been resolved at importer-pass
    time.
    """
    if ctx.importer is None:
        # No importer wired up (e.g. compile() called with process_imports
        # disabled). Leave the AtRule in place — it will emit verbatim,
        # which is the best we can do without file access.
        return [at]
    substituted = ctx.substitute_text(at.prelude)

    assert isinstance(ctx.importer, _Importer)
    raw_rules = ctx.importer.resolve_deferred(at, substituted)
    out: list[Node] = []
    for r in raw_rules:
        if isinstance(r, AtRule) and r is at:
            # Importer chose to keep the @import verbatim (CSS pass-
            # through, optional miss, etc.) — propagate as-is.
            out.append(at)
            continue
        # Imported files can themselves contain deferred `@import` —
        # recurse so the inner one re-resolves with the now-current
        # scope (which now includes any variables the inner file
        # defined before its own deferred import).
        if isinstance(r, AtRule) and r._deferred_import:
            out.extend(_resolve_deferred_import(r, ctx))
            continue
        # The importer's `_handle_import` already runs `transform_mixins`
        # on the inlined file's tree, so each rule is ready to evaluate.
        if isinstance(r, MixinCall):
            out.extend(_invoke_mixin_call(r, ctx))
        elif isinstance(r, VariableCall):
            out.extend(_invoke_variable_call(r, ctx))
        else:
            out.append(eval_node(r, ctx))
    return out
