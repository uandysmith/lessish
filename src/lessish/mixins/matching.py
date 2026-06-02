"""Mixin matching: find every ``MixinDefinition`` (or zero-arg
``Ruleset``) reachable from a call site whose name and arity satisfy
the call.

The matcher walks the eval frame stack innermost-first, consulting a
per-frame ``_MixinIndex`` (rebuilt on rules-list growth) for O(1) name
lookup. Namespace paths (``#a > .b``) trigger a tree-descent fallback
that walks nested rulesets segment by segment.
"""

from __future__ import annotations

from collections.abc import Callable

from ..ast_nodes import (
    Color,
    Dimension,
    Keyword,
    MixinArg,
    MixinCall,
    MixinDefinition,
    MixinParam,
    Node,
    Quoted,
    Ruleset,
    Selector,
)
from .clones import _shallow_clone_def
from .namespace import _split_namespace_path


def find_mixin_matches(
    call: MixinCall,
    frames: list[Ruleset],
    *,
    resolve_name: Callable[[str], str] | None = None,
) -> list[MixinDefinition]:
    """Walk `frames` innermost-first and return every MixinDefinition
    whose name equals `call.name` and whose arity is compatible.

    Plain Rulesets are also callable as zero-arg mixins (less.js feature:
    `.foo { ... }` defines both a CSS rule AND an inlinable mixin).

    Namespace lookups (`#ns > .mixin`, `#a .b > .c`, `#a.b.c.mixin`)
    split the call name on combinators and walk into the head ruleset's
    rules. Matching is order-preserving: rules are scanned in source
    order and, for each rule, any prefix of the call name that matches
    descends into the rule. less.js invokes all matching definitions
    and concatenates their bodies — the LAST match wins for property-
    name lookups (which walk in reverse).

    When a match is found via namespace descent, the containing
    Ruleset(s) are attached to the returned MixinDefinition's
    `_closure_frames` field so the call-site invoker can push them
    onto the eval frame stack for definition-scope variable capture
    (`.scope { @v: 1; .m(){c: @v}} .a { .scope > .m(); }`).
    """
    matches: list[MixinDefinition] = []
    parts = _split_namespace_path(call.name)
    # Walk frames innermost-first; for each match, copy the
    # MixinDefinition (so per-match annotations like `_closure_frames`
    # and `_match_frame_index` don't bleed across concurrent
    # invocations of the same def) and tag with the frame index where
    # it was found. `_invoke_mixin_call` applies less.js's shadowing
    # rule: keep only the lowest-frame-index survivors after the
    # recursion filter has run.
    for i, frame in enumerate(frames):
        for m in _match_in_rules(
            frame.rules,
            call.name,
            call.args,
            resolve_name=resolve_name,
            frame=frame,
        ):
            m = _shallow_clone_def(m)
            m._match_frame_index = i
            if i > 0:
                # Recompute every time — for a top-level shared MixinDef
                # the relevant closure depends on where the caller's
                # frame stack happens to live this invocation. A
                # splice-time `_splice_closure_frames` (set in
                # `_invoke_mixin_call`) takes priority and is read
                # separately at invoke time; do not clobber it here.
                m._closure_frames = list(frames[i:])
            matches.append(m)
    if matches:
        return matches
    if len(parts) == 1:
        return matches
    # Namespace path: scan rules in source order; for each rule, try
    # all possible prefixes (longer first within a single rule) so the
    # output preserves definition order across rules.
    for i, frame in enumerate(frames):
        for m in _descend_namespace(frame.rules, parts, call.args, closure=[], resolve_name=resolve_name):
            existing = m._closure_frames if m._closure_frames is not None else []
            m = _shallow_clone_def(m)
            m._match_frame_index = i
            # Append frames-outward from the matched frame so closure
            # chain is [descended Ruleset(s) ..., frame_i, frame_i+1, ...].
            if i > 0 or existing:
                m._closure_frames = (existing or []) + (list(frames[i:]) if i > 0 else [])
            matches.append(m)
    return matches


