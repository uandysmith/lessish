"""Tier 2 rules — risky rewrites that need `--unsafe-fix` or `--project`.

Removing an unused-looking variable / mixin can change observable
behaviour if a cross-file reference exists. Fixes only apply
under explicit opt-in.
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


class TestUnusedVariable(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('@unused: red;\n.a { color: blue; }\n', only={'unused-variable'})
        self.assertEqual([f.rule_id for f in out], ['unused-variable'])

    def test_used_in_value_position(self) -> None:
        self.assertEqual(
            lint('@c: red;\n.a { color: @c; }\n', only={'unused-variable'}),
            [],
        )

    def test_used_in_interpolation(self) -> None:
        self.assertEqual(
            lint('@a: 5;\n.x { padding: @{a}px; }\n', only={'unused-variable'}),
            [],
        )

    def test_used_in_atrule_prelude(self) -> None:
        self.assertEqual(
            lint('@p: print;\n@media (@p) { .a { color: red; } }\n', only={'unused-variable'}),
            [],
        )

    def test_chained_use(self) -> None:
        self.assertEqual(
            lint('@a: 5;\n@b: @a;\n.x { width: @b; }\n', only={'unused-variable'}),
            [],
        )

    def test_arguments_keyword_not_flagged(self) -> None:
        # `@arguments` is a magic mixin variable; never flag.
        src = '@arguments: 1;\n.a { color: red; }\n'
        out = lint(src, only={'unused-variable'})
        self.assertEqual(out, [])

    def test_unsafe_fix_removes_decl(self) -> None:
        self.assertEqual(
            unsafe_fix('@unused: red;\n.a { color: blue; }\n', only={'unused-variable'}),
            '.a { color: blue; }\n',
        )

    def test_safe_fix_keeps_decl(self) -> None:
        # Without --unsafe-fix, the decl stays.
        cfg = LinterConfig(enabled={'unused-variable'})
        linter = LessLinter(config=cfg)
        src = '@unused: red;\n.a { color: blue; }\n'
        self.assertEqual(linter.fix(src), src)


class TestUnusedVariableLastInBlock(unittest.TestCase):
    def test_last_decl_without_semicolon(self) -> None:
        # `@x: red }` — value span hits `}` at depth 0 (no trailing `;`).
        out = lint('.a { @x: red }', only={'unused-variable'})
        self.assertEqual([f.rule_id for f in out], ['unused-variable'])


class TestUnusedMixin(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.dead() { color: red; }\n.a { color: blue; }\n', only={'unused-mixin'})
        self.assertEqual([f.rule_id for f in out], ['unused-mixin'])

    def test_used_mixin_skipped(self) -> None:
        src = '.alive() { color: red; }\n.a { .alive(); }\n'
        self.assertEqual(lint(src, only={'unused-mixin'}), [])

    def test_namespace_call_marks_inner_used(self) -> None:
        src = '#ns { .inner() { color: red; } }\n.a { #ns.inner(); }\n'
        self.assertEqual(lint(src, only={'unused-mixin'}), [])

    def test_extend_clause_counts_as_use(self) -> None:
        src = '.target { color: red; }\n.a:extend(.target) { color: blue; }\n'
        self.assertEqual(lint(src, only={'unused-mixin'}), [])

    def test_unsafe_fix_removes(self) -> None:
        src = '.dead() { color: red; }\n.a { color: blue; }\n'
        self.assertEqual(
            unsafe_fix(src, only={'unused-mixin'}),
            '.a { color: blue; }\n',
        )


class TestDuplicateProperty(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { color: red; color: red; }\n', only={'duplicate-property'})
        self.assertEqual([f.rule_id for f in out], ['duplicate-property'])

    def test_different_values_not_flagged(self) -> None:
        # Common fallback pattern — don't false-positive.
        self.assertEqual(
            lint('.a { color: red; color: rgba(0,0,0,0.5); }\n', only={'duplicate-property'}),
            [],
        )

    def test_important_difference(self) -> None:
        # !important changes the cascade, so it's not really a duplicate.
        self.assertEqual(
            lint('.a { color: red; color: red !important; }\n', only={'duplicate-property'}),
            [],
        )

    def test_unsafe_fix_drops_earlier(self) -> None:
        src = '.a {\n  color: red;\n  color: red;\n}\n'
        self.assertEqual(
            unsafe_fix(src, only={'duplicate-property'}),
            '.a {\n  color: red;\n}\n',
        )

    def test_nested_rulesets_isolated(self) -> None:
        # Same property in sibling rulesets is fine.
        src = '.a { color: red; } .b { color: red; }\n'
        self.assertEqual(lint(src, only={'duplicate-property'}), [])


class TestDuplicatePropertyWithValueParens(unittest.TestCase):
    """`duplicate-property` value-span scanner tracks `(...)` depth so
    a `;` inside function args doesn't terminate the value early."""

    def test_detects_duplicate_with_function_call_values(self) -> None:
        out = lint(
            '.a { color: rgb(0,0,0); color: rgb(0,0,0); }\n',
            only={'duplicate-property'},
        )
        self.assertEqual([f.rule_id for f in out], ['duplicate-property'])


class TestRedundantMixinArgs(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.box(@size: 10px) { width: @size; }\n.a { .box(10px); }\n'
        out = lint(src, only={'redundant-mixin-args'})
        self.assertEqual([f.rule_id for f in out], ['redundant-mixin-args'])

    def test_different_value_not_flagged(self) -> None:
        src = '.box(@size: 10px) { width: @size; }\n.a { .box(20px); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_named_args_skipped(self) -> None:
        src = '.box(@size: 10px) { width: @size; }\n.a { .box(@size: 10px); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_two_definitions_skipped(self) -> None:
        src = '.box() { padding: 0; }\n.box(@size: 10px) { width: @size; }\n.a { .box(10px); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_fix(self) -> None:
        src = '.box(@size: 10px) { width: @size; }\n.a { .box(10px); }\n'
        self.assertEqual(
            unsafe_fix(src, only={'redundant-mixin-args'}),
            '.box(@size: 10px) { width: @size; }\n.a { .box(); }\n',
        )

    def test_all_defaults_match(self) -> None:
        src = '.box(@size: 10px, @c: red) { width: @size; }\n.a { .box(10px, red); }\n'
        self.assertEqual(
            [f.rule_id for f in lint(src, only={'redundant-mixin-args'})],
            ['redundant-mixin-args'],
        )

    def test_definition_with_pattern_arg_skipped(self) -> None:
        # `.box("foo", @x: 10px)` — the literal `"foo"` is a pattern;
        # the rule refuses to suggest stripping defaults around it.
        src = '.box("foo", @x: 10px) { width: @x; }\n.a { .box("foo", 10px); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_call_with_more_args_than_params_skipped(self) -> None:
        # Arity mismatch — _all_args_match_defaults bails.
        src = '.box(@s: 10px) { width: @s; }\n.a { .box(10px, 20px); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_call_with_different_type_arg_not_flagged(self) -> None:
        # `red` vs default `10px` — different node types (Color vs Dim).
        src = '.box(@s: 10px) { width: @s; }\n.a { .box(red); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_call_with_different_unit_not_flagged(self) -> None:
        # Same value but different unit — `_values_match` walks unit field.
        src = '.box(@s: 10px) { width: @s; }\n.a { .box(10em); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])

    def test_call_against_variadic_param_skipped(self) -> None:
        # `@list...` is variadic — no default to compare against.
        src = '.box(@list...) { width: @list; }\n.a { .box(1); }\n'
        self.assertEqual(lint(src, only={'redundant-mixin-args'}), [])


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
