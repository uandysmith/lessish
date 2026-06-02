"""Pre-emit safety checks + emittable-content probes.

`check_no_root_properties` raises a less.js-flavoured SyntaxError if a
non-variable Declaration survived at the top level after eval/import
resolution. The `_*_has_emittable_content` pair powers the
empty-block elision that lets `emit_css` skip `.x { }` shells and
`@media { }` wrappers around no-op bodies.
"""

from __future__ import annotations

from ..ast_nodes import AtRule, Comment, Declaration, Ruleset
from ..errors import EvalError


def check_no_root_properties(root: Ruleset) -> None:
    """Raise `SyntaxError: Properties must be inside selector blocks…`
    if a non-variable Declaration survived at the top level after
    eval/mixin/import resolution. less.js refuses to emit such a tree.

    The error is anchored at the Declaration's position. When the
    Declaration carries a `_source` attribute (set by `_tag_source` in
    the importer), the location is rendered against that imported
    file's source — so an offending `prop: 1;` in an imported file
    surfaces as `<imported.less>:L:C`, not the top-level entry point.
    """
    for r in root.rules:
        if isinstance(r, Declaration) and not r.variable:
            err = EvalError('Properties must be inside selector blocks. They cannot be in the root')
            err._less_js_name = 'SyntaxError'
            src = r._source
            if src is not None:
                err.location = src.location_at(r.index)
                err.snippet = src.snippet_around(r.index)
            else:
                err._base_index = r.index
            raise err


def _has_extend_added_descendant(rs: Ruleset) -> bool:
    """True iff any node in `rs`'s subtree carries `_extend_added_paths`.
    Used by `emit_ruleset`'s reference branch to decide whether
    recursing into a no-added-paths reference parent is worthwhile —
    avoids leaking mixin-spliced content (non-reference rulesets
    living inside a reference call site) into the emit.
    """
    for r in rs.rules:
        if isinstance(r, Ruleset):
            if r._extend_added_paths:
                return True
            if _has_extend_added_descendant(r):
                return True
        elif isinstance(r, AtRule) and r.body is not None:
            for inner in r.body:
                if isinstance(inner, Ruleset):
                    if inner._extend_added_paths:
                        return True
                    if _has_extend_added_descendant(inner):
                        return True
    return False


def _ruleset_has_emittable_content(rs: Ruleset) -> bool:
    """True iff `rs` would produce any CSS output. An empty selector
    block (`.x { }` with no declarations / non-empty nested rules) is
    silently dropped by `emit_ruleset`; this lets `emit_atrule`'s
    has-content check avoid keeping an `@media { }` wrapper around such
    a shell.
    """
    if rs.reference:
        return bool(rs._extend_added_paths)
    for r in rs.rules:
        if isinstance(r, Declaration) and not r.variable:
            return True
        if isinstance(r, Comment) and not r.silent:
            return True
        if isinstance(r, Ruleset) and _ruleset_has_emittable_content(r):
            return True
        if isinstance(r, AtRule) and _atrule_has_emittable_content(r):
            return True
    return False


def _atrule_has_emittable_content(at: AtRule) -> bool:
    """True iff `at` would produce any CSS output. `@-keyframes` is
    always emittable (its presence is CSS-meaningful even if empty);
    other block at-rules need at least one emittable rule in the body,
    and statement at-rules (no body) are always emittable.
    """
    if at._reference:
        # A `_reference`-tagged at-rule normally emits nothing — its
        # subtree was pulled in via `@import (reference)` for mixin/
        # extend resolution only. Statement-form at-rules
        # (`@import "x";`, `@namespace …;`, …) and `@-keyframes`
        # carry no Rulesets that `:extend(...)` could attach to, so
        # they stay invisible unconditionally.
        if at.body is None:
            return False
        if at.name.endswith('keyframes'):
            return False
        # Block-form: emit when any inner Ruleset gained extend-added
        # paths from an outside extender.
        return any(
            (isinstance(r, Ruleset) and _ruleset_has_emittable_content(r))
            or (isinstance(r, AtRule) and _atrule_has_emittable_content(r))
            for r in at.body
        )
    if at.body is None:
        return True
    if at.name.endswith('keyframes'):
        return True
    for r in at.body:
        if isinstance(r, Declaration):
            return True
        if isinstance(r, Comment) and not r.silent and not r._from_prelude:
            return True
        if isinstance(r, Ruleset) and _ruleset_has_emittable_content(r):
            return True
        if isinstance(r, AtRule) and _atrule_has_emittable_content(r):
            return True
    return False