def _descend_namespace(
    rules: list[Node],
    parts: list[str],
    args: list[MixinArg],
    *,
    closure: list[Ruleset],
    resolve_name: Callable[[str], str] | None = None,
) -> list[MixinDefinition]:
    """Walk rules in source order; at each rule try every possible
    prefix-of-`parts` match (longer prefixes preferred within a rule),
    recursing into the rule's body with the remainder.

    `closure` accumulates the parent Rulesets stepped through during
    descent. When a leaf match is recorded, the closure list is
    attached via `_closure_frames` so the invoker can push these
    frames for definition-scope variable capture.
    """
    out: list[MixinDefinition] = []
    if not parts:
        return out
    for rule in rules:
        rule_name: str | None = None
        rule_rules: list[Node] | None = None
        is_ruleset = False
        params = None
        if isinstance(rule, Ruleset):
            rule_name = _ruleset_mixin_name(rule)
            if rule_name is None:
                # `&.x` (compound on parent) is also a valid descent
                # target when we're already inside the parent. Treat it
                # as `.x` for the lookup.
                rule_name = _ruleset_amp_compound_name(rule)
            rule_rules = rule.rules
            is_ruleset = True
        elif isinstance(rule, MixinDefinition):
            rule_name = rule.name
            rule_rules = rule.rules
            params = rule.params
        if rule_name is None:
            continue
        # Resolve `@{name}` interpolation in the rule's selector at
        # match time (D5: `.@{a0} { … }` should be findable as `.\123`
        # when `@a0: \123;`).
        if '@{' in rule_name and resolve_name is not None:
            rule_name = resolve_name(rule_name)
        # The rule may itself span multiple descend-segments
        # (`.do .re .mi { .si { ... } }` — the outer ruleset's name is
        # `.do .re .mi`, a 3-segment chain). For namespace matching,
        # split the rule name the same way the call name is split and
        # require the call's leading parts to equal the full rule chain.
        rule_parts = _split_namespace_path(rule_name)
        rule_chain_len = len(rule_parts)
        if rule_chain_len > 1 and rule_chain_len <= len(parts) and rule_parts == parts[:rule_chain_len]:
            rest = parts[rule_chain_len:]
            assert rule_rules is not None
            next_closure = closure + [rule] if isinstance(rule, Ruleset) else closure
            if rest:
                if is_ruleset or (params is not None and _arity_compatible(params, [])):
                    out.extend(
                        _descend_namespace(
                            rule_rules,
                            rest,
                            args,
                            closure=next_closure,
                            resolve_name=resolve_name,
                        )
                    )
            else:
                if is_ruleset and not args:
                    assert isinstance(rule, Ruleset)
                    candidate = _ruleset_as_mixin(rule)
                    if closure:
                        candidate._closure_frames = list(closure)
                    out.append(candidate)
                elif params is not None and _arity_compatible(params, args):
                    assert isinstance(rule, MixinDefinition)
                    if closure:
                        rule._closure_frames = list(closure)
                    out.append(rule)
            continue
        # Try each prefix length, longest first.
        for split in range(len(parts), 0, -1):
            head = ''.join(parts[:split])
            if head != rule_name:
                continue
            rest = parts[split:]
            if rest:
                # Rulesets and MixinDefinitions that can be invoked with
                # zero positional args (all params have defaults, or
                # there are none) can be "stepped through" to expose
                # their inner namespace.
                if is_ruleset or (params is not None and _arity_compatible(params, [])):
                    assert rule_rules is not None
                    next_closure = closure + [rule] if isinstance(rule, Ruleset) else closure
                    out.extend(
                        _descend_namespace(
                            rule_rules,
                            rest,
                            args,
                            closure=next_closure,
                            resolve_name=resolve_name,
                        )
                    )
            else:
                # Leaf match: invoke this rule with the call's args.
                if is_ruleset and not args:
                    assert isinstance(rule, Ruleset)
                    candidate = _ruleset_as_mixin(rule)
                    if closure:
                        candidate._closure_frames = list(closure)
                    out.append(candidate)
                elif params is not None and _arity_compatible(params, args):
                    assert isinstance(rule, MixinDefinition)
                    if closure:
                        rule._closure_frames = list(closure)
                    out.append(rule)
            break  # don't try shorter prefixes for the same rule
    return out


class _MixinIndex:
    """Per-frame name → rules lookup table for `_match_in_rules`.

    Without this, every `find_mixin_matches` call walks the entire
    `frame.rules` list (often 1000+ rules on real-world stylesheets),
    extracting each Ruleset's mixin name as it goes. With an index,
    lookup is a single dict access. Cached on `frame.__dict__` keyed
    by the rules list's length — the pre-pass in `eval_ruleset` only
    appends to `rules`, so a length-change is the sole invalidator.

    Names that contain `@{...}` interpolation can't be indexed by
    final text (it depends on call-site scope); those rules go in a
    separate `interpolated` list and the caller does a linear scan +
    `resolve_name` on them. Real-world stylesheets rarely use this so
    the slow path is small.
    """

    __slots__ = ('by_name', 'interpolated', 'len_when_built')

    def __init__(self) -> None:
        self.by_name: dict[str, list[Node]] = {}
        self.interpolated: list[Node] = []
        self.len_when_built: int = 0


