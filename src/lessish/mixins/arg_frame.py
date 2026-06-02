"""Build the synthetic Ruleset frame that binds a mixin call's evaluated
arguments to the corresponding parameter names.

This frame is pushed onto the evaluator's frame stack before the mixin
body runs, so the body's references to ``@param`` resolve to the
call's evaluated argument value.
"""

from __future__ import annotations

from ..ast_nodes import (
    Anonymous,
    Declaration,
    DetachedRuleset,
    Expression,
    MixinCall,
    MixinDefinition,
    Node,
    Ruleset,
    Value,
)
from ..context import EvalContext, _attach_dr_closure, _fresh_dr_clones
from ..visitors import value_to_css


def _build_arg_value(value_nodes: list[Node], index: int) -> Node:
    """Build the value bound to `@arguments` (or a named variadic) from
    a list of evaluated per-arg values.

    Matches less.js shape:

    * Empty arg list → empty Expression (length 0).
    * Single arg → that arg's evaluated structure passed through. So
      `.M(a b c)` exposes `@arguments` as `Expression([a, b, c])` —
      `length` reports 3, `extract` returns the individual elements.
      `.M(1, 2, 3)` (where the call's only positional happens to be a
      comma-list) preserves the Value's comma structure.
    * Multiple args → `Expression([V1, V2, ...])` where each `Vi` is
      the simplest form of its arg (peeled single-expression Value
      wrappers, but multi-element Values/Expressions kept intact). The
      result emits as a space-joined text (e.g. `1px solid` for
      `.m(1px, solid)`) while `length` returns N and `extract(n)`
      returns the n-th arg's full structure.
    """
    if not value_nodes:
        return Expression(index=index, values=[])
    if len(value_nodes) == 1:
        return _peel_singleton_wrappers(value_nodes[0])
    return Expression(index=index, values=[_arg_item(v) for v in value_nodes])


def _peel_singleton_wrappers(node: Node) -> Node:
    """Unwrap a Value whose only expression has only one value, all the
    way down. `Value([Expression([Value([Expression([x])])])])` → `x`.
    Stops at the first multi-element wrapper so list structure is
    preserved (`Value([Expression([a, b, c])])` collapses to the inner
    Expression, not all the way to `a`).
    """
    cur = node
    while True:
        if isinstance(cur, Value) and len(cur.expressions) == 1:
            cur = cur.expressions[0]
        elif isinstance(cur, Expression) and len(cur.values) == 1:
            cur = cur.values[0]
        else:
            return cur


def _arg_item(node: Node) -> Node:
    """One entry in a multi-arg `@arguments` Expression. Peel singleton
    wrappers so simple scalars don't carry redundant Value([Expression]),
    but preserve any multi-element list shape (comma-list or space-list)
    so `extract(@arguments, n)` returns the original arg's structure.
    """
    return _peel_singleton_wrappers(node)


