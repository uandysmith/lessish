"""`:extend(target)` resolution pass.

Top-level entry point is `apply_extends`. The collect / apply pair runs
to fixed point (capped at `max_iterations`) so multi-hop chains
converge. The compound-match helpers (`_normalize_for_extend`,
`_replace_compound_matches`) live here too since they're the
selector-matching machinery the apply pass uses.
"""

from __future__ import annotations

from ..ast_nodes import AtRule, Extend, Node, Ruleset
from .atrules import _MEDIA_LIKE_AT_RULES
from .selectors import _combine_with_ancestors, selector_to_str


def apply_extends(root: Ruleset, *, max_iterations: int = 10) -> None:
    """Extend pass: collect every `:extend(target)` in the tree, then for
    each rule path that matches a target, append the extending path
    list. Chain iterations re-run the same logic against the now-
    extended paths so multi-hop chains converge.

    Runs after `join_selectors` so each Ruleset has `paths` populated.

    Per-rule applied-extends bookkeeping breaks pathological cycles:
    once we've fired (rule, extend) once, we don't refire it on the
    same rule. Stops `.a:extend(.b)` where `extender` contains `b` from
    expanding ad infinitum.

    `max_iterations` caps chain depth as a hard backstop — anything
    legitimate converges in 3–4 rounds.
    """
    extends = _collect_extends(root)
    if not extends:
        return
    applied: dict[int, set[int]] = {}  # id(ruleset) -> set of id(Extend) already applied
    for _ in range(max_iterations):
        any_added = _apply_extends_once(root, extends, applied)
        if not any_added:
            break


def _collect_extends(root: Ruleset) -> list[Extend]:
    """Walk the tree top-down, populating each Selector-attached
    Extend's `extender_paths` (the resolved selector paths the extend
    is "from") and returning the flat list of all such extends.

    Each Extend is also tagged with `_media_scope` — the `id()` of the
    closest enclosing AtRule that introduces a media-like scope
    (`@media`, `@supports`, `@container`), or `None` for root-level
    extends. The apply pass uses this to scope `:extend(...)` calls:
    less.js only fires an `@media`-internal extend on rules inside the
    same @media block (and never on rules in a nested child @media).
    """
    out: list[Extend] = []

    def visit(node: Node, parent_paths: list[str], media_scope: int | None, in_reference: bool) -> None:
        if isinstance(node, Ruleset):
            ref_here = in_reference or node.reference
            if node.root:
                for r in node.rules:
                    visit(r, [''], media_scope, ref_here)
                return
            # Build per-selector resolved paths (parallel to what
            # join_selectors does, but kept separate so we can attribute
            # extends to the *specific* selectors that carry them).
            per_selector_paths: list[list[str]] = []
            for sel in node.selectors:
                sel_str = selector_to_str(sel)
                per_selector_paths.append(_combine_with_ancestors(sel_str, parent_paths))
            # Collect extends. Extends declared inside an `@import
            # (reference)` subtree are reference-only: less.js silently
            # drops them so they don't bleed into the non-reference
            # output. Skip collecting them entirely.
            if not ref_here:
                for sel, paths in zip(node.selectors, per_selector_paths, strict=True):
                    # An `@{var}`-interpolated extender doesn't actually
                    # apply in less.js — the extend visitor walks the
                    # post-eval AST but the extender's element list is
                    # captured pre-interpolation, so the `@{var}` text
                    # never matches a real selector. Skip extends carried
                    # on such selectors to mirror that quirk.
                    if sel._had_interpolation:
                        continue
                    if sel.extend_list:
                        for ext in sel.extend_list:
                            ext.extender_paths = list(paths)
                            ext._media_scope = media_scope
                            out.append(ext)
            # Combined paths feed into nested rules.
            combined: list[str] = []
            for paths in per_selector_paths:
                combined.extend(paths)
            for r in node.rules:
                visit(r, combined, media_scope, ref_here)
        elif isinstance(node, AtRule) and node.body is not None:
            child_scope = id(node) if node.name in _MEDIA_LIKE_AT_RULES else media_scope
            for r in node.body:
                visit(r, parent_paths, child_scope, in_reference)

    visit(root, [''], None, False)
    return out


