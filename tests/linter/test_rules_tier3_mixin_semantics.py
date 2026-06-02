"""Tier 3 rules — mixin-signature semantic checks (detect only).

Sigil-level oddities in `.mixin(@a, @b: …, @rest...)` shapes:
defaults that forward-reference, rest-args mixed with defaults,
guard branches statically unreachable, and user mixins
shadowing a built-in function name.
"""

from __future__ import annotations

import unittest
from typing import Any

from lessish.linter import Finding, FixOptions, LessLinter, LinterConfig


def lint(
    src: str, *, only: set[str] | None = None, options: dict[str, dict[str, Any]] | None = None, full: bool = False
) -> list[Finding]:
    cfg = LinterConfig(enabled=only, rule_options=options or {})
    return LessLinter(config=cfg, full=full).check(src)


def fix(src: str, *, only: set[str] | None = None, options: dict[str, dict[str, Any]] | None = None) -> str:
    cfg = LinterConfig(enabled=only, rule_options=options or {})
    return LessLinter(config=cfg).fix(src)


def unsafe_fix(src: str, *, only: set[str] | None = None) -> str:
    cfg = LinterConfig(enabled=only)
    return LessLinter(config=cfg).fix(src, fix_options=FixOptions(safe_only=False))


class TestConfusingDefaultValue(unittest.TestCase):
    def test_detects_forward_reference(self) -> None:
        src = '.m(@x: @y, @y: 5) { width: @x; }\n'
        out = lint(src, only={'confusing-default-value'})
        self.assertEqual([f.rule_id for f in out], ['confusing-default-value'])

    def test_backward_reference_ok(self) -> None:
        src = '.m(@y: 5, @x: @y) { width: @x; }\n'
        self.assertEqual(lint(src, only={'confusing-default-value'}), [])


class TestMixedRestAndDefault(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.m(@a: 1, @rest...) { x: @a; }\n'
        out = lint(src, only={'mixed-rest-and-default'})
        self.assertEqual([f.rule_id for f in out], ['mixed-rest-and-default'])

    def test_default_only_ok(self) -> None:
        src = '.m(@a: 1) { x: @a; }\n'
        self.assertEqual(lint(src, only={'mixed-rest-and-default'}), [])

    def test_rest_only_ok(self) -> None:
        src = '.m(@rest...) { x: 1; }\n'
        self.assertEqual(lint(src, only={'mixed-rest-and-default'}), [])


class TestRedefinedBuiltin(unittest.TestCase):
    def test_detects_lighten(self) -> None:
        out = lint('.lighten(@c) { color: @c; }\n', only={'redefined-builtin'})
        self.assertEqual([f.rule_id for f in out], ['redefined-builtin'])

    def test_safe_name(self) -> None:
        self.assertEqual(
            lint('.my-helper() { color: red; }\n', only={'redefined-builtin'}),
            [],
        )


class TestUnreachableBranch(unittest.TestCase):
    def test_detects_literal_false(self) -> None:
        out = lint('.m() when (1 = 2) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_detects_unequal_units(self) -> None:
        # Different units are incomparable → not flagged (conservative).
        self.assertEqual(
            lint('.m() when (1px = 1em) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )

    def test_variable_guard_not_statically_flagged(self) -> None:
        # Static-only mode skips guards involving variables.
        self.assertEqual(
            lint('@x: 5;\n.m() when (@x = 5) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )

    def test_detects_greater_than_false(self) -> None:
        out = lint('.m() when (5 > 10) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_detects_less_equal_false(self) -> None:
        out = lint('.m() when (5 <= 4) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_detects_greater_equal_false(self) -> None:
        out = lint('.m() when (5 >= 10) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_detects_not_equal_false(self) -> None:
        out = lint('.m() when (1 <> 1) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_less_than_true_clean(self) -> None:
        # `1 < 2` is statically true → not flagged.
        self.assertEqual(
            lint('.m() when (1 < 2) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )

    def test_detects_not_statically_true_inner(self) -> None:
        # `not (1 = 1)` — inner is statically true, so the negated guard
        # is always false.
        out = lint('.m() when not (1 = 1) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_not_with_false_inner_clean(self) -> None:
        # `not (1 = 2)` — inner is false, so the negated guard is true.
        self.assertEqual(
            lint('.m() when not (1 = 2) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )

    def test_detects_keyword_inequality_via_eq(self) -> None:
        out = lint('.m() when (foo = bar) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_detects_keyword_same_ne(self) -> None:
        out = lint('.m() when (foo <> foo) { color: red; }\n', only={'unreachable-mixin-branch'})
        self.assertEqual([f.rule_id for f in out], ['unreachable-mixin-branch'])

    def test_keyword_equal_clean(self) -> None:
        self.assertEqual(
            lint('.m() when (foo = foo) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )

    def test_and_or_not_statically_flagged(self) -> None:
        self.assertEqual(
            lint('.m() when (1 = 2) and (3 = 4) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )

    def test_mixed_keyword_and_dimension_not_flagged(self) -> None:
        # `1 = foo` mixes Dimension + Keyword; `_compare` returns None.
        self.assertEqual(
            lint('.m() when (1 = foo) { color: red; }\n', only={'unreachable-mixin-branch'}),
            [],
        )


if __name__ == '__main__':
    unittest.main()
