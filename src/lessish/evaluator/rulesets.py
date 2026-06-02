"""Ruleset evaluation.

`eval_ruleset` is the workhorse of the evaluator — it walks one
Ruleset's children, pushes a live frame so siblings see each other,
pre-invokes every MixinCall / VariableCall so their bindings are
visible up-front, then runs a main pass that splices the pre-invoked
output into the right source position. CSS-guards on the ruleset
itself (`when (...)`), `& when (...)` inline blocks, deferred `@import`
re-resolution, and the hoist of `&:extend(...)` clauses arriving via
mixin splicing all happen here.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from ..ast_nodes import (
    Anonymous,
    AtRule,
    Call,
    Declaration,
    Extend,
    MixinCall,
    MixinDefinition,
    Node,
    Ruleset,
    Selector,
    VariableCall,
)
from ..context import EvalContext
from ..errors import LessError, UndefinedNameError, UnsupportedFeatureError
from ..mixins import _ExtendStatement
from ..parser import parse_value_text
from ._drive import drive
from .atrules import eval_atrule
from .control_flow import _invoke_variable_call_gen, eval_condition
from .declarations import eval_declaration
from .dispatch import _EVAL_DISPATCH
from .helpers import _locate
from .imports import _resolve_deferred_import
from .mixins import _invoke_mixin_call_gen
from .selectors import _eval_selector
from .values import _unwrap_single


def _is_amp_when_block(rule: Node) -> bool:
    """True for a child Ruleset shaped `& when (cond) { ... }`: a single
    selector whose only element is a bare `&`, plus a guard condition.
    Such blocks splice their declarations into the parent rather than
    emitting as a separate ruleset.
    """
    if not isinstance(rule, Ruleset):
        return False
    if rule.condition is None:
        return False
    if len(rule.selectors) != 1:
        return False
    sel = rule.selectors[0]
    if len(sel.elements) != 1:
        return False
    return sel.elements[0].value == '&'


def eval_ruleset(rs: Ruleset, ctx: EvalContext) -> Ruleset:
    """Synchronous entry-point.

    Fast path: when `rs.rules` carries no `MixinCall` / `VariableCall`
    at this level, the eval can't start a deep `eval_ruleset ↔
    _invoke_mixin_call` cycle here — children that *do* carry
    invocations dispatch their own trampoline when evaluated. The
    `_eval_ruleset_direct` path skips the generator + driver
    scaffolding for the (very common) inv-free case; otherwise every
    leaf ruleset would pay the trampoline overhead.

    Slow path: when invocations are present, dispatch to the
    generator + driver so the `_invoke_mixin_call_gen` yields stay
    flat under `_drive.drive`.
    """
    rules = rs.rules
    needs_trampoline = False
    for r in rules:
        t = type(r)
        if t is MixinCall or t is VariableCall:
            needs_trampoline = True
            break
    if needs_trampoline:
        result = drive(_eval_ruleset_gen(rs, ctx), ctx)
        assert isinstance(result, Ruleset)
        return result
    return _eval_ruleset_direct(rs, ctx)


def _eval_ruleset_gen(rs: Ruleset, ctx: EvalContext) -> Generator[Any, Any, Ruleset]:
    """Selectors evaluate in the parent scope; rules evaluate after this
    ruleset is pushed so child rules see its variables.

    A CSS-guard (`rs.condition`) is evaluated in the parent scope before
    pushing — if it's false, the ruleset emits no rules. Mirrors
    less.js: `.x when (cond) { ... }` is suppressed unless `cond` holds.

    MixinDefinitions in the rule list pass through unchanged — they stay
    in `rules` (so name lookup works) but are filtered at emit. MixinCalls
    are resolved here: find all matching definitions, invoke each, and
    splice the resulting (already-evaluated) rules into `new_rules`.

    Generator. Yields child generators for nested eval cycles (mixin
    bodies, nested rulesets, amp-when blocks); the trampoline in
    `_drive.drive` pumps them and `send()`s results back. Direct
    recursion through Python's call stack only happens for the
    shallow non-cycle paths (eval_value precedence climb, etc.).
    """

    new_selectors: list[Selector] = []
    for sel in rs.selectors:
        new_selectors.extend(_eval_selector(sel, ctx))
    if rs.condition is not None:
        # CSS-guard (vs parametric-mixin guard). Flag the active
        # `default()` so it can raise the correct SyntaxError instead
        # of falling back to the verbatim-passthrough path used for
        # `default()` in value position.
        ctx.default_state.set_in_css_guard(True)
        try:
            cond_passed = eval_condition(rs.condition, ctx)
        finally:
            ctx.default_state.set_in_css_guard(False)
        if not cond_passed:
            # Guard failed — drop the body entirely. We keep the selector
            # text but strip any `:extend(...)` clauses so the extend
            # pass doesn't apply them (`.x:extend(.y) when (false) {}`
            # must not extend `.y` — mirrors less.js).
            return Ruleset(
                index=rs.index,
                selectors=[Selector(index=s.index, elements=s.elements, extend_list=[]) for s in new_selectors],
                rules=[],
                root=rs.root,
                reference=rs.reference,
            )
    # Build the frame so subsequent siblings see (a) all definitions
    # from the original block upfront — variables, MixinDefinitions,
    # nested Rulesets — and (b) the spliced output of mixin invocations
    # processed earlier in the iteration. less.js does this — it's
    # what makes `#ns.mixin(); .colors[primary]` resolve `.colors` from
    # the just-invoked mixin's exposed defs.
    new_rules: list[Node] = []
    live_frame = Ruleset(
        index=rs.index,
        selectors=rs.selectors,
        rules=new_rules,
        root=rs.root,
        reference=rs.reference,
    )
    ctx.push_frame(live_frame)
    # Mark this Ruleset as "currently being evaluated" so mixin calls
    # inside its body don't match the Ruleset itself as a 0-arg mixin.
    # `.x { .x(); }` should invoke the sibling MixinDefinition `.x()`
    # (if any), never re-enter the same ruleset. less.js convention.
    invoking_ids = ctx._invoking_ruleset_ids
    invoking_ids.add(id(rs))
    try:
        # Seed the live frame with the *original* providers (variable
        # decls, mixin defs, nested rulesets) so forward references and
        # later-in-source lookups continue to work. We use the raw rules
        # — eval_ruleset will descend into them again as iteration reaches
        # them and the spliced output replaces these placeholders for
        # CSS emit (the order of `new_rules` is recomputed during the
        # main loop below).
        seeded = list(rs.rules)
        new_rules.extend(seeded)

        # Pre-pass: invoke every MixinCall / VariableCall and capture
        # the spliced rules. Adding the invocation's variable
        # declarations to `new_rules` (the live frame) up-front makes
        # them visible to source-position-earlier declarations in the
        # same Ruleset. Mirrors less.js's late-binding model: a
        # `color: @mix; .mixin();` block where `.mixin` defines
        # `@mix: #989` resolves `color` to `#989`. The actual splice
        # into output order still happens in the main loop below; this
        # pre-pass only seeds the frame.
        #
        # Spliced rules are inserted *at the call's source position* so
        # downstream mixin lookups see imported defs in the correct
        # order. Example: `#ns(); .m();` where `#ns` brings a `.m()` def
        # — the `.m()` call must match the imported def BEFORE a local
        # `.m()` def defined later in the same block.
        # Early reject: `@plugin "…"` is structurally a body-less AtRule,
        # which would normally only surface its UnsupportedFeatureError
        # later (when `eval_node` reaches it in the main pass below).
        # The MixinCall pre-pass that follows can trip over a
        # plugin-defined function call first — turning the clear
        # "@plugin not supported" diagnostic into a secondary
        # "Function 'test-X' did not return a root node" error pointing
        # at line 120 instead of line 2. Catch it here, in source order,
        # so the user sees the root cause first.
        for rule in rs.rules:
            if isinstance(rule, AtRule) and rule.name == '@plugin':
                err = UnsupportedFeatureError(
                    f'@plugin {rule.prelude} requires JavaScript evaluation; not supported by lessish'
                )
                _locate(err, rule.index, ctx)
                raise err

        pre_invoked: dict[int, list[Node]] = {}
        # `new_rules` starts as a copy of `rs.rules` (see the `seeded`
        # extend above), so each source rule's initial position in
        # `new_rules` matches its iteration index. Splicing `invoked`
        # after that position shifts every later rule right by
        # `len(invoked)`. Tracking the cursor inline keeps the pass
        # at O(N + total spliced output) — an `new_rules.index(rule)`
        # per pre-invoke would make it O(N²).
        #
        # Splice invalidates the variable/mixin index caches on
        # `live_frame`: inserting into the *middle* of the rules list
        # shifts old entries right, and any incremental cache update
        # that scans `[cached_len, new_len)` would miss the actually-
        # new entries (which sit at the splice point, *before* the
        # shifted tail). `_variable_index_for` /
        # `_mixin_index_for` only stay incrementally safe under
        # append-only growth; splicing in the middle violates that
        # invariant, so we drop the cache and let the next lookup
        # rebuild from scratch.
        target_pos = 0
        for rule in rs.rules:
            if isinstance(rule, MixinCall):
                try:
                    # Yield to trampoline — the mixin body evaluation
                    # is what drives the deep recursion cycle, so we
                    # hand it off as a child generator instead of
                    # recursing through Python's call stack.
                    invoked = yield _invoke_mixin_call_gen(rule, ctx)
                except LessError as e:
                    _locate(e, rule.index, ctx, source=rule._source)
                    raise
                pre_invoked[id(rule)] = invoked
                if invoked:
                    new_rules[target_pos + 1 : target_pos + 1] = invoked
                    live_frame._var_index_cache = None
                    live_frame._mixin_index_cache = None
                target_pos += 1 + len(invoked)
            elif isinstance(rule, VariableCall):
                try:
                    invoked = yield _invoke_variable_call_gen(rule, ctx)
                except LessError as e:
                    _locate(e, rule.index, ctx)
                    raise
                pre_invoked[id(rule)] = invoked
                if invoked:
                    new_rules[target_pos + 1 : target_pos + 1] = invoked
                    live_frame._var_index_cache = None
                    live_frame._mixin_index_cache = None
                target_pos += 1 + len(invoked)
            else:
                target_pos += 1

        # Main pass into a *separate* output list, then swap it into
        # the live frame at the end. The live frame keeps seeing all
        # original rules + pre-invoked splices while individual rules
        # evaluate.
        out_rules: list[Node] = []
        for rule in rs.rules:
            if _is_amp_when_block(rule):
                # `& when (cond) { ... }` inside a parent ruleset
                # inlines its declarations into the parent block when
                # the guard passes. The body evaluates in its own frame
                # so locally-declared variables don't leak into sibling
                # rules. K1.
                assert isinstance(rule, Ruleset) and rule.condition is not None
                if not eval_condition(rule.condition, ctx):
                    continue
                synthetic = Ruleset(
                    index=rule.index,
                    selectors=[],
                    rules=rule.rules,
                    root=False,
                )
                evaled_inner = yield _eval_ruleset_gen(synthetic, ctx)
                out_rules.extend(evaled_inner.rules)
                continue
            if isinstance(rule, MixinDefinition):
                out_rules.append(rule)
                continue
            if isinstance(rule, MixinCall):
                out_rules.extend(pre_invoked.get(id(rule), []))
                continue
            if isinstance(rule, VariableCall):
                out_rules.extend(pre_invoked.get(id(rule), []))
                continue
            if isinstance(rule, AtRule) and rule._deferred_import:
                # `@import "@{var}.less"` deferred from the importer pass
                # — substitute the path now (variables are in scope), call
                # back to the Importer to fetch+parse the target file,
                # and recursively eval the resulting rules in the current
                # frame so they participate in normal scoping.
                out_rules.extend(_resolve_deferred_import(rule, ctx))
                continue
            # Inlined `eval_node` dispatch. The three hottest types are
            # called directly to skip one Python frame per nested rule;
            # everything else falls through to the dispatch table.
            # `isinstance` is used (rather than `type(rule) is X`) so
            # mypy narrows the type for each branch; no perf delta
            # since none of these classes have subclasses.
            if isinstance(rule, Declaration):
                out_rules.append(eval_declaration(rule, ctx))
            elif isinstance(rule, Ruleset):
                # Nested ruleset → yield as child generator so the
                # deep eval_ruleset ↔ _invoke_mixin_call cycle stays
                # flat under the trampoline.
                evaled_nested = yield _eval_ruleset_gen(rule, ctx)
                out_rules.append(evaled_nested)
            elif isinstance(rule, AtRule):
                out_rules.append(eval_atrule(rule, ctx))
            else:
                handler = _EVAL_DISPATCH.get(type(rule))
                if handler is not None:
                    out_rules.append(handler(rule, ctx))  # type: ignore[operator]
                else:
                    out_rules.append(rule)
        # Eager validation pass for variable declarations: less.js
        # catches `@a: f(@a)` self-recursion when the value contains a
        # function call (which forces evaluation of args). We trigger
        # the same check by looking up each variable once. Cycles raise
        # `UndefinedNameError: Recursive variable definition for @x`.
        for rule in out_rules:
            if (
                isinstance(rule, Declaration)
                and rule.variable
                and isinstance(rule.value, Anonymous)
                and _value_text_has_recursion_trigger(rule.value.value, rule.name)
            ):
                try:
                    ctx.lookup_variable_node(rule.name)
                except UndefinedNameError as e:
                    if 'Recursive' in e.message:
                        # Anchor at the value's start; wrap with function
                        # name only if the inner eval_call didn't already
                        # do it for us.
                        if 'Error evaluating function' not in e.message:
                            parsed = parse_value_text(rule.value.value)
                            unwrapped = _unwrap_single(parsed)
                            if isinstance(unwrapped, Call):
                                e.message = f'Error evaluating function `{unwrapped.name}`: {e.message}'
                        e.location = None
                        _locate(e, rule.value.index, ctx)
                        raise
    finally:
        invoking_ids.discard(id(rs))
        ctx.pop_frame()
    new_rules = out_rules
    # Hoist statement-form `&:extend(...)` clauses that came in via
    # mixin splicing. At parse time we leave `_ExtendStatement` inside
    # MixinDefinition bodies so the extend rides with the splice;
    # here we transfer the extend onto every selector of the call-site
    # ruleset (mirrors less.js: `.container { .clearfix-mixin(); }`
    # makes `.container` extend `.clearfix`). If this Ruleset has no
    # selectors (synthetic body wrapper used for mixin invocations),
    # keep the `_ExtendStatement` in rules so it rides outward to the
    # next caller.
    if new_selectors:
        hoisted_extends: list[Extend] = []
        filtered: list[Node] = []
        for r in new_rules:
            if isinstance(r, _ExtendStatement):
                hoisted_extends.extend(r.extends)
            else:
                filtered.append(r)
        if hoisted_extends:
            new_rules = filtered
            new_selectors = [
                Selector(
                    index=sel.index,
                    elements=sel.elements,
                    extend_list=sel.extend_list
                    + [Extend(index=e.index, target=e.target, option=e.option) for e in hoisted_extends],
                )
                for sel in new_selectors
            ]
    # Post-pass: mark Declarations whose source position was accessed
    # via `$prop` somewhere during this ruleset's evaluation. This is
    # detected only after the producing declaration has already been
    # cloned, so we patch the clone here. The emitter consults
    # `important_origin == 'accessed'` to drop the space before
    # `!important`.
    accessed = ctx._property_accessed_indexes
    if accessed:
        for i, r in enumerate(new_rules):
            if isinstance(r, Declaration) and r.important and r.important_origin == 'source' and r.index in accessed:
                new_rules[i] = Declaration(
                    index=r.index,
                    name=r.name,
                    value=r.value,
                    important=r.important,
                    variable=r.variable,
                    merge=r.merge,
                    important_origin='accessed',
                )
    evaled_rs = Ruleset(
        index=rs.index,
        selectors=new_selectors,
        rules=new_rules,
        root=rs.root,
        reference=rs.reference,
    )
    # Propagate the import-time `_source` tag so source-map generation
    # can resolve indices against the originating sub-file. Without
    # this, every node from an `@import`'d Ruleset falls back to the
    # entry-point source and produces wrong line/column mappings.
    src_tag = rs._source
    if src_tag is not None:
        evaled_rs._source = src_tag
    return evaled_rs