def _apply_extends_once(root: Ruleset, extends: list[Extend], applied: dict[int, set[int]]) -> bool:
    """One iteration: for every rule's paths, attempt every extend not
    already applied to that rule. Returns True iff at least one new path
    was added.

    `applied` maps `id(ruleset) -> set[id(Extend)]`, breaking cycles when
    an extender contains its own target (so re-applying would just keep
    chasing its own tail).
    """
    added = False

    # Pre-strip target/extender_paths and pre-fetch the media scope once
    # per Extend. The inner visit() loop fires once per rule per extend,
    # so doing these strips inline costs ~30% on the extend pass on
    # Bootstrap. `target_norm_full` is the bracket-quote-normalised
    # form used for the fallback equality check; precomputed here so
    # we don't re-normalise the same target string for every visited
    # rule.
    pre_extends: list[tuple[Extend, str, str, list[str], str, int | None]] = []
    for ext in extends:
        target_norm = ext.target.strip()
        if not target_norm:
            continue
        ep_norm = [ep.strip() for ep in ext.extender_paths if ep.strip()]
        if not ep_norm:
            continue
        pre_extends.append(
            (ext, target_norm, _normalize_for_extend(target_norm), ep_norm, ext.option, ext._media_scope)
        )

    def visit(node: Node, media_scope: int | None) -> None:
        nonlocal added
        if isinstance(node, Ruleset):
            if node.paths:
                rule_id = id(node)
                applied_for_rule = applied.setdefault(rule_id, set())
                # Snapshot the original paths so a freshly-added path
                # in this same iteration isn't immediately re-extended.
                original = [p.strip() for p in node.paths]
                additions: list[str] = []
                skip_dups_against_paths = not node.reference
                node_paths_set = set(node.paths)
                # `normalized_paths` is computed at most once per rule
                # visit (only when some extend's substring probe misses
                # and a normalised equality check is the only remaining
                # chance). Hoisted out of the per-extend loop so a rule
                # with N candidate extends does the work once, not N
                # times. Most rules never need it because `target_norm
                # in path` matches.
                normalized_paths: list[str] | None = None
                for ext, target_norm, target_norm_n, ep_norm, option, ext_scope in pre_extends:
                    if id(ext) in applied_for_rule:
                        continue
                    # Scope check: an `@media`-internal `:extend(...)`
                    # may only fire on rules inside the *same* @media
                    # block — never propagating up to root or down into a
                    # nested @media. Root-level extends apply everywhere.
                    if ext_scope is not None and ext_scope != media_scope:
                        continue
                    # Cheap necessary condition: target must appear as a
                    # substring in at least one of this rule's paths.
                    # Skips the bulk of extend × rule probe pairs on
                    # large stylesheets without changing semantics —
                    # both default and `all` modes require the target
                    # substring to be present.
                    has_target = False
                    for path in original:
                        if target_norm in path:
                            has_target = True
                            break
                    if not has_target and option != 'all':
                        # Default mode also accepts a normalised equality
                        # — the `target_norm in path` check fails on
                        # `[attr=x]` vs `[attr='x']`. Fall through to
                        # the full probe rather than skipping.
                        if normalized_paths is None:
                            normalized_paths = [_normalize_for_extend(p) for p in original]
                        if target_norm_n not in normalized_paths:
                            continue
                    elif not has_target:
                        continue
                    matched_anything = False
                    # Per-extend dedup so a single extend doesn't add the
                    # same path twice (e.g. when an extender already has
                    # the produced selector among its sources).
                    ext_additions: list[str] = []
                    for path in original:
                        for ep in ep_norm:
                            for produced in _apply_one_extend_norm(path, target_norm, ep, option):
                                if skip_dups_against_paths and produced in node_paths_set:
                                    continue
                                if produced in ext_additions:
                                    continue
                                ext_additions.append(produced)
                                matched_anything = True
                                added = True
                    if matched_anything:
                        applied_for_rule.add(id(ext))
                        additions.extend(ext_additions)
                if additions:
                    node.paths = node.paths + additions
                    # `_extend_added_paths` is a list via dataclass
                    # default_factory — extend in place.
                    node._extend_added_paths.extend(additions)
            for r in node.rules:
                visit(r, media_scope)
        elif isinstance(node, AtRule) and node.body is not None:
            child_scope = id(node) if node.name in _MEDIA_LIKE_AT_RULES else media_scope
            for r in node.body:
                visit(r, child_scope)

    visit(root, None)
    return added


