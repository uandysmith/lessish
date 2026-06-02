"""`lessish format` must preserve comments — and the inline lint
directives carried by comments — when it reflows a block.

Tier-0 line-breaking rules must not replace the gap between two
tokens with bare whitespace (which would delete a comment sitting on
a brace / semicolon line), and `decls-before-rulesets` must keep a
trailing same-line comment attached to its declaration rather than
treating it as a block-trailing comment.

These tests exercise `format` exactly as the CLI configures it.
"""

from __future__ import annotations

import unittest
import warnings

from lessish import Lessish
from lessish.linter import FixOptions, LessLinter, LinterConfig
from lessish.linter.rules import TIER_0_RULES


def fmt(src: str) -> str:
    """Format `src` the way the `lessish format` subcommand does: all
    Tier-0 rules, `blank-line-before-block` on, up to 8 convergence passes.
    """
    config = LinterConfig(
        enabled={cls.id for cls in TIER_0_RULES},
        rule_options={'blank-line-before-block': {'enabled': True}},
    )
    return LessLinter(config=config).fix(
        src, filename='styles.less', fix_options=FixOptions(safe_only=True, max_passes=8)
    )


def compile_css(src: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return Lessish().compile(src, filename='styles.less')


def hex_short_fires(src: str) -> bool:
    """True if the linter (inline directives respected) reports hex-short."""
    findings = LessLinter().check(src, filename='styles.less')
    return any(f.rule_id == 'hex-short' for f in findings)


class TestTier0InvariantWithComments(unittest.TestCase):
    """compile(format(s)) == compile(s) for same-line comments."""

    CASES = [
        '.a { color: #FFFFFF; /* lessish-disable-line hex-short */ }\n',
        '.a { /* lessish-disable hex-short */ color: #FFFFFF; }\n',
        '.a { color: red; /* mid */ width: 2px; }\n',
        '.a { color: red; /* plain */ }\n',
        '.a { color: red; /* a */ /* b */ }\n',
    ]

    def test_compile_output_unchanged(self) -> None:
        for src in self.CASES:
            with self.subTest(src=src):
                self.assertEqual(compile_css(fmt(src)), compile_css(src))

    def test_comment_text_not_dropped(self) -> None:
        for src in self.CASES:
            with self.subTest(src=src):
                # Every comment body present in the input survives.
                for comment in ('disable-line', 'disable hex-short', 'mid', 'plain', '/* a */', '/* b */'):
                    if comment in src:
                        self.assertIn(comment, fmt(src))


class TestDisableLineDirectiveSurvives(unittest.TestCase):
    def test_documented_example(self) -> None:
        # The example from docs/linter/02-configuration.md.
        src = '.a { color: #FFFFFF; /* lessish-disable-line hex-short */ }\n'
        # Suppressed before formatting…
        self.assertFalse(hex_short_fires(src))
        out = fmt(src)
        # …the directive stays on the declaration's own line…
        self.assertIn('color: #FFFFFF; /* lessish-disable-line hex-short */', out)
        # …and stays effective afterwards.
        self.assertFalse(hex_short_fires(out))

    def test_trailing_directive_stays_on_declaration_line(self) -> None:
        # A line-scoped directive must remain on the line it governs, never
        # be pushed onto its own line (which would target nothing).
        out = fmt('.a { color: #FFFFFF; /* lessish-disable-line hex-short */ }\n')
        for line in out.splitlines():
            if 'lessish-disable-line' in line:
                self.assertIn('#FFFFFF', line, f'directive detached from its code: {line!r}')


class TestDeclsBeforeRulesetsTrailingComment(unittest.TestCase):
    def test_reorder_keeps_trailing_directive_attached(self) -> None:
        src = '.card {\n  .nested { color: red; }\n  color: #FFFFFF; /* lessish-disable-line hex-short */\n}\n'
        self.assertFalse(hex_short_fires(src))
        out = fmt(src)
        # The declaration moved ahead of the nested ruleset, but the
        # directive rode along on its line.
        self.assertIn('color: #FFFFFF; /* lessish-disable-line hex-short */', out)
        self.assertFalse(hex_short_fires(out))
        self.assertEqual(compile_css(out), compile_css(src))

    def test_format_is_idempotent(self) -> None:
        src = '.card {\n  .nested { color: red; }\n  color: #FFFFFF; /* lessish-disable-line hex-short */\n}\n'
        once = fmt(src)
        self.assertEqual(fmt(once), once)


if __name__ == '__main__':
    unittest.main()