def build_arg_frame(
    definition: MixinDefinition,
    call: MixinCall,
    ctx: EvalContext,
) -> Ruleset:
    """Construct a synthetic Ruleset containing one Declaration per
    bound parameter. Patterns don't bind; variadics collect overflow
    positional args into a single declaration; named args take
    precedence over positional fillers.

    Arg values are pre-evaluated and re-serialized so subsequent
    `@name` lookups in the mixin body see resolved text. The expression
    layer re-parses them on demand for arithmetic.

    A DetachedRuleset arg is a special case: serialising and re-parsing
    would discard its lexical closure (the scope where the DR literal
    was written at the call site). Capture the closure here and store
    the structured Value AST directly on the Declaration so the inner
    DR survives the lookup.
    """
    from ..evaluator import eval_value

    named_args = {a.name: a for a in call.args if a.name is not None}
    pos_args = [a for a in call.args if a.name is None]
    pos_i = 0

    bindings: list[Node] = []
    arg_texts: list[str] = []
    # Structured per-arg values collected in param order so we can build
    # a structured `@arguments` Value (rather than just text). Each entry
    # is the evaluated `Value` (or simpler node) of one arg slot —
    # `length(@arguments)` / `extract(@arguments, n)` need this to
    # preserve list-of-lists semantics (e.g. `.M(a, b c)` → length 2,
    # extract returns the original Value-per-arg).
    arg_value_nodes: list[Node] = []

    def _eval_for_binding(v: Value) -> tuple[str, Anonymous | Value]:
        """Eval `v`, return (text-form, value-node-for-storage). Always
        keep the structured AST around: re-parsing the serialised text
        on lookup would lose type information (`~"4"` → Quoted(escaped)
        re-parses as Dimension(4); a DR's lexical closure disappears).
        The text form is still computed for the synthetic `@arguments`
        binding and for the `value_to_css` round-trip downstream code
        relies on.
        """
        evaled = eval_value(v, ctx)
        if _value_contains_dr(evaled):
            # A DR literal inside a mixin body — like the `{@{var}: @l;}`
            # rulesets in weui's `.setColor` — is the same AST object
            # across every call to its enclosing mixin. The first
            # `_attach_dr_closure` would lock its captured frames to
            # call #1's arg-bindings; subsequent calls would skip the
            # attach and silently reuse #1's `@var`. Clone the DR(s)
            # here so each invocation captures its own closure.
            evaled = _fresh_dr_clones(evaled)
            _attach_dr_closure(evaled, ctx.frames)
        return value_to_css(evaled), evaled

    def _value_contains_dr(value: Value) -> bool:
        for e in value.expressions:
            for v in e.values:
                if isinstance(v, DetachedRuleset):
                    return True
        return False

    for param in definition.params:
        if param.variadic:
            rest_value_nodes: list[Node] = []
            rest_texts: list[str] = []
            for i in range(pos_i, len(pos_args)):
                text, value_node = _eval_for_binding(pos_args[i].value)
                rest_texts.append(text)
                rest_value_nodes.append(value_node)
            pos_i = len(pos_args)
            # Join text with `, ` so emit-side use (`box-shadow: @rest`)
            # preserves call-site comma-separated structure. Structured
            # form for `length`/`extract`: a Value of one Expression per
            # leftover arg — less.js's Value-of-Expressions convention.
            joined = ', '.join(rest_texts)
            arg_texts.append(joined)
            if param.name:
                bindings.append(
                    Declaration(
                        index=call.index,
                        name=param.name,
                        value=_build_arg_value(rest_value_nodes, call.index),
                        variable=True,
                    )
                )
            # The variadic param's slot also contributes to @arguments —
            # as the comma-list of remaining arg values (mirroring text).
            for v in rest_value_nodes:
                arg_value_nodes.append(v)
            continue
        if param.pattern is not None:
            # Patterns consume a positional arg but don't bind a variable.
            if pos_i < len(pos_args):
                text, value_node = _eval_for_binding(pos_args[pos_i].value)
                arg_texts.append(text)
                arg_value_nodes.append(value_node)
                pos_i += 1
            continue
        # Variable-bindable param: prefer named, fall back to positional, then default.
        used_default = False
        if param.name in named_args:
            arg_value = named_args[param.name].value
        elif pos_i < len(pos_args):
            arg_value = pos_args[pos_i].value
            pos_i += 1
        elif param.default is not None:
            arg_value = param.default
            used_default = True
        else:
            continue
        if used_default:
            # less.js evaluates default values in DEFINITION scope, not
            # call-site. Swap to the def's closure chain for this eval.
            # Splice-time closures (`_splice_closure_frames`) carry the
            # outer mixin's arg-frame and outrank the lookup-time chain.
            closure_frames = definition._splice_closure_frames or (
                definition._closure_frames if definition._closure_frames is not None else []
            )
            if closure_frames:
                saved = ctx.frames
                ctx.frames = list(closure_frames)
                try:
                    text, value_node = _eval_for_binding(arg_value)
                finally:
                    ctx.frames = saved
            else:
                text, value_node = _eval_for_binding(arg_value)
        else:
            text, value_node = _eval_for_binding(arg_value)
        arg_texts.append(text)
        arg_value_nodes.append(value_node)
        bindings.append(
            Declaration(
                index=call.index,
                name=param.name,
                value=value_node,
                variable=True,
            )
        )

    # `@arguments` — every mixin body gets this. less.js exposes it as
    # a structured list so `length`/`extract` see one entry per arg
    # slot (preserving nested comma-lists). For single-arg calls whose
    # value is itself a multi-element Expression (`.M(a b c)`), the
    # Expression is used directly so length(@arguments) yields the
    # space-element count (3) rather than 1.
    bindings.append(
        Declaration(
            index=call.index,
            name='@arguments',
            value=_build_arg_value(arg_value_nodes, call.index),
            variable=True,
        )
    )
    return Ruleset(
        index=call.index,
        selectors=[],
        rules=bindings,
        root=False,
    )
