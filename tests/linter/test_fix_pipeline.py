"""Verify fix idempotency, the Tier-0 compile-invariant, and how
fixes cascade across multiple rules in one pass.
"""

from __future__ import annotations

import unittest

from lessish import Lessish
from lessish.linter import LessLinter, LinterConfig


def fix(src: str, *, only: set[str] | None = None) -> str:
    cfg = LinterConfig(enabled=only)
    return LessLinter(config=cfg).fix(src)


TIER0_RULES = {
    'trailing-whitespace',
    'final-newline',
    'no-tabs',
    'no-multiple-blank-lines',
    'space-after-colon',
    'space-before-lbrace',
}

TIER1_RULES = {
    'hex-short',
    'hex-case',
    'zero-unit',
    'decimal-leading-zero',
    'trailing-zero',
}


SAMPLES = [
    '.a { color:#FFFFFF; }\n',
    '.a {\n  margin: .5em;\n  padding: 0px;\n  border: 1.500px;\n}\n',
    '.a{color: red;width: 0%;}\n',
    'a {\n\tcolor: red;\n}\n',
    '.a {\n  color: red;   \n}\n\n\n',
]


class TestIdempotency(unittest.TestCase):
    def test_safe_fixes_are_idempotent(self) -> None:
        linter = LessLinter()
        for src in SAMPLES:
            once = linter.fix(src)
            twice = linter.fix(once)
            self.assertEqual(once, twice, msg=f'fix not idempotent for {src!r}')


class TestTier0InvariantHoldsOnCompile(unittest.TestCase):
    def test_tier0_only_keeps_compile_output_stable(self) -> None:
        cfg = LinterConfig(enabled=TIER0_RULES)
        linter = LessLinter(config=cfg)
        ls = Lessish()
        for src in SAMPLES:
            try:
                orig = ls.compile(src)
            except Exception:
                continue
            fixed = linter.fix(src)
            self.assertEqual(
                ls.compile(fixed),
                orig,
                msg=f'Tier-0 fix changed compile output for {src!r}',
            )


class TestCascadingFixes(unittest.TestCase):
    def test_zero_unit_then_trailing_zero(self) -> None:
        # `0.0px` should become `0` after both rules.
        self.assertEqual(
            fix('.a { width: 0.0px; }\n', only={'zero-unit', 'trailing-zero'}),
            '.a { width: 0; }\n',
        )


if __name__ == '__main__':
    unittest.main()
