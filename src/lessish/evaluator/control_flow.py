"""Control-flow helpers: `each()`, `if()`, variable-call invocation,
and Condition evaluation.

These functions sit between the value-level and ruleset-level passes —
they take statement-position calls (`each`, `if`, `@dr()`) and produce
splicable rule lists, and they evaluate `when (...)` guard conditions
for mixin selection and ruleset CSS-guards.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from ..ast_nodes import (
    Anonymous,
    AtRule,
    Call,
    Color,
    Condition,
    Declaration,
    DetachedRuleset,
    Dimension,
    Expression,
    Keyword,
    Lookup,
    MixinCall,
    Node,
    Paren,
    Quoted,
    Ruleset,
    Value,
    Variable,
    VariableCall,
)
from ..comparisons import compare, evaluate_op
from ..context import EvalContext
from ..errors import EvalError, LessError
from ..functions import lookup
from ..visitors import value_to_css
from ._drive import drive
from .dispatch import eval_node
from .helpers import _maybe_promote_color
from .values import _eval_if, _unwrap_single, eval_call


def _invoke_function_statement(call: MixinCall, ctx: EvalContext) -> list[Node] | None:
    """Try to evaluate `name(args)` at statement position as a function
    call (e.g. `e('/* comment */');` at root). Returns a synthetic
    `@__inline__` AtRule wrapping the call's text output on success;
    `None` if `name` isn't a registered function (caller will surface
    the normal "name is undefined" error).

    less.js strict-mode behavior: a registered function returning a
    bare value-level node (Color, Dimension, Keyword, …) at statement
    position is rejected — `<Type> node returned by a function is not
    valid here`. Only Ruleset/list-of-rules returns are valid at root.
    The Quoted-escape (`e('…');`) special case is preserved because
    less.js itself emits its text output verbatim.
    """
    if lookup(call.name) is None:
        return None
    call_args: list[Expression] = []
    for a in call.args:
        v = a.value
        if isinstance(v, Expression):
            call_args.append(v)
        else:
            call_args.append(Expression(index=v.index, values=[v]))
    fn_call = Call(index=call.index, name=call.name, args=call_args)
    result = eval_call(fn_call, ctx)
    # Unwrap Quoted's escape-mode (so `e('text')` emits `text`, not
    # `'text'`).
    if isinstance(result, Quoted):
        return [
            AtRule(
                index=call.index,
                name='@__inline__',
                prelude=result.value,
                body=None,
            )
        ]
    # Bare value-level returns are invalid at statement position.
    if isinstance(result, (Color, Dimension, Keyword)):
        type_name = type(result).__name__
        err = EvalError(f'{type_name} node returned by a function is not valid here')
        err._less_js_name = 'SyntaxError'
        raise err
    # Anything else (Anonymous text from `e()`, etc.) emits inline.
    return [
        AtRule(
            index=call.index,
            name='@__inline__',
            prelude=value_to_css(result),
            body=None,
        )
    ]


def eval_condition(cond: Condition, ctx: EvalContext) -> bool:
    """Evaluate a Condition tree to a boolean.

    Logical ops (`and`/`or`) short-circuit. `not` flips. Comparisons
    evaluate both sides, run them through `comparisons.compare`, then
    apply `evaluate_op`. A bare value (`when (@active)`) is "truthy"
    when it resolves to anything other than `false` / 0 / undefined.
    """
    op = cond.op
    if op == 'and':
        assert cond.rhs is not None
        lhs_b = eval_condition(_as_condition(cond.lhs), ctx)
        if not lhs_b:
            return _apply_negate(False, cond.negate)
        rhs_b = eval_condition(_as_condition(cond.rhs), ctx)
        return _apply_negate(rhs_b, cond.negate)
    if op == 'or':
        assert cond.rhs is not None
        lhs_b = eval_condition(_as_condition(cond.lhs), ctx)
        if lhs_b:
            return _apply_negate(True, cond.negate)
        rhs_b = eval_condition(_as_condition(cond.rhs), ctx)
        return _apply_negate(rhs_b, cond.negate)
    if op == 'not':
        inner = eval_condition(_as_condition(cond.lhs), ctx)
        return _apply_negate(not inner, cond.negate)
    if op == 'truthy':
        result = _truthy(_unwrap_single(eval_node(cond.lhs, ctx)))
        return _apply_negate(result, cond.negate)
    assert cond.rhs is not None
    lhs = _unwrap_single(eval_node(cond.lhs, ctx))
    rhs = _unwrap_single(eval_node(cond.rhs, ctx))
    # Promote color keywords for equality on either side.
    lhs = _maybe_promote_color(lhs)
    rhs = _maybe_promote_color(rhs)
    cmp = compare(lhs, rhs)
    return _apply_negate(evaluate_op(op, cmp), cond.negate)


def _as_condition(n: Node) -> Condition:
    """Internal helper: guard tree always stores Condition children, but
    the type system can't express that fully — narrow with an assert.
    """
    assert isinstance(n, Condition)
    return n


def _apply_negate(value: bool, negate: bool) -> bool:
    return (not value) if negate else value


def _truthy(node: Node) -> bool:
    """Less.js's truthiness rule for bare-value guards: ONLY the literal
    Keyword `true` is truthy. Numbers (incl. nonzero), strings, other
    keywords, colors, and Dimensions all evaluate to false. Matches
    less.js's `Condition.eval` default branch which checks
    `a instanceof Bool && a.value === true`.
    """
    return isinstance(node, Keyword) and node.value == 'true'


def _invoke_each(call: MixinCall, ctx: EvalContext) -> list[Node]:
    """Synchronous entry-point — drives the generator form. Other
    generators in the iterative-evaluator cycle call
    `_invoke_each_gen` directly via `yield`.
    """
    result = drive(_invoke_each_gen(call, ctx), ctx)
    assert isinstance(result, list)
    return result


def _invoke_each_gen(call: MixinCall, ctx: EvalContext) -> Generator[Any, Any, list[Node]]:
    """`each(list, { ... })` — special form. For each item in `list`,
    evaluate the detached-ruleset body with `@value`, `@index` (1-based),
    and `@key` (for declaration-list inputs) bound. Returns the
    concatenation of every iteration's rules.

    `list` is one of:
      * Value with multiple Expression children (comma-separated)
      * Expression with multiple values (space-separated)
      * A single scalar — treated as a 1-element list
      * A DetachedRuleset whose body is a list of variable declarations
        (the "map" form) — each iteration sets `@key=name` `@value=val`.

    Generator. Yields a child `_eval_ruleset_gen` per iteration so a
    nested `each(...)` doesn't grow the Python stack.
    """
    from .rulesets import _eval_ruleset_gen

    if len(call.args) < 2:
        raise EvalError('each() expects 2 arguments')
    list_value = call.args[0].value
    body = call.args[1].value
    # The body is a Value wrapping a DetachedRuleset most of the time.
    body_unwrapped = _unwrap_single(body)
    if isinstance(body_unwrapped, Variable):
        body_unwrapped = _unwrap_single(eval_node(body_unwrapped, ctx))
    if not isinstance(body_unwrapped, DetachedRuleset):
        raise EvalError('each() second argument must be a detached ruleset')

    items, keys = _each_items(list_value, ctx)
    # `.(@v, @k, @i) { body }` shorthand: the lambda's positional params
    # map to value / key / index (in that order) rather than the default
    # `@value` / `@key` / `@index` names. Stored on the DR as
    # `_lambda_params` by `Parser._parse_anonymous_mixin`.
    lambda_params = body_unwrapped._lambda_params
    binding_names: tuple[str | None, str | None, str | None]
    if lambda_params:
        names = [p.name for p in lambda_params if p.name]
        binding_names = (
            names[0] if len(names) > 0 else None,
            names[1] if len(names) > 1 else None,
            names[2] if len(names) > 2 else None,
        )
    else:
        binding_names = ('@value', '@key', '@index')
    out: list[Node] = []
    for i, (item, key) in enumerate(zip(items, keys, strict=True), start=1):
        frame = _build_each_frame(item, key, i, call.index, binding_names)
        synthetic = Ruleset(index=call.index, selectors=[], rules=body_unwrapped.rules, root=False)
        ctx.push_frame(frame)
        try:
            evaled = yield _eval_ruleset_gen(synthetic, ctx)
        finally:
            ctx.pop_frame()
        out.extend(evaled.rules)
    return out


def _each_items(node: Node, ctx: EvalContext) -> tuple[list[Node], list[str | None]]:
    """Decompose `node` into the per-iteration item list, plus parallel
    `keys` for declaration-list / detached-ruleset map inputs (None when
    not applicable). Variables and function calls get resolved up-front
    so `each(range(1, 3), …)` and `each(@list, …)` both expand.
    """
    from .lookups import _eval_dr_body
    from .mixins import _invoke_mixin_call

    node = _unwrap_single(node)
    # `~(...)` list constructors arrive as a Paren wrapping the actual
    # list — strip the wrapper (which may need another unwrap pass to
    # collapse the inner single-Expression / single-value layers).
    while isinstance(node, Paren) and node._tilde_list:
        node = _unwrap_single(node.value)
    if isinstance(node, Variable | Call | Lookup):
        node = _unwrap_single(eval_node(node, ctx))
    if isinstance(node, MixinCall):
        # `each(.mixin(), …)` — invoke the mixin and iterate the rules
        # it splices into the call site. Same shape as a DR/map: every
        # produced Declaration contributes (value, name) to the iterator.
        produced = _invoke_mixin_call(node, ctx)
        items: list[Node] = []
        keys: list[str | None] = []
        for r in produced:
            if isinstance(r, Declaration):
                items.append(r.value)
                keys.append(r.name)
        return items, keys
    if isinstance(node, DetachedRuleset):
        # Map form: each iteration gets the declaration's name as @key
        # and value as @value. less.js evaluates the DR body in its
        # OWN scope before iterating (`list.rules.map(tryEval)`) so
        # sibling `$prop` / `@var` references inside a value resolve
        # against the surrounding DR rules — not against the each()
        # call site, which knows nothing about the DR's contents.
        # We mirror by running `_eval_dr_body` once up front; the
        # returned rules carry already-resolved Anonymous values.
        try:
            evaled_rules = _eval_dr_body(node, ctx, index=node.index)
        except LessError:
            # Fall back to the raw rule list if eval bails — preserves
            # the older behaviour for DRs whose body legitimately can't
            # eval in isolation (e.g. references the iteration's
            # `@value` from a parent each).
            evaled_rules = node.rules
        items = []
        keys = []
        for r in evaled_rules:
            if isinstance(r, Declaration):
                items.append(r.value)
                # less.js exposes the property name as a string. Use the
                # raw text (incl. leading '@' for variables).
                keys.append(r.name)
        return items, keys
    if isinstance(node, Value) and len(node.expressions) > 1:
        return list(node.expressions), [None] * len(node.expressions)
    if isinstance(node, Expression) and len(node.values) > 1:
        return list(node.values), [None] * len(node.values)
    # Single scalar — wrap as a 1-element list.
    return [node], [None]


def _build_each_frame(
    item: Node,
    key: str | None,
    index: int,
    call_index: int,
    binding_names: tuple[str | None, str | None, str | None] = ('@value', '@key', '@index'),
) -> Ruleset:
    """Build the synthetic frame holding the per-iteration value / key /
    index bindings. `binding_names` is `(value, key, index)` — defaults
    to the standard `@value` / `@key` / `@index` slots, but lambda
    shorthand (`.(@v, @k, @i)`) overrides each name. The values flow
    through `value_to_css` so subsequent lookups see resolved text.

    For array-shaped lists, `key` is None — we substitute the 1-based
    iteration index so `@key` (or its lambda alias) is non-empty and
    matches less.js.
    """
    value_name, key_name, index_name = binding_names
    bindings: list[Node] = []
    if value_name:
        text = value_to_css(item) if isinstance(item, Node) else str(item)
        bindings.append(
            Declaration(
                index=call_index,
                name=value_name,
                value=Anonymous(index=call_index, value=text),
                variable=True,
            )
        )
    if key_name:
        key_text = key if key is not None else str(index)
        bindings.append(
            Declaration(
                index=call_index,
                name=key_name,
                value=Anonymous(index=call_index, value=key_text),
                variable=True,
            )
        )
    if index_name:
        bindings.append(
            Declaration(
                index=call_index,
                name=index_name,
                value=Anonymous(index=call_index, value=str(index)),
                variable=True,
            )
        )
    return Ruleset(index=call_index, selectors=[], rules=bindings, root=False)


def _invoke_if_statement(call: MixinCall, ctx: EvalContext) -> list[Node]:
    """Synchronous entry-point — drives the generator form."""
    result = drive(_invoke_if_statement_gen(call, ctx), ctx)
    assert isinstance(result, list)
    return result


def _invoke_if_statement_gen(call: MixinCall, ctx: EvalContext) -> Generator[Any, Any, list[Node]]:
    """Statement-level `if(cond, A, B?);` — interpret as the value-level
    `if(...)` special form, then splice. A DetachedRuleset result is
    invoked (its rules emit at the call site); an empty Anonymous result
    produces nothing; any other result emits inline (matches
    `_invoke_function_statement`).

    Generator. Yields `_eval_dr_body_gen` for the DR-result branch.
    """
    from .lookups import _eval_dr_body_gen

    call_args: list[Expression] = []
    for a in call.args:
        v = a.value
        if isinstance(v, Expression):
            call_args.append(v)
        else:
            call_args.append(Expression(index=v.index, values=[v]))
    fn_call = Call(index=call.index, name='if', args=call_args)
    result = _unwrap_single(_eval_if(fn_call, ctx))
    if isinstance(result, DetachedRuleset):
        nodes: list[Node] = yield _eval_dr_body_gen(result, ctx, index=call.index)
        return nodes
    if isinstance(result, Anonymous) and not result.value:
        return []
    text = value_to_css(result)
    return [AtRule(index=call.index, name='@__inline__', prelude=text, body=None)]


def _invoke_variable_call(call: VariableCall, ctx: EvalContext) -> list[Node]:
    """Synchronous entry-point — drives the generator form."""
    result = drive(_invoke_variable_call_gen(call, ctx), ctx)
    assert isinstance(result, list)
    return result


def _invoke_variable_call_gen(call: VariableCall, ctx: EvalContext) -> Generator[Any, Any, list[Node]]:
    """Look up `call.name` (a variable holding a DetachedRuleset OR a
    captured value-position MixinCall) and evaluate its body in the
    call site's scope, returning the produced rules for splicing.

    A DR's `_captured_frames` (set at declaration / arg-binding time)
    is prepended to `ctx.frames` so variable lookups inside the body
    hit the DR's lexical scope first, falling back to the invoke-site
    scope (matches less.js `DetachedRuleset.callEval`).

    Generator. Yields `_eval_dr_body_gen` for the DR branch;
    `_invoke_value_mixin_call` stays a sync call (the value-position
    lookup path is not part of the trampolined cycle).
    """
    from .lookups import _eval_dr_body_gen
    from .mixins import _invoke_value_mixin_call

    resolved = _unwrap_single(ctx.lookup_variable_node(call.name))
    if isinstance(resolved, DetachedRuleset):
        nodes: list[Node] = yield _eval_dr_body_gen(resolved, ctx, index=call.index)
        return nodes
    if isinstance(resolved, MixinCall):
        # `@alias: .mixin(args); @alias();` — invoke the captured mixin.
        return _invoke_value_mixin_call(resolved, ctx)
    err = EvalError(f'Could not evaluate variable call {call.name}')
    raise err
