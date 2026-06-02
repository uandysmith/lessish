"""Targeted unit tests for `lessish.visitors.checks`.

The helpers in `visitors/checks.py` decide whether emit drops empty
shells and which root-level constructs are rejected. They're tiny
but cross-cutting; integration tests catch the common paths but
miss several minority branches. These cases drive each remaining
branch directly via hand-built AST nodes.
"""

from __future__ import annotations

import unittest

from lessish.ast_nodes import Anonymous, AtRule, Comment, Declaration, Element, Ruleset, Selector
from lessish.errors import EvalError
from lessish.source import Source
from lessish.visitors.checks import (
    _atrule_has_emittable_content,
    _has_extend_added_descendant,
    _ruleset_has_emittable_content,
    check_no_root_properties,
)


def _decl(name: str = 'color', value: str = 'red', variable: bool = False) -> Declaration:
    return Declaration(
        index=0,
        name=name,
        value=Anonymous(index=0, value=value),
        variable=variable,
    )


def _ruleset(rules: list, ref: bool = False, paths: list[str] | None = None) -> Ruleset:
    rs = Ruleset(
        index=0,
        selectors=[Selector(index=0, elements=[Element(index=0, combinator='', value='.x')])],
        rules=rules,
        reference=ref,
    )
    if paths is not None:
        rs._extend_added_paths = list(paths)
    return rs


def _atrule(
    name: str = '@media',
    body: list | None = None,
    *,
    reference: bool = False,
    prelude: str = '',
) -> AtRule:
    at = AtRule(index=0, name=name, prelude=prelude, body=body)
    at._reference = reference
    return at


class CheckNoRootPropertiesTests(unittest.TestCase):
    """`check_no_root_properties`: a Declaration tagged with `_source`
    from an importer must anchor its error at the imported file's
    location, not the entry-point source.
    """

    def test_root_decl_in_imported_subtree_anchors_at_imported_source(self) -> None:
        # Build a root-level Declaration tagged with a `_source` —
        # simulates what the importer's `_tag_source` does for
        # everything in an imported subtree.
        imported = Source(text='\n\n  color: red;', filename='imported.less')
        decl = _decl()
        decl.index = 4  # pretend the offending position lives in `imported`
        decl._source = imported
        root = _ruleset([decl])
        root.root = True
        with self.assertRaises(EvalError) as cm:
            check_no_root_properties(root)
        err = cm.exception
        self.assertIsNotNone(err.location)
        # The location was rendered against `imported`, not against
        # any outer source — confirmed by checking the filename.
        assert err.location is not None  # for mypy
        self.assertEqual(err.location.filename, 'imported.less')
        self.assertTrue(err.snippet)

    def test_root_variable_decl_is_allowed(self) -> None:
        # Variable declarations at root are legal (`@x: 1;`); only
        # non-variable property decls trip the check.
        root = _ruleset([_decl(name='@x', variable=True)])
        root.root = True
        check_no_root_properties(root)  # must NOT raise


class HasExtendAddedDescendantTests(unittest.TestCase):
    """`_has_extend_added_descendant`: recursion into nested Rulesets
    and into AtRule bodies."""

    def test_recurses_into_nested_ruleset(self) -> None:
        # Outer ruleset has no added paths; inner one does.
        inner = _ruleset([_decl()], paths=['.added'])
        outer = _ruleset([inner])
        self.assertTrue(_has_extend_added_descendant(outer))

    def test_atrule_body_carries_extended_inner_ruleset(self) -> None:
        # `@media` body wraps a Ruleset that's been extend-augmented.
        inner = _ruleset([_decl()], paths=['.added'])
        at = _atrule(name='@media', body=[inner])
        outer = _ruleset([at])
        self.assertTrue(_has_extend_added_descendant(outer))

    def test_atrule_body_recurses_into_deeper_ruleset(self) -> None:
        # Hit the recursive `_has_extend_added_descendant(inner)`
        # branch inside the AtRule-body loop.
        deep = _ruleset([_decl()], paths=['.added'])
        mid = _ruleset([deep])
        at = _atrule(name='@media', body=[mid])
        outer = _ruleset([at])
        self.assertTrue(_has_extend_added_descendant(outer))

    def test_returns_false_when_nothing_extended(self) -> None:
        # No `_extend_added_paths` anywhere → False.
        inner = _ruleset([_decl()])
        at = _atrule(name='@media', body=[_ruleset([_decl()])])
        outer = _ruleset([inner, at])
        self.assertFalse(_has_extend_added_descendant(outer))

    def test_atrule_body_with_mixed_children_recurses_past_non_rulesets(self) -> None:
        # AtRule body interleaves a non-Ruleset (Declaration) and a
        # Ruleset; the inner-body for-loop must skip the Declaration
        # and recurse into the Ruleset. Hits the `for inner in r.body`
        # iteration's "no-op continue" branch.
        decl = _decl()
        inner_rs = _ruleset([_decl()], paths=['.added'])
        at = _atrule(name='@media', body=[decl, inner_rs])
        outer = _ruleset([at])
        self.assertTrue(_has_extend_added_descendant(outer))

    def test_recurses_into_deeper_nested_ruleset(self) -> None:
        # Outer's direct child has NO paths; grand-child does. The
        # `r._extend_added_paths` check fails on the middle ruleset,
        # so we hit the `if _has_extend_added_descendant(r):` recurse.
        deepest = _ruleset([_decl()], paths=['.added'])
        middle = _ruleset([deepest])
        outer = _ruleset([middle])
        self.assertTrue(_has_extend_added_descendant(outer))


