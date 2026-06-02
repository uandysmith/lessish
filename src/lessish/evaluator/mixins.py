"""Mixin invocation, spread expansion, splice-closure tagging.

`_invoke_mixin_call` is the user-facing entry. It applies the depth /
total-invocation budget, expands `@list...` spread args, finds matches
through `lessish.mixins.find_mixin_matches`, filters out recursive
self-invocations and namespace-descent guards, runs the default()-
aware guard selection, then invokes every surviving candidate.

`_invoke_value_mixin_call` is the value-position twin used by lookup
resolution — same machinery but without important-propagation /
splice-closure tagging.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from ..ast_nodes import (
    Anonymous,
    AtRule,
    Call,
    Declaration,
    Expression,
    MixinArg,
    MixinCall,
    MixinDefinition,
    Node,
    Operation,
    Ruleset,
    Value,
    Variable,
)
from ..context import EvalContext
from ..errors import EvalError, UndefinedNameError
from ..mixins import build_arg_frame, find_mixin_matches, name_exists
from .control_flow import (
    _invoke_each_gen,
    _invoke_function_statement,
    _invoke_if_statement_gen,
    eval_condition,
)
from .helpers import _format_call_args, _locate, _propagate_important
from .values import eval_value


def _arg_has_variable(node: Node) -> bool:
    """True if `node` contains a Variable somewhere in its value tree.
    Used to decide whether call args need pre-evaluation for pattern
    matching.
    """
    if isinstance(node, Variable):
        return True
    if isinstance(node, Value):
        return any(_arg_has_variable(e) for e in node.expressions)
    if isinstance(node, Expression):
        return any(_arg_has_variable(v) for v in node.values)
    if isinstance(node, Operation):
        return _arg_has_variable(node.lhs) or _arg_has_variable(node.rhs)
    if isinstance(node, Call):
        return any(_arg_has_variable(a) for a in node.args)
    return False


def _invoke_mixin_call(call: MixinCall, ctx: EvalContext) -> list[Node]:
    """Synchronous entry-point — runs the generator body under the
    trampoline so the mixin-invocation cycle stays flat.
    """
    from ._drive import drive

    result = drive(_invoke_mixin_call_gen(call, ctx), ctx)
    assert isinstance(result, list)
    return result


def _invoke_mixin_call_gen(call: MixinCall, ctx: EvalContext) -> Generator[Any, Any, list[Node]]:
    """Find every matching definition in scope and invoke each.

    Guard filtering happens here (not in `find_mixin_matches`) because
    a guard needs the bound-arg frame and `ctx` to evaluate — those only
    exist once we have a concrete (call, definition) pair.

    Tie-break via `default()`: when at least one candidate matches with
    its `default()` returning *false* and at least one with *true*, the
    `default()=true` candidates are dropped (the explicit-match wins).
    less.js does the same; without this, common patterns like
    `.m(@x) when (default())` would always fire alongside the targeted
    overload.

    Generator. Yields a child `_eval_ruleset_gen` for each matched
    definition's synthetic body. The trampoline in `_drive.drive`
    pumps the children and feeds their results back via `.send()`.
    """
    # `each()` is a built-in special form, not a user-defined mixin —
    # short-circuit it here before the name-lookup machinery runs.
    if call.name == 'each':
        each_nodes: list[Node] = yield _invoke_each_gen(call, ctx)
        return each_nodes
    # `if(cond, A, B?);` at statement position — evaluate, splice a
    # DetachedRuleset result, drop a void result. Mirrors less.js where
    # `if((false), {g: 7})` produces no output.
    if call.name.lower() == 'if':
        if_nodes: list[Node] = yield _invoke_if_statement_gen(call, ctx)
        return if_nodes

    # Hard ceilings on mixin invocation. The depth check catches a
    # straight runaway recursion (`.x() { .x(); } .x();`); the total
    # check catches exponential expansion via cross-invocation matching
    # — when each invocation matches multiple defs in scope and each
    # of those re-invokes more, output grows multiplicatively even at
    # shallow depth. Without these we silently hang.
    #
    # Per-compile overrides (`mixin_*_limit` compile options) win;
    # otherwise read the module-level defaults through the package so
    # a test-time `lessish.evaluator.MIXIN_*_LIMIT = N` rebinding takes
    # effect at runtime instead of being shadowed by a module-local
    # binding.
    from .. import evaluator as _pkg

    depth_limit = ctx.mixin_depth_limit if ctx.mixin_depth_limit is not None else _pkg.MIXIN_DEPTH_LIMIT
    total_limit = ctx.mixin_total_limit if ctx.mixin_total_limit is not None else _pkg.MIXIN_TOTAL_LIMIT
    ctx._mixin_call_depth += 1
    ctx._mixin_call_total += 1
    if ctx._mixin_call_depth > depth_limit:
        ctx._mixin_call_depth -= 1
        raise EvalError(
            f'Maximum mixin call depth exceeded ({depth_limit}) invoking `{call.name}` — probable runaway recursion'
        )
    if ctx._mixin_call_total > total_limit:
        ctx._mixin_call_depth -= 1
        raise EvalError(
            f'Mixin invocation budget exhausted ({total_limit}) invoking `{call.name}` — probable exponential expansion'
        )
    if ctx._deadline is not None:
        try:
            ctx.check_deadline(f'invoking mixin `{call.name}`')
        except EvalError:
            ctx._mixin_call_depth -= 1
            raise

    try:
        from .rulesets import _eval_ruleset_gen

        # Call-site spread (`.mixin(@list...)`): expand `@list` into the
        # positional arg slot before matching. Mirrors less.js — the
        # ellipsis suffix on a Variable splices its value sequence as
        # individual positional args.
        call = _expand_spread_call_args(call, ctx)

        # Pre-evaluate call args so pattern matching (`right`, `4px`, etc.)
        # sees resolved values instead of raw Variables. Build a substituted
        # MixinCall for the matcher; the original `call.args` is kept for
        # build_arg_frame which prefers raw Values (defaults / variadic).
        if any(_arg_has_variable(a.value) for a in call.args):
            resolved_args = [MixinArg(index=a.index, name=a.name, value=eval_value(a.value, ctx)) for a in call.args]
            resolved_call = MixinCall(
                index=call.index,
                name=call.name,
                args=resolved_args,
                important=call.important,
            )
        else:
            resolved_call = call

        candidates = find_mixin_matches(resolved_call, ctx.frames, resolve_name=ctx.substitute_text)
        # Break self-recursion through a Ruleset-as-mixin: a body containing
        # `.x();` invoked from inside `.x { … }` should NOT re-enter the same
        # ruleset. less.js matches the MixinDefinition `.x()` (if any) and
        # skips the surrounding Ruleset. We track in-progress Ruleset ids
        # on ctx and filter them out of `candidates`.
        invoking_ids = ctx._invoking_ruleset_ids
        if invoking_ids:
            candidates = [c for c in candidates if c._source_ruleset_id not in invoking_ids]
        # Descent guard filter: `#guarded when (cond) { #deeper { .mixin() …
        # } }` exposes its inner `.mixin()` via a namespace path only when
        # `cond` holds. The candidate carries a `_closure_frames` list of
        # parent Rulesets — evaluate every parent's `condition` here in the
        # call-site scope and drop the candidate if any fails.
        candidates = [c for c in candidates if _descent_conditions_pass(c, ctx)]
        # less.js's shadowing rule: among the survivors after the recursion
        # and descent filters, keep only those that came from the lowest
        # (innermost) frame where any survivor was found. Outer-frame
        # definitions of the same name are shadowed and don't fire. Without
        # this, a `.col()` call inside one mixin invocation would also
        # match `.col()` definitions left in scope by a SIBLING mixin
        # invocation (since both splice into the same caller frame), which
        # chains exponentially.
        if candidates:
            # `_match_frame_index` is always `int` (default 0); no `is not
            # None` guard needed.
            innermost = min(c._match_frame_index for c in candidates)
            candidates = [c for c in candidates if c._match_frame_index == innermost]
        if not candidates:
            # Distinguish "name not found at all" from "name exists but
            # no arity-compatible definition" — less.js uses two different
            # error classes for these.
            if name_exists(call, ctx.frames, resolve_name=ctx.substitute_text):
                args_text = _format_call_args(call, ctx)
                err = EvalError(f'No matching definition was found for `{call.name}({args_text})`')
                err._less_js_name = 'RuntimeError'
                raise err
            # `name();` at root with no leading `.`/`#`/`@` may be a
            # function-call statement (e.g. `e('/* comment */');` — less.js
            # evaluates the call and emits the result as raw text). Try
            # the function registry before reporting "name is undefined".
            if not call.name.startswith(('.', '#', '@')):
                spliced = _invoke_function_statement(call, ctx)
                if spliced is not None:
                    return spliced
                # less.js strict-mode: a bare `name();` at statement
                # position with no matching mixin AND no registered
                # function is reported as `Function '<name>' did not
                # return a root node`, not as an undefined-name error.
                err = EvalError(f"Function '{call.name}' did not return a root node")
                err._less_js_name = 'SyntaxError'
                raise err
            raise UndefinedNameError(f'{call.name} is undefined')

        # Two-pass match: first compute the default()-conditioned guard
        # bookkeeping, then re-evaluate.
        selected = _select_mixin_branches(candidates, call, ctx)
        if not selected:
            # CSS-guarded rulesets called as 0-arg mixins silently produce
            # no output when their guards fail. Same for mixin definitions
            # where every arity-matched candidate carries a guard that
            # evaluated to false — less.js emits nothing rather than raise.
            if all(c.is_ruleset_wrapper or c.guard is not None for c in candidates):
                return []
            raise EvalError(f'no matching definition for mixin {call.name!r}')

        out: list[Node] = []
        for definition in selected:
            arg_frame = build_arg_frame(definition, call, ctx)
            synthetic = Ruleset(
                index=definition.index,
                selectors=[],
                rules=definition.rules,
                root=False,
            )
            # Closure frames: when the mixin was found via namespace
            # descent (`.scope > .mixin()`) or in a non-innermost frame,
            # the body sees DEFINITION scope first (matches less.js
            # lexical scoping for mixins). Prepend closure to call-site
            # frames (deduped by identity) so a name resolves: definition
            # scope → call-site → root, mirroring less.js's
            # `context.frames = closure.concat(context.frames)`.
            #
            # Both `_closure_frames` (set at lookup time by namespace
            # descent — the in-tree path the matcher walked through) AND
            # `_splice_closure_frames` (set when the def was emitted out
            # of a mixin invocation's body — captures the calling mixin's
            # arg frame) may apply. Splice frames are the *innermost*
            # lexical scope (the original mixin's arg/local bindings)
            # and must take precedence over the in-tree descent path so
            # a captured `@a = 1` overrides a global `@a: auto`. Descent
            # frames go after and the call-site stack last. Dedup by id
            # so a shared global frame isn't visited twice.
            descent_frames = definition._closure_frames or []
            splice_frames = definition._splice_closure_frames or []
            closure_frames: list[Ruleset] = list(splice_frames) + list(descent_frames)
            saved_frames = ctx.frames
            if closure_frames:
                seen_ids: set[int] = set()
                merged_frames: list[Ruleset] = []
                for f in list(closure_frames) + ctx.frames:
                    if id(f) in seen_ids:
                        continue
                    seen_ids.add(id(f))
                    merged_frames.append(f)
                ctx.frames = merged_frames
            ctx.push_frame(arg_frame)
            # Track the source Ruleset id (if this candidate is a
            # Ruleset-as-mixin wrapper) so nested calls can detect
            # recursion. See the filter on `_invoking_ruleset_ids` above.
            source_id = definition._source_ruleset_id
            if source_id is not None:
                invoking_ids.add(source_id)
            try:
                # Yield to the trampoline — this is the deep
                # recursion cycle's entry point. The driver pulls
                # the child generator onto its stack and sends back
                # the resulting Ruleset.
                evaled = yield _eval_ruleset_gen(synthetic, ctx)
                # Snapshot the live frame stack (arg_frame on top) so any
                # MixinDefinitions spliced out of this body remember the
                # scope they were born in. Required for the
                # `.lock-mixin(@a) { .inner(@x: @a) when (@a = 1) { … } }`
                # pattern: once the outer call returns and `@a` is gone,
                # invoking `.inner()` from the caller's frame must still
                # resolve `@a` against the captured stack.
                captured_frames = list(ctx.frames)
            except UndefinedNameError as e:
                # Re-anchor name-lookup failures inside the body at the CALL
                # site rather than the body interior — less.js convention.
                # Drop the inner location so `_locate` re-sets it.
                e.location = None
                _locate(e, call.index, ctx)
                raise
            finally:
                if source_id is not None:
                    invoking_ids.discard(source_id)
                ctx.pop_frame()
                if closure_frames:
                    ctx.frames = saved_frames
            rules = evaled.rules
            # Walk the spliced rules and tag MixinDefinitions with the
            # captured frame stack. The original MixinDefinition is the same
            # object across every invocation of the surrounding mixin, so
            # mutating it would leak this call's `@arg` bindings into later
            # calls — copy first. Use a separate `_splice_closure_frames`
            # key so a later `find_mixin_matches` lookup-time closure
            # (which gets overwritten freely) can coexist with this
            # capture-time closure.
            if rules:
                # Only clear `reference=True` on the splice when (a) the
                # mixin definition was *itself* sourced from an
                # `@import (reference)` subtree, AND (b) the call site is
                # NOT in a reference subtree. Both conditions ensure we
                # only "promote" reference content to emittable CSS when
                # the new context can actually emit it. Cascading mixin
                # invocations entirely within a reference subtree (e.g.
                # `media.less`'s `.menu { .nav-justified(); }` inside a
                # reference-imported file) keep their reference flag so
                # the eventual top-level emit drops them.
                call_site_is_reference = any(getattr(f, 'reference', False) for f in ctx.frames)
                clear_reference = bool(definition._was_referenced) and not call_site_is_reference
                rules = [_tag_splice_closures(r, captured_frames, clear_reference=clear_reference) for r in rules]
            if call.important:
                rules = [_propagate_important(r) for r in rules]
            # Tag variable declarations spliced out of a mixin invocation
            # so the call-site's `_find_variable_with_frame` lookup can
            # deprioritize them when the call site has its own decl for
            # the same name. Mirrors less.js's scope rule: a local
            # `@b: three` in the call-site block wins over a mixin-produced
            # `@b: two`, regardless of source order.
            for r in rules:
                if isinstance(r, Declaration) and r.variable:
                    # Mark as mixin-produced unless an explicit override
                    # already set it (rare — mostly for synthetic decls
                    # that should not be deprioritised).
                    # Write-once-if-not-set semantics.
                    if not r._from_mixin_call:
                        r._from_mixin_call = True
            out.extend(rules)
        return out
    finally:
        ctx._mixin_call_depth -= 1


def _tag_splice_closures(
    node: Node,
    captured_frames: list[Ruleset],
    *,
    clear_reference: bool = False,
) -> Node:
    """Walk a node spliced out of a mixin invocation, tagging every
    MixinDefinition (top-level OR nested inside a Ruleset) with the
    captured frame stack so a later lookup of that mixin resolves
    variables against the calling mixin's scope.

    Without recursion into Rulesets, a pattern like

        .Mix(@p) {
            .out {
                @v: @p;
                .inner() { x: @v; }
            }
        }
        .caller { .Mix(1); .out.inner(); }

    leaves `.inner`'s closure unset — `.out.inner()` invocation can't
    find `@p` because `.Mix`'s arg frame has already popped. less.js
    captures the lexical scope on every nested MixinDefinition; we
    mirror that here.
    """
    if isinstance(node, MixinDefinition):
        # Every per-match annotation (`_closure_frames`,
        # `_source_ruleset_id`, etc.) is a typed field, so
        # `dataclasses.replace` carries them by itself.
        import dataclasses as _dc

        return _dc.replace(node, _splice_closure_frames=captured_frames)
    if isinstance(node, Ruleset):
        # Mutate in place — Rulesets spliced out of a mixin body are
        # already private clones (eval_ruleset built a fresh Ruleset
        # with new rule list). Walking children and patching nested
        # MixinDefinitions doesn't disturb shared state.
        #
        # When `clear_reference` is on (the mixin definition itself
        # came from an `@import (reference)` subtree), drop
        # `reference=True` on the way down: invoking such a mixin
        # from a non-reference call site produces real CSS that must
        # emit. less.js handles this by reseting the visibility bit
        # at splice time.
        if clear_reference and node.reference:
            node.reference = False
        node.rules = [_tag_splice_closures(r, captured_frames, clear_reference=clear_reference) for r in node.rules]
        return node
    if isinstance(node, AtRule) and node.body is not None:
        # Same logic: an at-rule cloned out of a reference subtree
        # loses the `_reference` skip tag once it's living in a
        # non-reference call site — but only when the splice originates
        # from a reference-sourced definition.
        if clear_reference:
            node._reference = False
        node.body = [_tag_splice_closures(r, captured_frames, clear_reference=clear_reference) for r in node.body]
        return node
    return node


def _descent_conditions_pass(defn: MixinDefinition, ctx: EvalContext) -> bool:
    """Check the CSS-guards (`condition`) of every parent Ruleset that a
    namespace descent walked through. Closure frames captured at descent
    time are the in-source parents (`#guarded when (false) { #deeper {
    .mixin() … } }` records `[#guarded, #deeper]`); evaluating each
    parent's condition here filters out leaves that are only reachable
    through a guard-false ancestor.
    """
    # Descent always records its parent chain in `_closure_frames`
    # (set by `_descend_namespace`); the splice-time variant carries
    # arg/live frames instead and is not relevant for parent-condition
    # filtering. Read the lookup-time chain directly.
    closure_frames = defn._closure_frames if defn._closure_frames is not None else []
    for frame in closure_frames:
        cond = getattr(frame, 'condition', None)
        if cond is None:
            continue
        if not eval_condition(cond, ctx):
            return False
    return True


def _select_mixin_branches(
    candidates: list[MixinDefinition],
    call: MixinCall,
    ctx: EvalContext,
) -> list[MixinDefinition]:
    """Guard evaluation with `default()` four-bucket classification.

    Each candidate's guard is evaluated twice, once with `default()=False`
    and once with `default()=True`. The pair classifies the candidate:

      * specific (matches both)        — non-default-dependent
      * wants_default (only `=True`)   — `when (default())` positive
      * rejects_default (only `=False`)— `when not(default())` negative
      * never (neither)                — e.g. `default() and (@x = 4)`
        with @x=5 — guard fails regardless of default()

    Selection mirrors less.js:
      * if specific is non-empty: emit specific + rejects_default
        (the latter fires only when a specific match exists, by intent
        of `not(default())`)
      * else: emit wants_default (the genuine fallback)

    No guard means "matches both" → always specific.
    """

    # `build_arg_frame` is expensive (it evaluates every actual arg);
    # cache by `id(defn)` so the two matches_with calls (default=True /
    # False) share one frame. Same applies to the closure-merged frame
    # stack we install before evaluating the guard.
    arg_frame_cache: dict[int, Ruleset] = {}
    merged_frames_cache: dict[int, list[Ruleset] | None] = {}

    def matches_with(default_value: bool, defn: MixinDefinition) -> bool:
        if defn.guard is None:
            return True
        key = id(defn)
        frame = arg_frame_cache.get(key)
        if frame is None:
            frame = build_arg_frame(defn, call, ctx)
            arg_frame_cache[key] = frame
        # Guards evaluate in DEFINITION scope (matches less.js). If the
        # def was found in a non-innermost frame, via namespace descent,
        # or spliced out of a calling mixin's body, swap to its captured
        # closure frames so the guard's variable lookups see the lexical
        # scope of the definition site, not the caller.
        if key in merged_frames_cache:
            merged_or_none = merged_frames_cache[key]
        else:
            closure_frames: list[Ruleset] = defn._splice_closure_frames or defn._closure_frames or []
            if closure_frames:
                seen_ids: set[int] = set()
                merged: list[Ruleset] = []
                for f in list(closure_frames) + ctx.frames:
                    fid = id(f)
                    if fid in seen_ids:
                        continue
                    seen_ids.add(fid)
                    merged.append(f)
                merged_or_none = merged
            else:
                merged_or_none = None
            merged_frames_cache[key] = merged_or_none
        saved_frames = ctx.frames
        default_state = ctx.default_state
        prev = default_state.set_default_value(default_value)
        if merged_or_none is not None:
            ctx.frames = merged_or_none
        ctx.push_frame(frame)
        default_state.set_in_guard(True)
        try:
            return eval_condition(defn.guard, ctx)
        finally:
            default_state.set_in_guard(False)
            ctx.pop_frame()
            if merged_or_none is not None:
                ctx.frames = saved_frames
            default_state.set_default_value(prev)

    specific: list[MixinDefinition] = []
    wants_default: list[MixinDefinition] = []
    rejects_default: list[MixinDefinition] = []
    for d in candidates:
        m_false = matches_with(False, d)
        m_true = matches_with(True, d)
        if m_false and m_true:
            specific.append(d)
        elif m_true and not m_false:
            wants_default.append(d)
        elif m_false and not m_true:
            rejects_default.append(d)
        # else: never — drop silently.
    if specific:
        # Preserve source order between specific and rejects_default
        # (less.js emits in declaration order regardless of bucket).
        order = {id(d): i for i, d in enumerate(candidates)}
        merged = specific + rejects_default
        merged.sort(key=lambda d: order[id(d)])
        return merged
    # No specific match: less.js raises `Ambiguous use of default()`
    # when more than one candidate's outcome depends on the
    # default-flag — `wants_default` AND `rejects_default` together,
    # multiple `wants_default`, or multiple `rejects_default`. With a
    # single surviving candidate the answer is unambiguous; return it
    # as-is.
    if len(wants_default) + len(rejects_default) > 1:
        args_text = _format_call_args(call, ctx)
        err = EvalError(f'Ambiguous use of `default()` found when matching for `{call.name}({args_text})`')
        err._less_js_name = 'RuntimeError'
        raise err
    return wants_default


def _expand_spread_call_args(call: MixinCall, ctx: EvalContext) -> MixinCall:
    """`.mixin(@list...)` → `.mixin(<list[0]>, <list[1]>, ...)`.

    Walk `call.args` for the spread shape (positional MixinArg whose
    value is a single Expression of `[Variable, Anonymous('...')]`).
    Resolve the variable, treat the result as a comma-list, and splice
    each Expression as its own positional MixinArg in the original
    arg's position. Non-spread args pass through.
    """
    if not any(_is_spread_arg(a) for a in call.args):
        return call
    new_args: list[MixinArg] = []
    for a in call.args:
        if not _is_spread_arg(a):
            new_args.append(a)
            continue
        var_name = _spread_var_name(a)
        try:
            resolved = ctx.lookup_variable_node(var_name)
        except UndefinedNameError:
            # Variable missing — keep the arg as-is so the matcher
            # reports a normal "no matching definition" error.
            new_args.append(a)
            continue
        for piece in _spread_pieces(resolved, a.index):
            new_args.append(MixinArg(index=a.index, name=None, value=piece))
    return MixinCall(
        index=call.index,
        name=call.name,
        args=new_args,
        important=call.important,
    )


def _is_spread_arg(a: MixinArg) -> bool:
    if a.name is not None:
        return False
    v = a.value
    if not isinstance(v, Value) or len(v.expressions) != 1:
        return False
    expr = v.expressions[0]
    if not isinstance(expr, Expression):
        return False
    vals = expr.values
    if len(vals) != 2:
        return False
    if not isinstance(vals[0], Variable):
        return False
    return isinstance(vals[1], Anonymous) and vals[1].value == '...'


def _spread_var_name(a: MixinArg) -> str:
    assert isinstance(a.value, Value)
    expr = a.value.expressions[0]
    assert isinstance(expr, Expression)
    var = expr.values[0]
    assert isinstance(var, Variable)
    return var.name


def _spread_pieces(resolved: Node, index: int) -> list[Value]:
    """Treat `resolved` as a comma-list and return each Expression as
    a single-Expression Value. Space-separated lists wrap one piece.
    """
    inner = resolved
    if isinstance(inner, Value):
        return [Value(index=e.index, expressions=[e]) for e in inner.expressions]
    if isinstance(inner, Expression):
        return [Value(index=inner.index, expressions=[inner])]
    return [Value(index=index, expressions=[Expression(index=index, values=[inner])])]


def _invoke_value_mixin_call(mc: MixinCall, ctx: EvalContext) -> list[Node]:
    """Invoke a value-position mixin call to get its body rules, ready
    for `[key]` resolution. Mirrors `_invoke_mixin_call` but returns
    rules without splicing or `!important` propagation.
    """

    from .lookups import _materialize_inner_variables
    from .rulesets import eval_ruleset

    candidates = find_mixin_matches(mc, ctx.frames, resolve_name=ctx.substitute_text)
    if not candidates:
        if name_exists(mc, ctx.frames, resolve_name=ctx.substitute_text):
            err = EvalError(f'No matching definition was found for `{mc.name}(...)`')
            err._less_js_name = 'RuntimeError'
            raise err
        raise UndefinedNameError(f'{mc.name} is undefined')
    selected = _select_mixin_branches(candidates, mc, ctx)
    if not selected:
        if all(c.is_ruleset_wrapper for c in candidates):
            return []
        raise EvalError(f'no matching definition for mixin {mc.name!r}')

    # less.js: invoke ALL matching definitions and concatenate their
    # body rules. For property-name lookups this naturally gives the
    # "last definition wins" semantics because `_lookup_in_rules` walks
    # in reverse. Variable-name lookups (`@var`) work the same way.
    all_rules: list[Node] = []
    for definition in selected:
        arg_frame = build_arg_frame(definition, mc, ctx)
        synthetic = Ruleset(index=definition.index, selectors=[], rules=definition.rules, root=False)
        ctx.push_frame(arg_frame)
        try:
            evaled = eval_ruleset(synthetic, ctx)
            # Materialize variable declarations inside the invocation
            # frame so subsequent lookups (which happen after the frame
            # is popped) see resolved text. Without this, mixin params
            # referenced by inner `@var: @param ...` decls would lose
            # their binding by lookup time.
            resolved_rules = _materialize_inner_variables(evaled.rules, ctx)
        finally:
            ctx.pop_frame()
        all_rules.extend(resolved_rules)
    return all_rules