def _eval_ruleset_direct(rs: Ruleset, ctx: EvalContext) -> Ruleset:
    """Synchronous mirror of `_eval_ruleset_gen` for the fast path
    described in `eval_ruleset` — `rs.rules` has no `MixinCall` /
    `VariableCall`, so the pre-pass is a no-op and every nested
    eval cycle can be a direct `eval_ruleset(...)` call instead of
    a `yield`.

    Behavioural invariant: this function MUST stay in lock-step with
    `_eval_ruleset_gen`. Any change to selector/condition handling,
    frame seeding, amp-when splice, `_ExtendStatement` hoisting,
    `_property_accessed_indexes` repatching, or `_source` propagation
    needs the same change in both. The gen version is the canonical
    one — read it first when reasoning about behaviour.
    """
    new_selectors: list[Selector] = []
    for sel in rs.selectors:
        new_selectors.extend(_eval_selector(sel, ctx))
    if rs.condition is not None:
        ctx.default_state.set_in_css_guard(True)
        try:
            cond_passed = eval_condition(rs.condition, ctx)
        finally:
            ctx.default_state.set_in_css_guard(False)
        if not cond_passed:
            return Ruleset(
                index=rs.index,
                selectors=[Selector(index=s.index, elements=s.elements, extend_list=[]) for s in new_selectors],
                rules=[],
                root=rs.root,
                reference=rs.reference,
            )
    new_rules: list[Node] = []
    live_frame = Ruleset(
        index=rs.index,
        selectors=rs.selectors,
        rules=new_rules,
        root=rs.root,
        reference=rs.reference,
    )
    ctx.push_frame(live_frame)
    invoking_ids = ctx._invoking_ruleset_ids
    invoking_ids.add(id(rs))
    try:
        new_rules.extend(rs.rules)
        # `@plugin` early-reject — same diagnostic as the gen path.
        # Stays before the main pass so the error anchors at the
        # plugin's source position, not at some downstream surprise.
        for rule in rs.rules:
            if isinstance(rule, AtRule) and rule.name == '@plugin':
                err = UnsupportedFeatureError(
                    f'@plugin {rule.prelude} requires JavaScript evaluation; not supported by lessish'
                )
                _locate(err, rule.index, ctx)
                raise err
        # No MixinCall/VariableCall pre-pass — the fast-path
        # precondition rules them out. Main pass starts straight in.
        out_rules: list[Node] = []
        for rule in rs.rules:
            if _is_amp_when_block(rule):
                assert isinstance(rule, Ruleset) and rule.condition is not None
                if not eval_condition(rule.condition, ctx):
                    continue
                synthetic = Ruleset(
                    index=rule.index,
                    selectors=[],
                    rules=rule.rules,
                    root=False,
                )
                evaled_inner = eval_ruleset(synthetic, ctx)
                out_rules.extend(evaled_inner.rules)
                continue
            if isinstance(rule, MixinDefinition):
                out_rules.append(rule)
                continue
            if isinstance(rule, AtRule) and rule._deferred_import:
                out_rules.extend(_resolve_deferred_import(rule, ctx))
                continue
            if isinstance(rule, Declaration):
                out_rules.append(eval_declaration(rule, ctx))
            elif isinstance(rule, Ruleset):
                # Nested ruleset → call `eval_ruleset` directly; if it
                # carries invocations itself, *its* call site decides
                # to dispatch through the trampoline.
                out_rules.append(eval_ruleset(rule, ctx))
            elif isinstance(rule, AtRule):
                out_rules.append(eval_atrule(rule, ctx))
            else:
                handler = _EVAL_DISPATCH.get(type(rule))
                if handler is not None:
                    out_rules.append(handler(rule, ctx))  # type: ignore[operator]
                else:
                    out_rules.append(rule)
        # Eager validation pass — mirrors gen path.
        for rule in out_rules:
            if (
                isinstance(rule, Declaration)
                and rule.variable
                and isinstance(rule.value, Anonymous)
                and _value_text_has_recursion_trigger(rule.value.value, rule.name)
            ):
                try:
                    ctx.lookup_variable_node(rule.name)
                except UndefinedNameError as e:
                    if 'Recursive' in e.message:
                        if 'Error evaluating function' not in e.message:
                            parsed = parse_value_text(rule.value.value)
                            unwrapped = _unwrap_single(parsed)
                            if isinstance(unwrapped, Call):
                                e.message = f'Error evaluating function `{unwrapped.name}`: {e.message}'
                        e.location = None
                        _locate(e, rule.value.index, ctx)
                        raise
    finally:
        invoking_ids.discard(id(rs))
        ctx.pop_frame()
    new_rules = out_rules
    if new_selectors:
        hoisted_extends: list[Extend] = []
        filtered: list[Node] = []
        for r in new_rules:
            if isinstance(r, _ExtendStatement):
                hoisted_extends.extend(r.extends)
            else:
                filtered.append(r)
        if hoisted_extends:
            new_rules = filtered
            new_selectors = [
                Selector(
                    index=sel.index,
                    elements=sel.elements,
                    extend_list=sel.extend_list
                    + [Extend(index=e.index, target=e.target, option=e.option) for e in hoisted_extends],
                )
                for sel in new_selectors
            ]
    accessed = ctx._property_accessed_indexes
    if accessed:
        for i, r in enumerate(new_rules):
            if isinstance(r, Declaration) and r.important and r.important_origin == 'source' and r.index in accessed:
                new_rules[i] = Declaration(
                    index=r.index,
                    name=r.name,
                    value=r.value,
                    important=r.important,
                    variable=r.variable,
                    merge=r.merge,
                    important_origin='accessed',
                )
    evaled_rs = Ruleset(
        index=rs.index,
        selectors=new_selectors,
        rules=new_rules,
        root=rs.root,
        reference=rs.reference,
    )
    src_tag = rs._source
    if src_tag is not None:
        evaled_rs._source = src_tag
    return evaled_rs


def _value_text_has_recursion_trigger(text: str, var_name: str) -> bool:
    """True when `text` contains both the variable's own name AND a
    function call — the case where less.js eagerly evaluates and
    detects recursion. Pure-substitution cycles (`@a: @a;`) stay lazy
    in less.js too, so we mirror that and only trigger here when a
    function-call wrapper would force eager evaluation.
    """
    if var_name not in text:
        return False
    return '(' in text