class RulesetHasEmittableContentTests(unittest.TestCase):
    """`_ruleset_has_emittable_content`: inner AtRule with emittable
    content fires `True`."""

    def test_inner_atrule_with_decl_makes_ruleset_emittable(self) -> None:
        # `.x { @media (min-width:0) { color: red; } }` — `.x`'s
        # direct children include only the AtRule; the
        # `isinstance(r, AtRule)` branch must return True via the
        # nested `_atrule_has_emittable_content` call.
        media = _atrule(name='@media', body=[_decl()])
        rs = _ruleset([media])
        self.assertTrue(_ruleset_has_emittable_content(rs))

    def test_ruleset_with_only_inert_content_returns_false(self) -> None:
        # No declarations, no visible comments, only a variable decl
        # and an empty inner ruleset — the for-loop falls through to
        # the trailing `return False`.
        empty_inner = _ruleset([])
        rs = _ruleset(
            [
                _decl(name='@x', variable=True),  # variable: not emittable
                Comment(index=0, text='// hidden', silent=True),  # silent
                empty_inner,  # nested but vacant
            ]
        )
        self.assertFalse(_ruleset_has_emittable_content(rs))

    def test_reference_ruleset_without_extends_returns_false(self) -> None:
        # `reference=True` short-circuits before the rules-loop:
        # emittable iff some extender added paths. With none, False.
        rs = _ruleset([_decl()], ref=True)
        # `_extend_added_paths` is the default empty list.
        self.assertFalse(_ruleset_has_emittable_content(rs))

    def test_reference_ruleset_with_extends_is_emittable(self) -> None:
        # Same but with extender-added paths → emittable.
        rs = _ruleset([_decl()], ref=True, paths=['.outsider'])
        self.assertTrue(_ruleset_has_emittable_content(rs))


class AtRuleHasEmittableContentTests(unittest.TestCase):
    """`_atrule_has_emittable_content`: `_reference`-tagged statement-form
    at-rule → False; visible comment inside body wins; AtRule-only body
    with no emittable content → False."""

    def test_reference_statement_atrule_returns_false(self) -> None:
        # Statement-form `_reference`-tagged at-rule (body=None) is
        # invisible.
        at = _atrule(name='@import', body=None, reference=True, prelude='"x"')
        self.assertFalse(_atrule_has_emittable_content(at))

    def test_reference_keyframes_returns_false(self) -> None:
        # Reference-tagged @-keyframes: also returns False (no
        # selector inside that an extend can attach to).
        at = _atrule(name='@keyframes', body=[_ruleset([_decl()])], reference=True)
        self.assertFalse(_atrule_has_emittable_content(at))

    def test_visible_comment_in_atrule_body_is_emittable(self) -> None:
        # A non-silent, non-from-prelude comment counts as emittable
        # content.
        c = Comment(index=0, text='/* keep */', silent=False)
        c._from_prelude = False
        at = _atrule(name='@media', body=[c], prelude='print')
        self.assertTrue(_atrule_has_emittable_content(at))

    def test_atrule_body_with_only_empty_atrule_returns_false(self) -> None:
        # `@media (...) { @page {} }` — outer @media has only an
        # inner @page with an empty body. The inner returns False
        # via the same helper; the outer falls through to the
        # final `return False`.
        inner_empty = _atrule(name='@page', body=[])
        outer = _atrule(name='@media', body=[inner_empty], prelude='print')
        self.assertFalse(_atrule_has_emittable_content(outer))

    def test_atrule_body_with_only_silent_comment_returns_false(self) -> None:
        # Silent comment (`//` line) doesn't count.
        c = Comment(index=0, text='// hi', silent=True)
        at = _atrule(name='@media', body=[c], prelude='print')
        self.assertFalse(_atrule_has_emittable_content(at))

    def test_atrule_body_with_prelude_comment_returns_false(self) -> None:
        # `_from_prelude`-tagged comments don't count either — they
        # were hoisted from the at-rule's own prelude and re-emitted
        # there, so duplicating them as body content would be wrong.
        c = Comment(index=0, text='/* k */', silent=False)
        c._from_prelude = True
        at = _atrule(name='@media', body=[c], prelude='print')
        self.assertFalse(_atrule_has_emittable_content(at))


if __name__ == '__main__':
    unittest.main()
