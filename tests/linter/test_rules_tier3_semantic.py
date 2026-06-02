"""Tier 3 rules — cross-feature semantic checks (detect only).

Rules whose verdict depends on multi-construct context:
`:extend` reaching across `@media`, arithmetic whose result
depends on the `math` mode, or use of constructs lessish
deliberately doesn't implement.
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


class TestExtendCrossMedia(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.target { color: red; }\n@media (max-width: 600px) {\n  .a:extend(.target) { color: blue; }\n}\n'
        out = lint(src, only={'extend-cross-media'})
        self.assertTrue(any(f.rule_id == 'extend-cross-media' for f in out))

    def test_same_scope_ok(self) -> None:
        src = '@media (max-width: 600px) {\n  .target { color: red; }\n  .a:extend(.target) { color: blue; }\n}\n'
        self.assertEqual(lint(src, only={'extend-cross-media'}), [])


if __name__ == '__main__':
    unittest.main()


class TestAmbiguousMath(unittest.TestCase):
    def test_detects_outside_parens(self) -> None:
        out = lint('.a { width: 1px + 2; }\n', only={'ambiguous-math'})
        self.assertEqual([f.rule_id for f in out], ['ambiguous-math'])

    def test_inside_parens_clean(self) -> None:
        self.assertEqual(lint('.a { width: (1px + 2); }\n', only={'ambiguous-math'}), [])


class TestUnsupportedFeature(unittest.TestCase):
    def test_detects_plugin(self) -> None:
        out = lint('@plugin "x";\n.a { color: red; }\n', only={'unsupported-feature'})
        self.assertTrue(any(f.rule_id == 'unsupported-feature' for f in out))

    def test_detects_backtick(self) -> None:
        out = lint('.a { width: `2 * 5`px; }\n', only={'unsupported-feature'})
        self.assertTrue(any(f.rule_id == 'unsupported-feature' for f in out))


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
