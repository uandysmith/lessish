"""Selector serialization + ancestor combination + the
`join_selectors` pass.

These four symbols form a tight cluster: `selector_to_str`
produces the textual form of a single Selector;
`_combine_with_ancestors` is the Cartesian product against parent
paths (`&` substitution); `join_selectors` walks the tree top-down
populating each non-root Ruleset's `paths` field with the resolved
selector list; `_resets_selector_context` answers "does this at-rule
break the surrounding selector chain?" (`@keyframes`, `@font-face`,
…).
"""

from __future__ import annotations

from ..ast_nodes import AtRule, Node, Ruleset, Selector


def selector_to_str(sel: Selector) -> str:
    """Join a Selector's elements back into a single string with the
    same shape Less.js would write to CSS.

    For the first element, an explicit combinator (rare — usually means
    a leading `>` etc. that the user wrote) is kept; an implicit `' '`
    is dropped (leading whitespace doesn't belong in CSS).
    """
    parts: list[str] = []
    for i, elem in enumerate(sel.elements):
        if i == 0:
            if elem.combinator and elem.combinator != ' ':
                parts.append(f'{elem.combinator} ')
            parts.append(elem.value)
        else:
            if elem.combinator == ' ':
                parts.append(f' {elem.value}')
            elif elem.combinator == '':
                parts.append(elem.value)
            elif elem.combinator == '|':
                # CSS namespace separator (`ns|element`) — never spaced.
                parts.append(f'|{elem.value}')
            else:
                parts.append(f' {elem.combinator} {elem.value}')
    return ''.join(parts)


def _combine_with_ancestors(sel_str: str, ancestors: list[str]) -> list[str]:
    """Combine `sel_str` with `ancestors` (the parent ruleset's paths).

    When `&` appears in `sel_str`, each occurrence is independently
    substituted with any of the ancestor paths — giving the full
    Cartesian product. So with parent paths `[.foo, .bar]` and child
    `& + &`, the output is the 4 combinations
    `.foo + .foo`, `.foo + .bar`, `.bar + .foo`, `.bar + .bar`
    (less.js semantics).

    When there is no `&`, descendant-combine (`ancestor child`) — one
    output per ancestor path. An empty ancestor (top-level) leaves
    the child unchanged.
    """
    if '&' not in sel_str:
        # Strip leading whitespace from the child text before
        # descendant-combine: when interpolation injects ` + .e` (a
        # leading-combinator string), the `f'{anc} {sel_str}'` join
        # would otherwise emit `.a  + .e` (double space). less.js
        # collapses the whitespace at this boundary.
        return [sel_str if not anc else f'{anc} {sel_str.lstrip()}' for anc in ancestors]
    # Split on `&`, then per gap interleave with each ancestor choice
    # in turn. `re.split` keeps things simple here.
    parts = sel_str.split('&')
    # For each gap (between parts[i] and parts[i+1]) we pick one
    # ancestor. There are len(parts)-1 gaps and len(ancestors) choices
    # per gap. Build all combinations.
    n_gaps = len(parts) - 1
    if n_gaps == 0:
        return [sel_str]
    out: list[str] = []

    def expand(prefix: str, gap_idx: int) -> None:
        if gap_idx == n_gaps:
            # When `&` resolves to an empty parent (root scope), the
            # selector may end up with a leading combinator + space
            # (e.g. ` .underParents`). Strip the leading whitespace —
            # less.js trims this.
            out.append((prefix + parts[gap_idx]).lstrip())
            return
        for anc in ancestors:
            expand(prefix + parts[gap_idx] + anc, gap_idx + 1)

    expand('', 0)
    return out


def join_selectors(root: Ruleset) -> None:
    """Walk the tree top-down. For each non-root Ruleset, populate
    `paths` with the cartesian product of its selectors and the parent's
    paths (with `&` substitution where it appears).

    At-rule bodies inherit the parent's paths — the @-rule wraps the
    rulesets inside without contributing to the selector chain. The
    exception is `@keyframes` (and similar "rooted" at-rules): the
    surrounding selector context never propagates into a keyframes body
    because `0%` / `from` are not real selectors.
    """

    def visit(node: Node, parent_paths: list[str]) -> None:
        if isinstance(node, Ruleset):
            if node.root:
                for r in node.rules:
                    visit(r, [''])
            else:
                paths: list[str] = []
                for sel in node.selectors:
                    sel_str = selector_to_str(sel)
                    paths.extend(_combine_with_ancestors(sel_str, parent_paths))
                node.paths = paths
                for r in node.rules:
                    visit(r, paths)
        elif isinstance(node, AtRule) and node.body is not None:
            inner_paths = [''] if _resets_selector_context(node.name) else parent_paths
            for r in node.body:
                visit(r, inner_paths)

    visit(root, [''])


def _resets_selector_context(name: str) -> bool:
    """True for at-rules whose body doesn't inherit the surrounding
    selector chain — `0%` / `from` / `to` inside `@keyframes` and the
    pure declarations inside `@font-face` / `@page` / `@counter-style`
    are not real selectors that participate in nesting.
    """
    n = name.lower()
    return n.endswith('keyframes') or n in {'@font-face', '@page', '@counter-style', '@viewport'}