def _mixin_index_for(frame: Ruleset) -> _MixinIndex:
    """Lazy-build a `_MixinIndex` for `frame`; extend incrementally
    when the rules list has grown since the last build.

    The pre-pass in `eval_ruleset` only ever appends to a frame's
    rules (`new_rules[pos+1:pos+1] = invoked` is an insert, but the
    eval pre-pass uses this on its own freshly-built list; a *frame*'s
    rules grow via the live-frame seeding + splice route which is
    append-shaped from the index's perspective). When growth is
    append-only we just scan the new tail and merge it into the
    existing buckets — on a block with N mixin calls that turns N
    full O(N) rebuilds into one O(N) build plus N O(1) extensions.

    If `len(rules) < cache.len_when_built` (defensive: the contract
    above ever breaks, e.g. a future caller truncates rules), we
    rebuild from scratch so the index can't go stale silently.
    """
    rules = frame.rules
    cache = frame._mixin_index_cache
    n = len(rules)
    if isinstance(cache, _MixinIndex):
        if cache.len_when_built == n:
            return cache
        if cache.len_when_built < n:
            _extend_mixin_index(cache, rules, cache.len_when_built, n)
            return cache
    idx = _MixinIndex()
    _extend_mixin_index(idx, rules, 0, n)
    frame._mixin_index_cache = idx
    return idx


def _extend_mixin_index(idx: _MixinIndex, rules: list[Node], start: int, end: int) -> None:
    """Append `rules[start:end]` into `idx`'s buckets and stamp
    `len_when_built = end`. Pulled out of `_mixin_index_for` so both
    the cold path (fresh index, `start=0`) and the warm append path
    share the same classification logic.
    """
    for i in range(start, end):
        rule = rules[i]
        if isinstance(rule, MixinDefinition):
            if '@{' in rule.name:
                idx.interpolated.append(rule)
            else:
                idx.by_name.setdefault(rule.name, []).append(rule)
        elif isinstance(rule, Ruleset):
            names = _ruleset_mixin_names(rule)
            for n in names:
                if '@{' in n:
                    idx.interpolated.append(rule)
                    break
            for n in names:
                if '@{' not in n:
                    idx.by_name.setdefault(n, []).append(rule)
    idx.len_when_built = end


def _match_in_rules(
    rules: list[Node],
    name: str,
    args: list[MixinArg],
    *,
    resolve_name: Callable[[str], str] | None = None,
    frame: Ruleset,
) -> list[MixinDefinition]:
    """Indexed lookup against a frame: scan the by-name bucket for
    exact matches, then walk the (usually empty) interpolated-name
    bucket. `rules` is kept in the signature for symmetry with the
    caller's framing but is unused; the index built from `frame`
    drives the lookup.
    """
    del rules  # superseded by the index built from `frame`
    out: list[MixinDefinition] = []
    idx = _mixin_index_for(frame)
    for rule in idx.by_name.get(name, ()):
        if isinstance(rule, MixinDefinition):
            if _arity_compatible(rule.params, args):
                out.append(rule)
        elif isinstance(rule, Ruleset) and not args:
            out.append(_ruleset_as_mixin(rule))
    if idx.interpolated and resolve_name is not None:
        for rule in idx.interpolated:
            if isinstance(rule, MixinDefinition):
                rn = resolve_name(rule.name)
                if rn == name and _arity_compatible(rule.params, args):
                    out.append(rule)
            elif isinstance(rule, Ruleset) and not args:
                for rs_name in _ruleset_mixin_names(rule):
                    if '@{' in rs_name:
                        rs_name = resolve_name(rs_name)
                    if rs_name == name:
                        out.append(_ruleset_as_mixin(rule))
                        break
    return out


