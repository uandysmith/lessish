"""Tree-shape passes: dedup, merge, bubble.

These run after `apply_extends` and before `emit_css`:
  * `dedup_declarations` drops within-block duplicate declarations.
  * `merge_same_path_nested` folds a child whose paths equal its
    parent's back into the parent (`& { … }` collapse).
  * `dedup_charset` keeps only the first root-level `@charset`.
  * `bubble_atrules` lifts `@media` / `@supports` / `@container` /
    `@document` out of surrounding rulesets and merges same-type
    siblings into one block with combined prelude.
  * `merge_rules` consolidates `+:` / `+_:`-tagged Declarations into
    a single comma/space-joined value.

The helpers `_paths_match`, `_is_amp_only_selectors`,
`_wrap_loose_with_parent`, `_merge_media_preludes` are exposed for
the visitors package re-export contract.
"""

from __future__ import annotations

from ..ast_nodes import Anonymous, AtRule, Declaration, Node, Ruleset
from .atrules import _BUBBLE_AT_RULES, _MERGE_AT_RULES
from .emitter import _emit_decl_value, _important_suffix


def dedup_declarations(root: Ruleset) -> None:
    """Drop earlier-occurrence duplicate declarations within every
    Ruleset / at-rule body. Mirrors less.js's `_removeDuplicateRules`
    in to-css-visitor: for each declaration name, keep only the LAST
    occurrence with each distinct value (so
    `color: red; color: blue; color: red` collapses to
    `color: blue; color: red`).
    """

    def dedup_rules(rules: list[Node]) -> list[Node]:
        # Walk back-to-front; keep a `(name -> set of seen values)`
        # cache and drop a declaration whose `(name, value_text)` pair
        # was already seen later in the list.
        seen: dict[str, set[str]] = {}
        kept_reversed: list[Node] = []
        for r in reversed(rules):
            if isinstance(r, Declaration) and not r.variable:
                value_text = _emit_decl_value(r)
                if r.important:
                    value_text += _important_suffix(r, False)
                cache = seen.setdefault(r.name, set())
                if value_text in cache:
                    continue
                cache.add(value_text)
            kept_reversed.append(r)
        return list(reversed(kept_reversed))

    def visit(node: Node) -> None:
        if isinstance(node, Ruleset):
            node.rules = dedup_rules(node.rules)
            for r in node.rules:
                visit(r)
        elif isinstance(node, AtRule) and node.body is not None:
            node.body = dedup_rules(node.body)
            for r in node.body:
                visit(r)

    visit(root)


def merge_same_path_nested(root: Ruleset) -> None:
    """Merge a nested Ruleset whose joined paths exactly match its
    immediate parent's paths back INTO the parent's body. Mirrors
    less.js's behaviour for `& { … }` whose only selector resolves to
    the parent path: `.A { color: green; & { color: red; } }` emits
    as one block `.A { color: green; color: red; }`, not two.

    Runs after `apply_extends` so any extend-added paths participate
    in the equality check too — otherwise a `:extend(.z all)`-cloned
    `.z` with an inner `& {}` would emit duplicate blocks under
    `@import (reference)`.
    """

    def visit(node: Node) -> None:
        if isinstance(node, Ruleset):
            new_rules: list[Node] = []
            for r in node.rules:
                if isinstance(r, Ruleset) and not r.root and _paths_match(node, r) and _is_amp_only_selectors(r):
                    # Splice the inner ruleset's body into the parent.
                    # Recurse into it first so nested-nested merges
                    # bubble up correctly.
                    visit(r)
                    new_rules.extend(r.rules)
                    continue
                visit(r)
                new_rules.append(r)
            node.rules = new_rules
        elif isinstance(node, AtRule) and node.body is not None:
            for r in node.body:
                visit(r)

    visit(root)