def _apply_one_extend(path: str, target: str, extender: str, option: str) -> list[str]:
    """Return new path(s) produced by applying extend `target → extender`
    to `path`. Empty list when nothing matches.

    `option='all'` finds the target as a compound-element sub-sequence
    and replaces it. Default mode requires a full-selector match.
    """
    return _apply_one_extend_norm(path.strip(), target.strip(), extender.strip(), option)


def _apply_one_extend_norm(path: str, target: str, extender: str, option: str) -> list[str]:
    """Internal: same as `_apply_one_extend` but assumes inputs are
    already stripped. Hot path: called from the apply-extends visit
    loop, which strips once per (rule, extend) pair instead of once
    per inner probe.
    """
    if not target or not extender:
        return []
    if option == 'all':
        return _replace_compound_matches(path, target, extender)
    # Default mode: target must match the ENTIRE selector path (no tail-
    # match). `.ff:extend(.dd)` does not extend `.aa .dd` because the
    # rule's path is `.aa .dd`, not `.dd`. Use `:extend(.dd all)` for
    # anywhere-as-compound matching.
    if _normalize_for_extend(path) == _normalize_for_extend(target):
        return [extender]
    return []


def _normalize_for_extend(s: str) -> str:
    """Normalize quote variants inside `[attr=value]` so the three
    spellings `[title=x]`, `[title='x']`, `[title="x"]` compare equal
    for extend-match purposes (matches less.js's extend visitor).
    """
    if '[' not in s:
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch != '[':
            out.append(ch)
            i += 1
            continue
        end = s.find(']', i + 1)
        if end == -1:
            out.append(s[i:])
            break
        body = s[i + 1 : end]
        eq = body.find('=')
        if eq != -1:
            attr = body[: eq + 1]
            val = body[eq + 1 :].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            out.append('[')
            out.append(attr)
            out.append(val)
            out.append(']')
        else:
            out.append(s[i : end + 1])
        i = end + 1
    return ''.join(out)


def _replace_compound_matches(path: str, target: str, extender: str) -> list[str]:
    """Replace every compound-boundary occurrence of `target` in `path`
    with `extender`, producing a single new path. Returns `[new_path]`
    when at least one occurrence matched, `[]` otherwise.

    "Compound boundary" means: the character before the match is start-
    of-string, whitespace, or a combinator (`>`, `+`, `~`). When the
    target starts with a compound-atom marker (`.`, `#`, `:`, `[`), an
    alphanumeric/`)` predecessor also qualifies (so `.x` inside `div.x`
    matches). The character after is end-of-string, whitespace, a
    combinator, or a compound continuation start (`.`, `#`, `:`, `[`).
    Matches that would land in the middle of an identifier (e.g.
    `.error` matching inside `.errorless`) are rejected.

    less.js applies `:extend(.x all)` by replacing ALL matched positions
    simultaneously, yielding one extended path per rule even when the
    target appears multiple times (e.g. `.bb .bb` → `.ff .ff`, not
    separate `.ff .bb` / `.bb .ff` paths).
    """
    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = path.find(target, start)
        if idx == -1:
            break
        end = idx + len(target)
        if idx == 0:
            pre_ok = True
        elif path[idx - 1] in ' >+~,':
            pre_ok = True
        elif target[:1] in '.#:[' and (path[idx - 1].isalnum() or path[idx - 1] in ')]'):
            pre_ok = True
        else:
            pre_ok = False
        post_ok = end == len(path) or path[end] in ' >+~,.#:[' or path[end].isspace()
        if post_ok and end < len(path):
            after = path[end]
            target_last = target[-1]
            if after.isalnum() and target_last.isalnum():
                post_ok = False
        if pre_ok and post_ok:
            matches.append((idx, end))
            start = end
        else:
            start = idx + 1
    if not matches:
        return []
    out: list[str] = []
    cursor = 0
    for s, e in matches:
        out.append(path[cursor:s])
        out.append(extender)
        cursor = e
    out.append(path[cursor:])
    return [''.join(out)]
