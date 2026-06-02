"""Dispatch table + `eval_node`.

Indexed by concrete `type(node)`. An isinstance chain on every node
is the #1 hot spot (1.6M isinstance calls per Bootstrap compile);
`type(...)` + dict lookup is one C-level op. Concrete subclasses
must register here too — every AST class in `ast_nodes` that flows
through `eval_node` is enumerated by `_build_eval_dispatch`.

The table is populated by `__init__.py` once every handler sub-
module has been imported. Sub-modules may depend on `eval_node`
through function-body imports, so populating at package import
time avoids any chicken-and-egg.
"""

from __future__ import annotations

from collections.abc import Callable

from ..ast_nodes import (
    Anonymous,
    AtRule,
    Call,
    Declaration,
    DetachedRuleset,
    Expression,
    Lookup,
    MixinCall,
    MixinDefinition,
    Negative,
    Node,
    Operation,
    Paren,
    PropertyAccess,
    Quoted,
    Ruleset,
    Url,
    Value,
    Variable,
    VariableCall,
)
from ..context import EvalContext
from ..errors import LessError, UndefinedNameError
from .helpers import _locate


# Bound by `_build_eval_dispatch` at package-init time. The wrap helpers
# below read from these globals so they don't need a per-call import of
# `.lookups` (which can't go at module top due to a circular import).
# The placeholder raises if the dispatch table is queried before
# `_build_eval_dispatch()` ran; in practice the package `__init__`
# populates these before any compile starts.
def _uninitialised(*_args: object, **_kw: object) -> Node:
    raise RuntimeError('evaluator dispatch helpers were called before _build_eval_dispatch()')


_eval_variable: Callable[[Variable, EvalContext], Node] = _uninitialised
_eval_property_access: Callable[[PropertyAccess, EvalContext], Node] = _uninitialised
_eval_lookup: Callable[[Lookup, EvalContext], Node] = _uninitialised


def _eval_variable_wrap(node: Variable, ctx: EvalContext) -> Node:
    try:
        return _eval_variable(node, ctx)
    except LessError as e:
        _locate(e, node.index, ctx)
        raise


def _eval_property_access_wrap(node: PropertyAccess, ctx: EvalContext) -> Node:
    try:
        return _eval_property_access(node, ctx)
    except LessError as e:
        _locate(e, node.index, ctx)
        raise


def _eval_lookup_wrap(node: Lookup, ctx: EvalContext) -> Node:
    try:
        return _eval_lookup(node, ctx)
    except UndefinedNameError as e:
        # Key-not-found and variable-not-found errors anchor at the
        # `[` token (matches less.js's column reporting).
        _locate(e, node.index, ctx)
        raise
    # Other LessErrors let the outer `eval_declaration` anchor at the
    # declaration start.


def _eval_anonymous(node: Anonymous, ctx: EvalContext) -> Node:
    if node._ie_filter:
        # ie-filter values are captured as a single Anonymous; resolve
        # `@var` / `@{var}` inside the call args before emit.
        new_text = ctx.substitute_text(node.value)
        if new_text == node.value:
            return node
        replaced = Anonymous(index=node.index, value=new_text)
        replaced._ie_filter = True
        return replaced
    return node


def _eval_identity(node: Node, ctx: EvalContext) -> Node:
    return node


# Indexed by concrete `type(node)`. Populated by `_build_eval_dispatch`
# at package-init time once every handler sub-module has been imported.
_EVAL_DISPATCH: dict[type, object] = {}


def _build_eval_dispatch() -> None:
    from .atrules import eval_atrule
    from .declarations import eval_declaration
    from .lookups import eval_lookup, eval_property_access, eval_variable
    from .rulesets import eval_ruleset
    from .values import (
        eval_call,
        eval_expression,
        eval_negative,
        eval_operation_inner,
        eval_paren,
        eval_quoted,
        eval_url,
        eval_value,
    )

    global _eval_variable, _eval_property_access, _eval_lookup
    _eval_variable = eval_variable
    _eval_property_access = eval_property_access
    _eval_lookup = eval_lookup

    _EVAL_DISPATCH.update(
        {
            Ruleset: eval_ruleset,
            Declaration: eval_declaration,
            AtRule: eval_atrule,
            Value: eval_value,
            Expression: eval_expression,
            Variable: _eval_variable_wrap,
            PropertyAccess: _eval_property_access_wrap,
            # Operations don't attach location here — less.js reports
            # operation errors at the *declaration's* position. The outer
            # eval_declaration wrapper fills in the correct column.
            Operation: eval_operation_inner,
            Negative: eval_negative,
            Paren: eval_paren,
            Call: eval_call,
            Quoted: eval_quoted,
            Url: eval_url,
            Lookup: _eval_lookup_wrap,
            # MixinCall/MixinDefinition/VariableCall reach `eval_node`
            # only as defensive fall-through: return unchanged.
            MixinCall: _eval_identity,
            MixinDefinition: _eval_identity,
            VariableCall: _eval_identity,
            # DetachedRuleset literal — bound to a variable, invoked
            # later by `_invoke_variable_call`.
            DetachedRuleset: _eval_identity,
            Anonymous: _eval_anonymous,
        }
    )


def eval_node(node: Node, ctx: EvalContext) -> Node:
    """Dispatch on `node`'s runtime type. Literals pass through unchanged."""
    handler = _EVAL_DISPATCH.get(type(node))
    if handler is not None:
        return handler(node, ctx)  # type: ignore[operator,no-any-return]
    # Dimension / Color / Keyword / unregistered subtypes: literal pass-through.
    return node