def _paths_match(parent: Ruleset, child: Ruleset) -> bool:
    """True iff `child.paths` is the same multi-selector set as
    `parent.paths`. Order-sensitive — comparing sorted tuples would
    let `.A, .B` collapse with `.B, .A` against the parent, which
    less.js doesn't do.
    """
    return bool(parent.paths) and bool(child.paths) and list(parent.paths) == list(child.paths)


def _is_amp_only_selectors(rs: Ruleset) -> bool:
    """True iff every selector of `rs` is a single-element `&` —
    the shape that semantically denotes "same as parent". Restricts
    the same-path merge to the exact `& { … }` pattern less.js
    targets, so non-`&` rulesets that happen to resolve identically
    don't get accidentally fused.
    """
    if not rs.selectors:
        return False
    for sel in rs.selectors:
        if len(sel.elements) != 1:
            return False
        if sel.elements[0].value != '&':
            return False
    return True


def dedup_charset(root: Ruleset) -> None:
    """Drop duplicate `@charset` directives at the root level.

    CSS requires `@charset` to appear at most once, at the top of the
    file. After `@import` resolution we can end up with two: one from
    the main file and another spliced in from an import. less.js keeps
    only the first; we follow.
    """
    if not root.root:
        return
    seen = False
    out: list[Node] = []
    for r in root.rules:
        if isinstance(r, AtRule) and r.name == '@charset':
            if seen:
                continue
            seen = True
        out.append(r)
    root.rules = out


