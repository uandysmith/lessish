"""[key] lookup + `$prop` property access + `@var` resolution.

Three public entry points:

* `eval_lookup`            — `target[key]` value form (incl. variable,
                              property, and `$` indirection key kinds)
* `eval_property_access`   — `$name` value position
* `eval_variable`          — `@name` (and `@@name`) value position

Lookups depend on the rule list produced by mixin / detached-ruleset
invocations. `eval_ruleset` and `_invoke_value_mixin_call` are imported
at module top — `rulesets` triggers `mixins` to load through its own
top-level import, so by the time the `from .mixins import ...` line
runs, mixins has fully loaded.

`_eval_dr_body` (DetachedRuleset invocation) lives here too — it's
used by both control_flow's `_invoke_variable_call` and lookups'
`_resolve_lookup_target`, and the body-rule-list filtering it does
(`_is_local_to_dr`) shares semantics with the other lookup helpers.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from ..ast_nodes import (
    Anonymous,
    Declaration,
    DetachedRuleset,
    Lookup,
    MixinCall,
    Node,
    PropertyAccess,
    Ruleset,
    Variable,
)
from ..context import EvalContext
from ..errors import EvalError, LessError, ParseError, UndefinedNameError
from ..parser import parse_value_text
from ..visitors import value_to_css
from ._drive import drive
from .dispatch import eval_node
from .mixins import _invoke_value_mixin_call
from .rulesets import _eval_ruleset_gen, eval_ruleset
from .values import _unwrap_single, eval_value


def _eval_dr_body(dr: DetachedRuleset, ctx: EvalContext, *, index: int) -> list[Node]:
    """Synchronous entry-point for `_eval_dr_body_gen` — drives the
    generator to completion. Use the generator form directly from
    other generators in the iterative-evaluator cycle.
    """
    result = drive(_eval_dr_body_gen(dr, ctx, index=index), ctx)
    assert isinstance(result, list)
    return result


def _eval_dr_body_gen(dr: DetachedRuleset, ctx: EvalContext, *, index: int) -> Generator[Any, Any, list[Node]]:
    """Invoke a DetachedRuleset: evaluate its rules and return them.

    If the DR carries `_captured_frames` (set at declaration / arg-bind
    time), prepend them to `ctx.frames` so its body's variable lookups
    resolve in the DR's *lexical* scope first. The invoke-site scope
    stays accessible for names that don't shadow the closure.

    Variable / MixinDefinition declarations inside the body are local
    to the DR's invocation frame — they're visible to other rules
    *within* the body but don't leak into the splice. Filter them out
    of the returned list so a DR like `{ @b: x; color: @b; }` emits
    only `color: x` and doesn't shadow a sibling `@b` at the call site.

    Generator. Yields a child `_eval_ruleset_gen` for the synthetic
    body; the trampoline pumps it.
    """
    captured: list[Ruleset] = dr._captured_frames or []
    saved_frames = ctx.frames
    if captured:
        # Prepend captured frames but dedupe against current — root and
        # any other still-live frames are shared by identity, and a
        # double-listed frame would make `find_mixin_matches` return
        # each definition there twice (output duplication).
        seen: set[int] = set()
        merged: list[Ruleset] = []
        for f in list(captured) + ctx.frames:
            if id(f) in seen:
                continue
            seen.add(id(f))
            merged.append(f)
        ctx.frames = merged
    try:
        synthetic = Ruleset(
            index=index,
            selectors=[],
            rules=dr.rules,
            root=False,
        )
        evaled = yield _eval_ruleset_gen(synthetic, ctx)
        return [r for r in evaled.rules if not _is_local_to_dr(r)]
    finally:
        ctx.frames = saved_frames


def _is_local_to_dr(rule: Node) -> bool:
    """True for nodes that belong to the DR's local scope only —
    variable declarations. Mirrors less.js where a DR invocation
    splices regular declarations *and* MixinDefinitions ("unlocking
    mixins" pattern) but keeps variable bindings inside the DR's
    body so they don't shadow sibling variables at the call site.
    """
    return isinstance(rule, Declaration) and rule.variable


def eval_property_access(p: PropertyAccess, ctx: EvalContext) -> Node:
    """Resolve `$prop` to the value of property `prop` in scope.

    Walks the frame stack innermost-first; within each frame, collects
    every Declaration with the matching name in source order. The
    innermost frame that has any match wins. The result depends on
    what's in that frame:

    * Single declaration (or multiple but none merge-tagged): the LAST
      one wins ("last definition wins" — matches less.js's lazy lookup).
    * Multiple declarations with at least one merge-tagged (`+:` /
      `+_:`): merge them with the same semantics as the post-eval
      `merge_rules` pass — `+:` segments comma-join, `+_:` segments
      space-append to the prior segment. less.js does this in its
      `Property.prototype.eval` (when accessor sees a merged property)
      so that `$bg` inside a nested rule sees the same `red, foo` value
      the parent rule will end up emitting.

    The looked-up value is re-parsed and re-evaluated to handle chains
    like `color: red; bg: $color` resolving cleanly.

    Recursion guard: when the same property name is already being
    resolved on this stack (`color: $color` style cycle), raise
    `UndefinedNameError('Recursive property reference for $name')`
    matching less.js's wording.
    """
    matches = _find_property_matches(ctx, p.name)
    if not matches:
        # Two failure modes: pure "not found" vs "all candidates were
        # excluded as already-being-resolved". `_current_decl_ids`
        # excludes the latter — if any frame held a candidate with our
        # target name, surface as Recursive (less.js wording) so the
        # error message matches.
        if _has_property_decl(ctx, p.name):
            raise UndefinedNameError(f'Recursive property reference for ${p.name}')
        raise UndefinedNameError(f"Property '${p.name}' is undefined")
    # Merge path: 2+ declarations and at least one is merge-tagged.
    # Otherwise fall through to single-decl (last-wins) path.
    if len(matches) >= 2 and any(d.merge for d in matches):
        text = _build_merged_value_text(matches)
        base = matches[0].value.index if isinstance(matches[0].value, Anonymous) else 0
        accessed = ctx._property_accessed_indexes
        for d in matches:
            accessed.add(d.index)
        # Recursion guard: skip every Declaration that contributed to
        # the merge while evaluating the merged text, so a nested
        # `$prop` referencing the same name doesn't loop back into them.
        skip_set = {id(d) for d in matches}
        ctx._current_decl_ids |= skip_set
        try:
            result = _unwrap_single(eval_value(parse_value_text(text, base_offset=base), ctx))
        finally:
            ctx._current_decl_ids -= skip_set
        # !important from any member promotes (matches merge_rules).
        if any(d.important for d in matches):
            result._lookup_important = True
        return result
    decl = matches[-1]
    text = decl.value.value if isinstance(decl.value, Anonymous) else ''
    base = decl.value.index if isinstance(decl.value, Anonymous) else 0
    # Record that this declaration was `$prop`-accessed so the emitter
    # can drop the space before `!important` (less.js quirk:
    # `$prop`-accessed declarations emit `red!important`, others emit
    # `red !important`). Identity is keyed by source index since the
    # post-eval pass sees a clone rather than this original.
    ctx._property_accessed_indexes.add(decl.index)
    # Skip the found Declaration during nested $prop lookups in its
    # value (chain of D1 → D2 → … would otherwise loop back).
    ctx._current_decl_ids.add(id(decl))
    try:
        result = _unwrap_single(eval_value(parse_value_text(text, base_offset=base), ctx))
    finally:
        ctx._current_decl_ids.discard(id(decl))
    # `$prop` propagates the source declaration's `!important` to the
    # caller via the same marker `eval_lookup` uses for mixin-call
    # lookups. The promotion is read by `_eval_declaration_inner`.
    if decl.important:
        result._lookup_important = True
    return result


def _build_merged_value_text(decls: list[Declaration]) -> str:
    """Concatenate the value-text of `decls` using less.js's merge
    semantics: `+_:` declarations append to the previous segment with
    a space; everything else (`+:` or plain) starts a new comma
    segment. Mirrors `merge_one` in visitors.py — single source of
    truth for the merge format.

    Plain (non-merge) declarations contribute a segment too, which is
    the right semantics when at least one tag in the group IS a merge:
    `bg: red; bg+: foo;` should produce `red, foo` for `$bg`, exactly
    what the post-eval `merge_rules` pass emits.
    """
    segments: list[str] = []
    for d in decls:
        v = d.value.value if isinstance(d.value, Anonymous) else ''
        if d.merge == '+_' and segments:
            segments[-1] = segments[-1] + ' ' + v
        else:
            segments.append(v)
    return ', '.join(segments)


def _has_property_decl(ctx: EvalContext, name: str) -> bool:
    """True if any frame contains a non-variable Declaration named `name`
    — used to disambiguate Undefined from Recursive when
    `_find_property_matches` returned an empty list because every
    candidate was excluded by `_current_decl_ids`.
    """
    for frame in ctx.frames:
        for rule in frame.rules:
            if isinstance(rule, Declaration) and not rule.variable and rule.name == name:
                return True
    return False


def eval_lookup(lk: Lookup, ctx: EvalContext) -> Node:
    """Evaluate `target[key]`. Resolve `target` to a list of rules
    (invoking captured mixin calls or unwrapping a DetachedRuleset)
    and look up `key` inside.

    A captured MixinCall flagged `!important` propagates that flag to
    the looked-up value via a wrapping marker (read by eval_declaration).
    """
    rules = _resolve_lookup_target(lk.target, ctx)
    result = _lookup_in_rules(rules, lk.key_kind, lk.key, ctx)
    # Check whether the target carries `!important` (captured at parse).
    if _lookup_target_is_important(lk.target, ctx):
        result._lookup_important = True
    return result


def _lookup_target_is_important(target: Node, ctx: EvalContext) -> bool:
    """True if the target is a MixinCall (direct or via variable) with
    `important=True`. Mirrors less.js: `@colors: .colors() !important;`
    then `@colors[primary]` produces an important declaration.
    """

    if isinstance(target, MixinCall):
        return target.important
    if isinstance(target, Variable):
        decl = ctx._find_variable(target.name)
        if decl is None:
            return False
        # `@var: <mixin call> !important;` — the block parser strips
        # `!important` from the value text and sets Declaration.important.
        if decl.important:
            return True
        if not isinstance(decl.value, Anonymous):
            return False
        inner = _unwrap_single(parse_value_text(decl.value.value))
        if isinstance(inner, MixinCall):
            return inner.important
    return False


def _resolve_lookup_target(target: Node, ctx: EvalContext) -> list[Node]:
    """Resolve a Lookup target to a list of rules (declarations,
    nested mixin defs, etc.) ready for key matching.
    """

    resolved = _unwrap_single(eval_node(target, ctx))
    if isinstance(resolved, MixinCall):
        return _invoke_value_mixin_call(resolved, ctx)
    if isinstance(resolved, DetachedRuleset):
        synthetic = Ruleset(index=resolved.index, selectors=[], rules=resolved.rules, root=False)
        evaled = eval_ruleset(synthetic, ctx)
        return evaled.rules
    if isinstance(resolved, Ruleset):
        # A nested Lookup result that returned a Ruleset (rare path).
        return resolved.rules
    # Variable target that didn't resolve to a callable — less.js wording.
    if isinstance(target, Variable):
        raise EvalError(f'Could not evaluate variable call {target.name}')
    raise EvalError('lookup target is not a namespace, mixin call, or detached ruleset')


def _materialize_inner_variables(rules: list[Node], ctx: EvalContext) -> list[Node]:
    """Eagerly resolve variable declarations in `rules` so their value
    text reflects the current frame stack (typically a mixin's arg
    frame about to be popped). Non-variable declarations are returned
    unchanged.

    Each materialised declaration becomes immediately visible to the
    next one through a synthetic frame built from `rules` — needed
    for chains like `@val: 100px; @min: (min-width: @val); @max: …
    @min`. less.js's mixin invocation evaluates these sequentially
    in the body's own scope; we mirror by pushing a frame whose
    rule list grows as each value resolves.
    """

    out: list[Node] = []
    inner_frame_rules: list[Node] = []
    inner_frame = Ruleset(index=0, selectors=[], rules=inner_frame_rules, root=False)
    ctx.push_frame(inner_frame)
    try:
        for r in rules:
            if isinstance(r, Declaration) and r.variable and isinstance(r.value, Anonymous):
                try:
                    parsed = parse_value_text(r.value.value)
                    evaled = eval_value(parsed, ctx)
                    new_text = value_to_css(evaled)
                except (LessError, ParseError):
                    # If the value can't fully resolve here (e.g. references
                    # something only visible at the call site), keep the raw
                    # text so lookup-time resolution can try again.
                    out.append(r)
                    inner_frame_rules.append(r)
                    continue
                new_decl = Declaration(
                    index=r.index,
                    name=r.name,
                    value=Anonymous(index=r.value.index, value=new_text),
                    important=r.important,
                    variable=True,
                    merge=r.merge,
                )
                out.append(new_decl)
                inner_frame_rules.append(new_decl)
            else:
                out.append(r)
                inner_frame_rules.append(r)
    finally:
        ctx.pop_frame()
    return out


def _lookup_in_rules(rules: list[Node], key_kind: str, key: str, ctx: EvalContext) -> Node:
    """Find a declaration matching `key` in `rules` and return its
    evaluated value.

    `key_kind`:
      'name'           → bare-IDENT / $prop lookup (last non-variable decl wins)
      'var'            → @var lookup (last variable decl wins; `key` includes leading `@`)
      'var-indirect'   → resolve @@key first, then recurse as 'var'
      'prop-indirect'  → resolve $@key first, then recurse as 'name'
    """

    if key_kind == 'var-indirect':
        # `@@x` — resolve `@x` first to a name, then look up the variable.
        indirect = _unwrap_single(ctx.lookup_variable_node('@' + key[2:]))
        resolved_name = value_to_css(indirect).strip()
        if len(resolved_name) >= 2 and resolved_name[0] == resolved_name[-1] and resolved_name[0] in ('"', "'"):
            resolved_name = resolved_name[1:-1]
        return _lookup_in_rules(rules, 'var', '@' + resolved_name, ctx)

    if key_kind == 'prop-indirect':
        # `$@x` — resolve `@x` first, then property-lookup with that name.
        indirect = _unwrap_single(ctx.lookup_variable_node(key))
        resolved_name = value_to_css(indirect).strip()
        if len(resolved_name) >= 2 and resolved_name[0] == resolved_name[-1] and resolved_name[0] in ('"', "'"):
            resolved_name = resolved_name[1:-1]
        return _lookup_in_rules(rules, 'name', resolved_name, ctx)

    if key_kind == 'last':
        # `target[]` — less.js's `rs.lastDeclaration()`: return the
        # value of the last Declaration (variable or not) in the
        # rule list. Used by patterns like `.mixin()[]` to grab the
        # mixin's final assignment, or `@dr[]` to read the only
        # declaration in a single-line DR.
        for r in reversed(rules):
            if isinstance(r, Declaration):
                text = r.value.value if isinstance(r.value, Anonymous) else ''
                return _unwrap_single(eval_value(parse_value_text(text), ctx))
        raise UndefinedNameError('no declarations to look up via `[]`')

    if key_kind == 'name':
        for r in reversed(rules):
            if isinstance(r, Declaration) and not r.variable and r.name == key:
                text = r.value.value if isinstance(r.value, Anonymous) else ''
                return _unwrap_single(eval_value(parse_value_text(text), ctx))
        raise UndefinedNameError(f'property "{key}" not found')

    if key_kind == 'var':
        # `key` already has leading `@`.
        for r in reversed(rules):
            if isinstance(r, Declaration) and r.variable and r.name == key:
                text = r.value.value if isinstance(r.value, Anonymous) else ''
                return _unwrap_single(eval_value(parse_value_text(text), ctx))
        raise UndefinedNameError(f'variable {key} not found')

    raise EvalError(f'unknown lookup key kind: {key_kind}')


def _find_property_matches(ctx: EvalContext, name: str) -> list[Declaration]:
    """Collect every Declaration matching `name` in the innermost frame
    that has at least one match. Returned in source order so the merge
    branch in `eval_property_access` can concatenate segments correctly.

    Frames further out aren't consulted once an inner match exists.
    Skips any declaration whose value is currently being evaluated, so
    `color: $color` cycles can inherit from an outer-scope `color`.
    """
    skip_ids = ctx._current_decl_ids
    for frame in ctx.frames:
        matches: list[Declaration] = []
        for rule in frame.rules:
            if isinstance(rule, Declaration) and not rule.variable and rule.name == name and id(rule) not in skip_ids:
                matches.append(rule)
        if matches:
            return matches
    return []


def eval_variable(v: Variable, ctx: EvalContext) -> Node:
    """Resolve a `@name` reference. Trivial Value/Expression wrappers
    around the resolved node are flattened so callers see the simplest
    node possible (Dimension, Color, ...).

    `@@name` is a variable-variable: look up `@name`, treat its value as
    a variable name, then look that up. less.js calls this 'reference to
    a variable named after another variable's value'.
    """

    name = v.name
    if name.startswith('@@'):
        first = _unwrap_single(ctx.lookup_variable_node('@' + name[2:]))
        indirect = value_to_css(first).strip()
        if len(indirect) >= 2 and indirect[0] == indirect[-1] and indirect[0] in ('"', "'"):
            indirect = indirect[1:-1]
        return _unwrap_single(ctx.lookup_variable_node('@' + indirect))
    return _unwrap_single(ctx.lookup_variable_node(name))
