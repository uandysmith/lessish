"""Tier 1 rules — safe token-level rewrites.

Compiled CSS may differ byte-wise (`#FFFFFF` → `#fff`) but
renders identically. Applied automatically by `--fix` and
`lessish format`.
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


class TestHexShort(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { color: #FFFFFF; }\n', only={'hex-short'})
        self.assertEqual([f.rule_id for f in out], ['hex-short'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { color: #FFFFFF; }\n', only={'hex-short'}),
            '.a { color: #fff; }\n',
        )

    def test_no_false_positive(self) -> None:
        # `#FFAB00` has `AB` (≠ `A`), so it's not shortenable.
        self.assertEqual(lint('.a { color: #FFAB00; }\n', only={'hex-short'}), [])


class TestHexCase(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { color: #FF0000; }\n', only={'hex-case'})
        self.assertEqual([f.rule_id for f in out], ['hex-case'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { color: #FF0000; }\n', only={'hex-case'}),
            '.a { color: #ff0000; }\n',
        )

    def test_already_lowercase(self) -> None:
        self.assertEqual(lint('.a { color: #abc123; }\n', only={'hex-case'}), [])


class TestZeroUnit(unittest.TestCase):
    def test_detects_px(self) -> None:
        out = lint('.a { width: 0px; }\n', only={'zero-unit'})
        self.assertEqual([f.rule_id for f in out], ['zero-unit'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { width: 0px; }\n', only={'zero-unit'}),
            '.a { width: 0; }\n',
        )

    def test_skips_calc(self) -> None:
        self.assertEqual(lint('.a { width: calc(100% - 0px); }\n', only={'zero-unit'}), [])

    def test_skips_nonzero(self) -> None:
        self.assertEqual(lint('.a { width: 10px; }\n', only={'zero-unit'}), [])

    def test_percent(self) -> None:
        self.assertEqual(
            fix('.a { width: 0%; }\n', only={'zero-unit'}),
            '.a { width: 0; }\n',
        )

    def test_zero_followed_by_non_css_ident(self) -> None:
        # `0foo` — `foo` isn't a CSS unit; not flagged.
        self.assertEqual(lint('.a { width: 0foo; }\n', only={'zero-unit'}), [])

    def test_zero_followed_by_space_ident(self) -> None:
        # Whitespace between `0` and the next ident → not a unit, not flagged.
        self.assertEqual(lint('.a { width: 0 ident; }\n', only={'zero-unit'}), [])

    def test_zero_at_eof_no_following_token(self) -> None:
        # `@x: 0` (no `;`/EOF after `0`) — no next token to inspect.
        self.assertEqual(lint('@x: 0', only={'zero-unit'}), [])


class TestDecimalLeadingZero(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { opacity: .5; }\n', only={'decimal-leading-zero'})
        self.assertEqual([f.rule_id for f in out], ['decimal-leading-zero'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { opacity: .5; }\n', only={'decimal-leading-zero'}),
            '.a { opacity: 0.5; }\n',
        )


class TestTrailingZero(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { width: 1.500px; }\n', only={'trailing-zero'})
        self.assertEqual([f.rule_id for f in out], ['trailing-zero'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { width: 1.500px; }\n', only={'trailing-zero'}),
            '.a { width: 1.5px; }\n',
        )

    def test_drops_whole_decimal(self) -> None:
        self.assertEqual(
            fix('.a { width: 2.0em; }\n', only={'trailing-zero'}),
            '.a { width: 2em; }\n',
        )

    def test_no_false_positive(self) -> None:
        self.assertEqual(lint('.a { width: 1.5px; }\n', only={'trailing-zero'}), [])


if __name__ == '__main__':
    unittest.main()