def bubble_atrules(root: Ruleset) -> None:
    """Bubble nested same-type at-rules out so they emit as siblings.

    less.js merges `@media (tv) { @media (hires) { … } }` into
    `@media (tv) and (hires) { … }` at the outer level, and lifts
    `@media` blocks out of surrounding Rulesets (wrapping the body in
    the parent's selector context). The same applies to `@supports` and
    `@container`. We perform the merge as a tree rewrite: process
    bodies bottom-up, then extract bubbleable at-rules at each level.

    Returns nothing; mutates `root.rules` in place.
    """

    def process(
        rules: list[Node],
        parent_paths: list[str] | None = None,
        atrule_chain: tuple[str, ...] = (),
        parent_reference: bool = False,
    ) -> list[Node]:
        out: list[Node] = []
        for r in rules:
            if isinstance(r, AtRule) and r.body is not None and r.name in _BUBBLE_AT_RULES:
                # Recurse so any deeper nesting is already flattened.
                # Propagate `parent_paths` so wraps fire at the deepest
                # level — `.x { @media { @supports { p:v; } } }` should
                # produce `@media { @supports { .x { p:v; } } }`.
                inner = process(
                    r.body,
                    parent_paths,
                    atrule_chain + (r.name,),
                    parent_reference=parent_reference,
                )
                own: list[Node] = []
                lifted: list[AtRule] = []
                # Merge same-name nested at-rules ONLY when the entire
                # ancestor chain is the same type — less.js's
                # `evalNested` bails out if any other bubbleable at-rule
                # type sits above. So `@container { @media { @media …
                # } }` keeps the inner @media nested; `@media { @media
                # … }` at root merges to `@media a and b`.
                merge_allowed = all(name == r.name for name in atrule_chain) and r.name in _MERGE_AT_RULES
                for c in inner:
                    if merge_allowed and isinstance(c, AtRule) and c.body is not None and c.name == r.name:
                        merged = _merge_media_preludes(r.prelude, c.prelude, r.name)
                        lifted.append(
                            AtRule(
                                index=c.index,
                                name=r.name,
                                prelude=merged,
                                body=c.body,
                                trailing_comments=c.trailing_comments,
                            )
                        )
                    else:
                        own.append(c)
                # If this @media lives inside a Ruleset, wrap its loose
                # declarations + inline at-rules in the parent's
                # selector so the emit looks like `@media (…) { .x {…} }`
                # rather than `@media (…) { …; .x …; }`. Carry over
                # the parent's `reference` flag so reference-import
                # bubbles don't accidentally turn into emittable CSS.
                if parent_paths is not None:
                    own = _wrap_loose_with_parent(
                        own,
                        parent_paths,
                        r.index,
                        parent_reference=parent_reference,
                    )
                if own:
                    new_at = AtRule(
                        index=r.index,
                        name=r.name,
                        prelude=r.prelude,
                        body=own,
                        trailing_comments=r.trailing_comments,
                    )
                    if r._reference:
                        new_at._reference = True
                    out.append(new_at)
                # Propagate `_reference` to every merged-out at-rule too.
                for li in lifted:
                    if r._reference:
                        li._reference = True
                out.extend(lifted)
            elif isinstance(r, AtRule) and r.body is not None:
                # Non-mergeable at-rule (e.g. `@keyframes`): recurse so
                # nested @media inside it still gets processed.
                new_body = process(
                    r.body,
                    None,
                    atrule_chain + (r.name,),
                    parent_reference=parent_reference,
                )
                new_at = AtRule(
                    index=r.index,
                    name=r.name,
                    prelude=r.prelude,
                    body=new_body,
                    trailing_comments=r.trailing_comments,
                )
                if r._reference:
                    new_at._reference = True
                out.append(new_at)
            elif isinstance(r, Ruleset):
                # Use the ruleset's RESOLVED paths so `& `-references are
                # already substituted before we splice them into a
                # bubbled at-rule's body.
                effective_paths = r.paths if r.paths else parent_paths
                processed = process(
                    r.rules,
                    effective_paths,
                    atrule_chain,
                    parent_reference=parent_reference or r.reference,
                )
                # Bubble bubbleable at-rules OUT. When at least one such
                # at-rule is present, also splice the parent's nested
                # Rulesets out alongside it so source order between them
                # is preserved at root (less.js model: the parent body
                # only holds loose decls; nested children become sibling
                # blocks at the caller's level, interleaved with the
                # bubbled at-rules in their original order).
                has_bubbleable = any(
                    isinstance(c, AtRule) and c.body is not None and c.name in _BUBBLE_AT_RULES for c in processed
                )
                kept: list[Node] = []
                bubbled: list[Node] = []
                for c in processed:
                    if isinstance(c, AtRule) and c.body is not None and c.name in _BUBBLE_AT_RULES:
                        # Inherit the parent Ruleset's `reference` flag so
                        # `@import (reference) ...` subtrees whose inner
                        # `@media` blocks get bubbled to the top level
                        # are still suppressed at emit. The flag rides
                        # on `__dict__` since `AtRule` has no `reference`
                        # field; `emit_atrule` honours it.
                        if r.reference:
                            c._reference = True
                        bubbled.append(c)
                    elif has_bubbleable and isinstance(c, Ruleset):
                        bubbled.append(c)
                    else:
                        kept.append(c)
                r.rules = kept
                # If everything bubbled out, the Ruleset is now an
                # empty shell — drop it so we don't emit a bare
                # `.x { }` block.
                if r.rules or not bubbled:
                    out.append(r)
                out.extend(bubbled)
            else:
                out.append(r)
        return out

    root.rules = process(root.rules, None)


def _wrap_loose_with_parent(
    body: list[Node],
    parent_paths: list[str],
    index: int,
    *,
    parent_reference: bool = False,
) -> list[Node]:
    """When a bubbleable at-rule is lifted out of a Ruleset, its loose
    declarations (and inline at-rules) need the surrounding selector
    context. Group every run of non-Ruleset/non-bubbleable items into a
    synthetic Ruleset whose `paths` are the parent's resolved paths;
    keep nested Rulesets as-is.

    `parent_reference` propagates the original parent Ruleset's
    `reference` flag so a `@import (reference)` subtree's nested
    `.x { @media { … } }` doesn't accidentally turn its bubbled
    wrapper into emittable CSS when the parent meant "compile-time
    reference only".
    """
    grouped: list[Node] = []
    pending: list[Node] = []

    def flush() -> None:
        if pending:
            wrapper = Ruleset(
                index=index,
                selectors=[],
                rules=list(pending),
                root=False,
                reference=parent_reference,
            )
            wrapper.paths = list(parent_paths)
            grouped.append(wrapper)
            pending.clear()

    for entry in body:
        if isinstance(entry, Ruleset):
            flush()
            grouped.append(entry)
        elif isinstance(entry, AtRule) and entry.body is not None and entry.name in _BUBBLE_AT_RULES:
            flush()
            grouped.append(entry)
        else:
            pending.append(entry)
    flush()
    return grouped


