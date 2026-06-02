"""Inline lint-directive parsing."""

from __future__ import annotations

import unittest

from lessish.linter import Finding, LessLinter, LinterConfig


def rule_ids(src: str, *, only: set[str] | None = None) -> list[str]:
    cfg = LinterConfig(enabled=only)
    return [f.rule_id for f in LessLinter(config=cfg).check(src)]


def findings(src: str, *, only: set[str] | None = None) -> list[Finding]:
    cfg = LinterConfig(enabled=only)
    return LessLinter(config=cfg).check(src)


class TestBlockDisable(unittest.TestCase):
    def test_disable_then_enable(self) -> None:
        src = (
            '.a { color: #FF0000; }\n'
            '/* lessish-disable hex-case */\n'
            '.b { color: #FF0000; }\n'
            '/* lessish-enable hex-case */\n'
            '.c { color: #FF0000; }\n'
        )
        out = [(f.location.line, f.rule_id) for f in findings(src, only={'hex-case'})]
        self.assertEqual(out, [(1, 'hex-case'), (5, 'hex-case')])

    def test_disable_all(self) -> None:
        src = '/* lessish-disable */\n.a { color: #FF0000; }\n'
        self.assertEqual(rule_ids(src), [])

    def test_enable_without_args_clears_all(self) -> None:
        src = (
            '/* lessish-disable hex-case, hex-short */\n'
            '.a { color: #FF0000; }\n'
            '/* lessish-enable */\n'
            '.b { color: #FFFFFF; }\n'
        )
        out = findings(src, only={'hex-case', 'hex-short'})
        # `b` line should have both fire.
        self.assertTrue(any(f.rule_id == 'hex-case' and f.location.line == 4 for f in out))
        self.assertTrue(any(f.rule_id == 'hex-short' and f.location.line == 4 for f in out))


class TestLineScope(unittest.TestCase):
    def test_disable_line(self) -> None:
        src = '.a { color: #FF0000; } /* lessish-disable-line hex-case */\n'
        self.assertEqual(rule_ids(src, only={'hex-case'}), [])

    def test_disable_next_line(self) -> None:
        src = '/* lessish-disable-next-line hex-case */\n.a { color: #FF0000; }\n.b { color: #FF0000; }\n'
        out = [f.location.line for f in findings(src, only={'hex-case'})]
        self.assertEqual(out, [3])

    def test_disable_line_only_affects_that_line(self) -> None:
        src = '.a { color: #FF0000; } /* lessish-disable-line hex-case */\n.b { color: #FF0000; }\n'
        out = [f.location.line for f in findings(src, only={'hex-case'})]
        self.assertEqual(out, [2])


class TestCommaSeparated(unittest.TestCase):
    def test_multiple_rules(self) -> None:
        src = '/* lessish-disable hex-case, hex-short */\n.a { color: #FFFFFF; }\n.b { color: #FF0000; }\n'
        self.assertEqual(rule_ids(src, only={'hex-case', 'hex-short'}), [])

    def test_partial_enable(self) -> None:
        src = (
            '/* lessish-disable hex-case, hex-short */\n'
            '.a { color: #FFFFFF; }\n'
            '/* lessish-enable hex-case */\n'
            '.b { color: #FFFFFF; }\n'
        )
        out = [f.rule_id for f in findings(src, only={'hex-case', 'hex-short'})]
        self.assertEqual(out, ['hex-case'])


class TestLineCommentsWork(unittest.TestCase):
    def test_double_slash_form(self) -> None:
        src = '// lessish-disable hex-case\n.a { color: #FF0000; }\n'
        self.assertEqual(rule_ids(src, only={'hex-case'}), [])


class TestNonDirectiveCommentsIgnored(unittest.TestCase):
    def test_regular_comment_has_no_effect(self) -> None:
        src = '/* turn off hex-case please */\n.a { color: #FF0000; }\n'
        self.assertEqual(rule_ids(src, only={'hex-case'}), ['hex-case'])


class TestInlineOverridesConfig(unittest.TestCase):
    """`inline > CLI > config` precedence."""

    # `#FFAB00` triggers hex-case (uppercase) but NOT hex-short
    # (halves don't match), keeping each test focused on one rule.

    def test_inline_enable_overrides_config_disable(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = '/* lessish-enable hex-case */\n.a { color: #FFAB00; }\n'
        cfg = LinterConfig(enabled={'hex-case'}, disabled={'hex-case'})
        out = [f.rule_id for f in LessLinter(config=cfg).check(src)]
        self.assertIn('hex-case', out)

    def test_inline_enable_targeted_keeps_others_disabled(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        # `#FFFFFF` triggers both hex-case AND hex-short.
        src = '/* lessish-enable hex-case */\n.a { color: #FFFFFF; }\n'
        cfg = LinterConfig(
            enabled={'hex-case', 'hex-short'},
            disabled={'hex-case', 'hex-short'},
        )
        out = [f.rule_id for f in LessLinter(config=cfg).check(src)]
        # Only hex-case fires (inline-enabled); hex-short stays config-disabled.
        self.assertEqual(out, ['hex-case'])

    def test_inline_enable_only_in_block_scope(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = (
            '/* lessish-enable hex-case */\n'
            '.a { color: #FFAB00; }\n'
            '/* lessish-disable hex-case */\n'
            '.b { color: #FFAB00; }\n'
        )
        cfg = LinterConfig(enabled={'hex-case'}, disabled={'hex-case'})
        lines = [f.location.line for f in LessLinter(config=cfg).check(src) if f.rule_id == 'hex-case']
        self.assertEqual(lines, [2])

    def test_inline_enable_line(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = '.a { color: #FFAB00; } /* lessish-enable-line hex-case */\n.b { color: #FFAB00; }\n'
        cfg = LinterConfig(enabled={'hex-case'}, disabled={'hex-case'})
        lines = [f.location.line for f in LessLinter(config=cfg).check(src) if f.rule_id == 'hex-case']
        self.assertEqual(lines, [1])

    def test_inline_enable_next_line(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = '/* lessish-enable-next-line hex-case */\n.a { color: #FFAB00; }\n.b { color: #FFAB00; }\n'
        cfg = LinterConfig(enabled={'hex-case'}, disabled={'hex-case'})
        lines = [f.location.line for f in LessLinter(config=cfg).check(src) if f.rule_id == 'hex-case']
        self.assertEqual(lines, [2])

    def test_inline_disable_overrides_config_enable(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = '/* lessish-disable hex-case */\n.a { color: #FFAB00; }\n'
        out = LessLinter(config=LinterConfig(enabled={'hex-case'})).check(src)
        self.assertEqual(out, [])


class TestKillSwitch(unittest.TestCase):
    """`respect_inline=False` (CLI `--no-inline-config`) ignores all directives."""

    def test_kill_switch_ignores_inline_disable(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = '/* lessish-disable hex-case */\n.a { color: #FF0000; }\n'
        out = [f.rule_id for f in LessLinter(config=LinterConfig(), respect_inline=False).check(src)]
        self.assertIn('hex-case', out)

    def test_kill_switch_ignores_inline_enable(self) -> None:
        from lessish.linter import LessLinter, LinterConfig

        src = '/* lessish-enable hex-case */\n.a { color: #FFAB00; }\n'
        # Restrict to hex-case so other format rules don't add noise.
        cfg = LinterConfig(enabled={'hex-case'}, disabled={'hex-case'})
        out = LessLinter(config=cfg, respect_inline=False).check(src)
        self.assertEqual(out, [])


if __name__ == '__main__':
    unittest.main()