def name_exists(
    call: MixinCall,
    frames: list[Ruleset],
    *,
    resolve_name: Callable[[str], str] | None = None,
) -> bool:
    """Is there *any* MixinDefinition or Ruleset whose name matches
    `call.name`, irrespective of arity? Used to distinguish "name
    undefined" (less.js: `NameError: .x is undefined`) from "arity
    mismatch" (less.js: `RuntimeError: No matching definition was
    found for ...`).
    """
    parts = _split_namespace_path(call.name)
    leaf = parts[-1] if parts else call.name
    for frame in frames:
        # Use the same name index as `_match_in_rules`. The index
        # discards arity, which is exactly what `name_exists` wants —
        # any matching definition counts.
        idx = _mixin_index_for(frame)
        if leaf in idx.by_name:
            return True
        if idx.interpolated and resolve_name is not None:
            for rule in idx.interpolated:
                if isinstance(rule, MixinDefinition):
                    if resolve_name(rule.name) == leaf:
                        return True
                elif isinstance(rule, Ruleset):
                    for rsname in _ruleset_mixin_names(rule):
                        if '@{' in rsname:
                            rsname = resolve_name(rsname)
                        if rsname == leaf:
                            return True
    return False


def _ruleset_mixin_names(rs: Ruleset) -> list[str]:
    """Return every selector of `rs` that can be invoked as a mixin
    (one entry per `.x` / `#y` selector). A multi-selector ruleset
    like `.a, .b { … }` exposes itself under both `.a` and `.b`
    (D4 / less.js convention).
    """
    out: list[str] = []
    for sel in rs.selectors:
        n = _selector_mixin_name(sel)
        if n is not None:
            out.append(n)
    return out


def _ruleset_mixin_name(rs: Ruleset) -> str | None:
    """If `rs` looks like a class/id ruleset that can be invoked as a
    mixin, return its mixin-style name. Otherwise return None.
    """
    if len(rs.selectors) != 1:
        return None
    return _selector_mixin_name(rs.selectors[0])


def _selector_mixin_name(sel: Selector) -> str | None:
    # Cache on the Selector instance — selectors are immutable in
    # practice for this purpose (their .elements list doesn't get
    # mutated post-parse), and `find_mixin_matches` reaches the same
    # selectors thousands of times during a single compile.
    # Two fields (`_mixin_name_cached`, `_mixin_name`) because
    # "computed and is None" is a legitimate cache hit distinct from
    # "not yet computed".
    if sel._mixin_name_cached:
        return sel._mixin_name
    elements = sel.elements
    if not elements:
        sel._mixin_name = None
        sel._mixin_name_cached = True
        return None
    # Accept `.x`, `#x`, or `@{name}` — the interpolation form resolves
    # to a `.`/`#`-prefixed token at lookup time (handled by
    # `_match_in_rules`'s `resolve_name`).
    head = elements[0].value
    if not (head.startswith('.') or head.startswith('#') or head.startswith('@{')):
        sel._mixin_name = None
        sel._mixin_name_cached = True
        return None
    parts: list[str] = []
    for i, e in enumerate(elements):
        if i == 0:
            parts.append(e.value)
        elif e.combinator == ' ':
            parts.append(' ' + e.value)
        elif e.combinator == '':
            parts.append(e.value)
        else:
            parts.append(f' {e.combinator} {e.value}')
    result = ''.join(parts)
    sel._mixin_name = result
    sel._mixin_name_cached = True
    return result


def _ruleset_amp_compound_name(rs: Ruleset) -> str | None:
    """For a nested ruleset whose selector starts with `&`, return the
    name with the leading `&` stripped — preserving any internal
    descendant combinators.

    Examples:
      `&.x`           → `.x`
      `&.x.y`         → `.x.y`
      `&.x .y`        → `.x .y`        (descend through both segments)
      `&.x-foo .y#z`  → `.x-foo .y#z`  (mixin-call can match dot-joined `.x-foo.y.z`)

    Used during namespace descent so calls like `.foo.bar.baz()` can
    resolve into `.foo { &.bar .baz { ... } }` — the multi-segment
    descendant chain after `&` becomes additional path segments via
    `_split_namespace_path`.

    Returns None when the first post-`&` element isn't tight-compound
    (the `&` itself must directly attach to a class/id), or when any
    non-descendant combinator appears (`>`, `+`, etc. — those don't
    flatten into a mixin name path).
    """
    if len(rs.selectors) != 1:
        return None
    elements = rs.selectors[0].elements
    if not elements:
        return None
    if elements[0].value != '&':
        return None
    parts: list[str] = []
    for i, e in enumerate(elements[1:]):
        if i == 0:
            # First post-`&` element: combinator '' means tight-compound
            # (`&.foo`), ' ' means descendant (`& .foo`). For mixin name
            # path purposes both flatten the same — `_split_namespace_path`
            # treats the result as a list of segments either way.
            if e.combinator not in ('', ' '):
                return None
            parts.append(e.value)
        elif e.combinator == ' ':
            parts.append(' ' + e.value)
        elif e.combinator == '':
            parts.append(e.value)
        else:
            return None
    if not parts:
        return None
    # Accept `.x`, `#x`, or `@{name}` as the first segment. The
    # interpolation form resolves to a `.`/`#`-prefixed token at
    # match time (handled by `_descend_namespace`'s `resolve_name`
    # branch); rejecting it here would skip otherwise-valid
    # interpolated mixin targets like `& @{name}.bbb`.
    if not (parts[0].startswith('.') or parts[0].startswith('#') or parts[0].startswith('@{')):
        return None
    return ''.join(parts)