def _merge_media_preludes(outer: str, inner: str, name: str) -> str:
    """Combine outer + inner at-rule preludes with `and` joins. For
    `@media`, comma-separated queries distribute against each other
    (`@media a, b` + `@media c` → `@media a and c, b and c`).
    `@supports`/`@container` use a single `and` join.
    """
    o = outer.strip()
    i = inner.strip()
    if not o:
        return i
    if not i:
        return o
    if name == '@media':
        outer_parts = [p.strip() for p in o.split(',')]
        inner_parts = [p.strip() for p in i.split(',')]
        # less.js cycles inner SLOWLY (outer-fast) so output is:
        # outer[0]∧inner[0], outer[1]∧inner[0], …, outer[0]∧inner[1], …
        combos = []
        for ip in inner_parts:
            for op in outer_parts:
                combos.append(f'{op} and {ip}')
        return ', '.join(combos)
    return f'{o} and {i}'


def merge_rules(root: Ruleset) -> None:
    """Consolidate consecutive `+:` / `+_:` declarations into one.

    For each ruleset:
      * Group declarations by name where `merge` is non-empty.
      * Merge into the first occurrence (in source order); drop the
        rest. The output value is built from the group's values in
        order, with the merge flag of each item deciding how it joins:
        `+` starts a new comma segment, `+_` appends to the last
        segment with a space. Non-merge declarations between merge-
        tagged ones don't participate — they stay as separate output.
      * `!important` from any group member propagates to the merged
        declaration.

    Mirrors less.js's MergeRulesVisitor. Run AFTER eval (declarations
    must carry their final text) and BEFORE emit.
    """

    def merge_one(rules: list[Node]) -> list[Node]:
        groups: dict[str, list[Declaration]] = {}
        order: list[str] = []
        for r in rules:
            if isinstance(r, Declaration) and r.merge:
                key = r.name
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(r)
        if not groups:
            return rules
        # For each group, build merged value text + flag the first to keep.
        merged_value: dict[int, str] = {}  # id(first) -> merged text
        merged_important: dict[int, bool] = {}
        drop_ids: set[int] = set()
        for name in order:
            members = groups[name]
            if len(members) < 2:
                continue
            segments: list[str] = []
            important = False
            for d in members:
                v = d.value.value if isinstance(d.value, Anonymous) else ''
                if d.important:
                    important = True
                if d.merge == '+_' and segments:
                    segments[-1] = segments[-1] + ' ' + v
                else:
                    segments.append(v)
            first = members[0]
            merged_value[id(first)] = ', '.join(segments)
            merged_important[id(first)] = important
            for d in members[1:]:
                drop_ids.add(id(d))
        out: list[Node] = []
        for r in rules:
            if id(r) in drop_ids:
                continue
            if isinstance(r, Declaration) and id(r) in merged_value:
                out.append(
                    Declaration(
                        index=r.index,
                        name=r.name,
                        value=Anonymous(index=r.value.index, value=merged_value[id(r)]),
                        important=merged_important[id(r)],
                        variable=r.variable,
                        merge='',
                    )
                )
            else:
                out.append(r)
        return out

    def visit(node: Node) -> None:
        if isinstance(node, Ruleset):
            node.rules = merge_one(node.rules)
            for r in node.rules:
                visit(r)
        elif isinstance(node, AtRule) and node.body is not None:
            node.body = merge_one(node.body)
            for r in node.body:
                visit(r)

    visit(root)
