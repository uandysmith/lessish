"""AST transforms triggered while resolving `@import` directives.

These helpers manipulate the imported subtree before it's spliced into
the consuming file: source-tagging for error attribution, reference-
qualifier marking, inline-pass-through wrapping, and the CSS pass-
through builder that demotes a Less-shaped `@import` into the plain
CSS form when no fetch/inline is appropriate.
"""

from __future__ import annotations

from ..ast_nodes import AtRule, MixinDefinition, Node, Ruleset
from ..source import Source
from .spec import _ImportSpec


def _tag_source(node: Node, source: Source) -> None:
    """Recursively stamp `Node._source` on every Node in the
    imported subtree so the evaluator's error-anchoring path can pick
    the right `<file>:line:column` when a failure originates inside
    an imported file. No-op when already set (so a deep import chain's
    leaves keep pointing at their original source rather than the
    nearest re-importer).
    """
    if not isinstance(node, Node) or node._source is not None:
        return
    node._source = source
    # Recurse into the shapes the evaluator may anchor at.
    for attr in ('rules', 'body', 'selectors'):
        children = getattr(node, attr, None)
        if isinstance(children, list):
            for c in children:
                if isinstance(c, Node):
                    _tag_source(c, source)


def _atrule_has_inner_rulesets(at: AtRule) -> bool:
    """True iff `at`'s body contains a Ruleset (directly or nested
    inside a sub-AtRule). Used by the reference-import filter to
    decide whether the at-rule is worth keeping for extend resolution.
    """
    if at.body is None:
        return False
    for r in at.body:
        if isinstance(r, Ruleset):
            return True
        if isinstance(r, AtRule) and _atrule_has_inner_rulesets(r):
            return True
    return False


def _mark_reference(node: Node) -> Node:
    """Recursively set `reference=True` on every Ruleset in `node`.
    Children inside an AtRule body are also marked so nested rulesets
    inherit the qualifier. MixinDefinitions get a `_was_referenced`
    tag so a later invocation's splice pass knows to clear `reference`
    on the cloned body (the inner rules' flag is an import-contract
    artefact, not a semantic intent). Other node types pass through
    unchanged.
    """
    if isinstance(node, Ruleset):
        new_rs = Ruleset(
            index=node.index,
            selectors=node.selectors,
            rules=[_mark_reference(r) for r in node.rules],
            root=node.root,
            paths=list(node.paths),
            condition=node.condition,
            reference=True,
        )
        return new_rs
    if isinstance(node, MixinDefinition):
        # MixinDefinitions imported via `@import (reference)` need a
        # tag so `_invoke_mixin_call`'s `clear_reference` decision
        # fires when this mixin is invoked from a non-reference site.
        # Recurse into the body too so nested Rulesets (`&:first-child
        # { … }` inside a mixin defined in a reference file) carry
        # `reference=True`; when invoked from a non-reference site the
        # splice-time pass clears it, but when invoked from another
        # reference scope the flag must stay so the suppression holds.
        node._was_referenced = True
        node.rules = [_mark_reference(r) for r in node.rules]
        return node
    if isinstance(node, AtRule) and node.body is not None:
        return AtRule(
            index=node.index,
            name=node.name,
            prelude=node.prelude,
            body=[_mark_reference(r) for r in node.body],
            trailing_comments=node.trailing_comments,
        )
    return node


def _inline_as_nodes(text: str, at: AtRule) -> list[Node]:
    """Wrap an `(inline)` import's body so the emit pass writes it
    verbatim. We use a Comment-with-non-silent semantics — the visitor
    would naturally drop comments, so model it as a synthetic AtRule
    whose entire prelude is the file's text and body=None.
    """
    # The simplest faithful pass-through: emit the raw text as a single
    # @-inline directive that genCSS just dumps. Use a sentinel AtRule
    # name to mark it.
    return [
        AtRule(
            index=at.index,
            name='@__inline__',
            prelude=text,
            body=None,
        )
    ]


def _pass_through_as_css(at: AtRule, spec: _ImportSpec) -> AtRule:
    """Build a CSS-only `@import` AtRule from a Less-side spec — drops
    any `(css)` / `(less)` / `(inline)` / `(reference)` qualifier so
    the emitted prelude is valid CSS. Preserves the original path
    spelling — quotes are restored when they were present in the
    source (`url("x.css")` stays `url("x.css")`, not `url(x.css)`).
    """
    quote = spec.path_quote or '"'
    inner = f'{spec.path_quote}{spec.path}{spec.path_quote}' if spec.path_quote else spec.path
    if spec.is_url_form:
        path_text = f'url({inner})'
    else:
        path_text = f'{quote}{spec.path}{quote}'
    prelude = path_text
    if spec.media:
        prelude = f'{prelude} {spec.media}'
    return AtRule(
        index=at.index,
        name=at.name,
        prelude=prelude,
        body=at.body,
        trailing_comments=at.trailing_comments,
    )