def _ruleset_as_mixin(rs: Ruleset) -> MixinDefinition:
    """Wrap a plain Ruleset in a MixinDefinition view for invocation.
    The original Ruleset is unchanged (it still emits its own CSS).

    A CSS-guarded ruleset (`.x when (cond) { ... }`) carries its guard
    onto the mixin wrapper so a call site filters out the rule when the
    guard would have made it disappear at top level. Less.js's behavior
    diverges in one corner — the guard is meant to evaluate against the
    *definition* scope, not the call site — but for the common case
    (no local shadowing of the referenced variable) the two coincide.
    """
    wrapper = MixinDefinition(
        index=rs.index,
        name=_ruleset_mixin_name(rs) or '',
        params=[],
        rules=rs.rules,
        guard=rs.condition,
        is_ruleset_wrapper=True,
    )
    # Stash the source Ruleset id so the invoker can break recursion
    # when a body calls itself: `.x { .x(); }` should match only the
    # MixinDefinition `.x()` (if any), never the same `.x` ruleset.
    wrapper._source_ruleset_id = id(rs)
    # When the source ruleset came from an `@import (reference)`
    # subtree, the splice-time `_tag_splice_closures` pass clears the
    # reference flag on the spliced output so a non-reference caller
    # sees emittable CSS. A non-reference Ruleset (whose body may
    # *itself* contain reference content via in-body `@import
    # (reference)`) doesn't get this flag — its inner reference rules
    # must stay invisible.
    if rs.reference:
        wrapper._was_referenced = True
    return wrapper


def _arity_compatible(params: list[MixinParam], args: list[MixinArg]) -> bool:
    """Check that `args` can satisfy `params` accounting for patterns,
    named bindings, defaults, and the variadic tail (if any).

    Patterns are anchored to positional positions: arg[i] must equal
    param[i].pattern when that param is a pattern. Named args fill the
    `@name` params they reference. Defaults cover any unfilled `@name`
    param. The variadic tail (if present) absorbs all overflow.
    """
    named_args = {a.name: a for a in args if a.name is not None}
    pos_args = [a for a in args if a.name is None]
    pos_i = 0
    has_variadic = any(p.variadic for p in params)

    for p in params:
        if p.variadic:
            return True  # variadic absorbs anything left; remaining named
            # args (if any) still need to map to a param — fall through:
        if p.pattern is not None:
            if pos_i >= len(pos_args):
                return False
            if not _values_equal(p.pattern, pos_args[pos_i].value):
                return False
            pos_i += 1
            continue
        # Variable-bindable param
        if p.name in named_args:
            continue
        if pos_i < len(pos_args):
            pos_i += 1
            continue
        if p.default is not None:
            continue
        return False  # required param without arg

    # Without variadic, we can't have leftover positional args.
    if not has_variadic and pos_i < len(pos_args):
        return False
    # Every named arg must map to some param name in the definition.
    param_names = {p.name for p in params if p.name}
    for nname in named_args:
        if nname not in param_names:
            return False
    return True


def _values_equal(a: Node, b: Node) -> bool:
    """Loose semantic equality for mixin pattern-matching. Compares
    after unwrapping single-element Value/Expression layers. Only the
    pattern-relevant node kinds are inspected; everything else returns
    False.
    """
    from ..evaluator import _unwrap_single

    a = _unwrap_single(a)
    b = _unwrap_single(b)
    if isinstance(a, Keyword) and isinstance(b, Keyword):
        return a.value == b.value
    if isinstance(a, Dimension) and isinstance(b, Dimension):
        return a.value == b.value and a.unit == b.unit
    if isinstance(a, Color) and isinstance(b, Color):
        return a.value == b.value
    if isinstance(a, Quoted) and isinstance(b, Quoted):
        return a.value == b.value
    return False
