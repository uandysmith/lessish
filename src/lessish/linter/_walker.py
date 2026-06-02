"""DFS AST walker used by the streaming-dispatch engine.

`walk(root)` yields every node in document order. The engine groups
rules by `node_types` and calls each rule's `on_node` per matching
visit.

The walker descends into the container fields that actually carry
sub-nodes: `Ruleset.rules`, `AtRule.body`, `MixinDefinition.rules`,
plus the selector / extend / param substructures. It does NOT descend
into value-position trees (Declaration.value, Expression, Operation,
…) — those are large and only a few rules want them. Rules that need
value-position nodes can re-parse via `parse_value_text` or walk the
Declaration's subtree themselves.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..ast_nodes import (
    AtRule,
    Declaration,
    Extend,
    MixinDefinition,
    Node,
    Ruleset,
    Selector,
)


def walk(root: Node) -> Iterator[Node]:
    """Yield every structural node under `root`, including `root`."""
    yield root
    if isinstance(root, Ruleset):
        for sel in root.selectors:
            yield from _walk_selector(sel)
        for child in root.rules:
            yield from walk(child)
    elif isinstance(root, AtRule):
        if root.body is not None:
            for child in root.body:
                yield from walk(child)
    elif isinstance(root, MixinDefinition):
        for child in root.rules:
            yield from walk(child)
    elif isinstance(root, Declaration):
        # Stop here — declaration values live in `root.value` and are
        # AST sub-trees with their own shapes. Rules that want them
        # opt in explicitly.
        pass


def _walk_selector(sel: Selector) -> Iterator[Node]:
    yield sel
    for ext in sel.extend_list:
        if isinstance(ext, Extend):
            yield ext
