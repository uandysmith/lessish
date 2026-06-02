"""Tier 3 rules — code-complexity / overuse smells (detect only).

No autofix: removing the smell requires human judgement
(extract a variable, restructure the nesting, refactor the
mixin signature).
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


class TestDeepNesting(unittest.TestCase):
    def test_detects_default_depth_4(self) -> None:
        # Nesting depth 5.
        src = '.a { .b { .c { .d { .e { color: red; } } } } }\n'
        out = lint(src, only={'deep-nesting'})
        self.assertTrue(any(f.rule_id == 'deep-nesting' for f in out))

    def test_shallow_is_clean(self) -> None:
        src = '.a { .b { color: red; } }\n'
        out = lint(src, only={'deep-nesting'})
        self.assertEqual(out, [])

    def test_configurable_max_depth(self) -> None:
        src = '.a { .b { .c { color: red; } } }\n'
        out = lint(src, only={'deep-nesting'}, options={'deep-nesting': {'max-depth': 2}})
        self.assertTrue(any(f.rule_id == 'deep-nesting' for f in out))


class TestMagicNumber(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.a { padding: 14px; } .b { margin: 14px; } .c { width: 14px; }\n'
        out = lint(src, only={'magic-number'})
        # 3 occurrences of 14px, all flagged.
        self.assertEqual(len([f for f in out if f.rule_id == 'magic-number']), 3)

    def test_below_threshold(self) -> None:
        src = '.a { padding: 14px; } .b { margin: 14px; }\n'
        self.assertEqual(lint(src, only={'magic-number'}), [])

    def test_extracted_var_silences(self) -> None:
        src = '@base: 14px;\n.a { padding: 14px; }\n.b { margin: 14px; }\n.c { width: 14px; }\n'
        self.assertEqual(lint(src, only={'magic-number'}), [])

    def test_configurable_threshold(self) -> None:
        src = '.a { padding: 14px; } .b { margin: 14px; }\n'
        out = lint(
            src,
            only={'magic-number'},
            options={'magic-number': {'min-occurrences': 2}},
        )
        self.assertEqual(len([f for f in out if f.rule_id == 'magic-number']), 2)


class TestImportantOveruse(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.a { color: red !important; }\n'
        out = lint(src, only={'important-overuse'})
        self.assertEqual([f.rule_id for f in out], ['important-overuse'])

    def test_threshold_zero_default(self) -> None:
        # Any !important fires.
        out = lint(
            '.a { color: red !important; }\n.b { width: 0 !important; }\n',
            only={'important-overuse'},
        )
        self.assertEqual(len(out), 2)

    def test_configurable_threshold(self) -> None:
        # max-per-file=5: not enough to trigger.
        out = lint(
            '.a { color: red !important; }\n',
            only={'important-overuse'},
            options={'important-overuse': {'max-per-file': 5}},
        )
        self.assertEqual(out, [])


class TestExcessiveMixinArgs(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.box(@a, @b, @c, @d, @e, @f) { padding: 0; }\n.a { .box(1,2,3,4,5,6); }\n'
        out = lint(src, only={'excessive-mixin-args'})
        self.assertEqual([f.rule_id for f in out], ['excessive-mixin-args'])

    def test_below_threshold(self) -> None:
        src = '.box(@a, @b) { padding: 0; }\n'
        self.assertEqual(lint(src, only={'excessive-mixin-args'}), [])

    def test_configurable(self) -> None:
        src = '.box(@a, @b, @c) { padding: 0; }\n'
        out = lint(
            src,
            only={'excessive-mixin-args'},
            options={'excessive-mixin-args': {'max-args': 3}},
        )
        self.assertEqual([f.rule_id for f in out], ['excessive-mixin-args'])


if __name__ == '__main__':
    unittest.main()
