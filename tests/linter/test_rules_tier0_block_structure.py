"""Tier 0 rules — block-level shape: braces, semicolons, blank lines.

Tightens the structure of a Less file (`.x{...}` → `.x {\n  ...\n}`,
`decls-before-rulesets` reordering, blank-line rules around
block boundaries). Same Tier 0 fix-invariant as
`test_rules_tier0_whitespace.py`: compiled CSS is byte-identical
before and after the fix.
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


class TestSemicolonRequired(unittest.TestCase):
    def test_detects_missing_trailing_semicolon(self) -> None:
        out = lint('.a { color: red }\n', only={'semicolon-required'})
        self.assertEqual([f.rule_id for f in out], ['semicolon-required'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { color: red }\n', only={'semicolon-required'}),
            '.a { color: red; }\n',
        )

    def test_empty_block_not_flagged(self) -> None:
        self.assertEqual(lint('.a {}\n', only={'semicolon-required'}), [])


class TestNoLoneSemicolon(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { ; }\n', only={'no-trailing-semicolon-in-empty-block'})
        self.assertTrue(any(f.rule_id == 'no-trailing-semicolon-in-empty-block' for f in out))

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { ; }\n', only={'no-trailing-semicolon-in-empty-block'}),
            '.a {  }\n',
        )


class TestBlankLineAtBlockStart(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.a {\n\n  color: red;\n}\n'
        out = lint(src, only={'blank-line-at-block-start'})
        self.assertEqual([f.rule_id for f in out], ['blank-line-at-block-start'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a {\n\n  color: red;\n}\n', only={'blank-line-at-block-start'}),
            '.a {\n  color: red;\n}\n',
        )


class TestBlankLineAtBlockEnd(unittest.TestCase):
    def test_detects(self) -> None:
        src = '.a {\n  color: red;\n\n}\n'
        out = lint(src, only={'blank-line-at-block-end'})
        self.assertEqual([f.rule_id for f in out], ['blank-line-at-block-end'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a {\n  color: red;\n\n}\n', only={'blank-line-at-block-end'}),
            '.a {\n  color: red;\n}\n',
        )


class TestBlankLineBeforeBlock(unittest.TestCase):
    def test_off_by_default(self) -> None:
        # Adjacent rulesets, no blank — without explicit enabled=true.
        out = lint('.a {}\n.b {}\n', only={'blank-line-before-block'})
        self.assertEqual(out, [])

    def test_detects_when_enabled(self) -> None:
        out = lint(
            '.a {}\n.b {}\n',
            only={'blank-line-before-block'},
            options={'blank-line-before-block': {'enabled': True}},
        )
        self.assertTrue(any(f.rule_id == 'blank-line-before-block' for f in out))


class TestBraceOpeningOwnLine(unittest.TestCase):
    def test_off_by_default(self) -> None:
        out = lint('.a\n{\n  color: red;\n}\n', only={'block-opening-brace-line'})
        self.assertEqual(out, [])

    def test_detects_when_enabled(self) -> None:
        out = lint(
            '.a\n{\n  color: red;\n}\n',
            only={'block-opening-brace-line'},
            options={'block-opening-brace-line': {'enabled': True}},
        )
        self.assertTrue(any(f.rule_id == 'block-opening-brace-line' for f in out))


class TestBraceOpeningLine(unittest.TestCase):
    """`block-opening-brace-line` — opt-in rule; `{` should join the
    selector on the same line."""

    @staticmethod
    def _lint(src: str, enabled: bool) -> list[Finding]:
        cfg = LinterConfig(
            enabled={'block-opening-brace-line'},
            rule_options={'block-opening-brace-line': {'enabled': enabled}},
        )
        return LessLinter(config=cfg).check(src)

    def test_disabled_by_default(self) -> None:
        # Rule is off unless explicitly enabled.
        cfg = LinterConfig(enabled={'block-opening-brace-line'})
        self.assertEqual(LessLinter(config=cfg).check('.a\n{\n  c: 1;\n}\n'), [])

    def test_flags_when_brace_on_new_line(self) -> None:
        out = self._lint('.a\n{\n  c: 1;\n}\n', enabled=True)
        self.assertEqual([f.rule_id for f in out], ['block-opening-brace-line'])

    def test_clean_same_line(self) -> None:
        # `{` follows selector on same line — clean.
        self.assertEqual(self._lint('.a { c: 1; }\n', enabled=True), [])

    def test_brace_at_file_start_skipped(self) -> None:
        # `{` is the first token — defensive idx==0 check.
        self.assertEqual(self._lint('{ c: 1; }\n', enabled=True), [])


class TestClosingBraceNewlineBefore(unittest.TestCase):
    def test_detects_multidecl_oneliner(self) -> None:
        src = '.a { color: red; padding: 0; }\n'
        out = lint(src, only={'closing-brace-newline-before'})
        self.assertEqual([f.rule_id for f in out], ['closing-brace-newline-before'])

    def test_single_decl_fires_by_default(self) -> None:
        # Default `threshold=1` forces `}` to its own line for every
        # non-empty block — the format command relies on this.
        out = lint('.a { color: red; }\n', only={'closing-brace-newline-before'})
        self.assertEqual([f.rule_id for f in out], ['closing-brace-newline-before'])

    def test_single_decl_with_threshold_2_is_fine(self) -> None:
        out = lint(
            '.a { color: red; }\n',
            only={'closing-brace-newline-before'},
            options={'closing-brace-newline-before': {'threshold': 2}},
        )
        self.assertEqual(out, [])


class TestBlockClosingBraceNewlineAfter(unittest.TestCase):
    """`block-closing-brace-newline-after` — insert a newline between
    `}` and the next sibling token.
    """

    def test_detects_inline_siblings(self) -> None:
        out = lint('.a {} .b {}\n', only={'block-closing-brace-newline-after'})
        self.assertEqual(
            [f.rule_id for f in out],
            ['block-closing-brace-newline-after'],
        )

    def test_fix_inserts_newline_at_top_level(self) -> None:
        # Top-level depth: insert a bare `\n` (no indent).
        self.assertEqual(
            fix('.a {} .b {}\n', only={'block-closing-brace-newline-after'}),
            '.a {}\n.b {}\n',
        )

    def test_fix_inserts_indented_newline_when_nested(self) -> None:
        # Nested depth: insert `\n` + width-spaces indent.
        self.assertIn(
            '.a {}\n  .b {}',
            fix('.parent { .a {} .b {} }\n', only={'block-closing-brace-newline-after'}),
        )

    def test_skipped_when_brace_at_eof(self) -> None:
        # `}` is the last char of source — no next sibling.
        self.assertEqual(
            lint('.a {}', only={'block-closing-brace-newline-after'}),
            [],
        )

    def test_skipped_when_followed_by_semicolon(self) -> None:
        # `};` — the `;` terminates the surrounding statement.
        self.assertEqual(
            lint('@x: { c: 1; }; .b {}\n', only={'block-closing-brace-newline-after'}),
            [],
        )

    def test_clean_when_already_newline(self) -> None:
        self.assertEqual(
            lint('.a {}\n.b {}\n', only={'block-closing-brace-newline-after'}),
            [],
        )


class TestDeclsBeforeRulesets(unittest.TestCase):
    """`decls-before-rulesets` — reorder decls to come before nested
    blocks, separated by a blank line.
    """

    @staticmethod
    def _fix(src: str) -> str:
        cfg = LinterConfig(enabled={'decls-before-rulesets'})
        return LessLinter(config=cfg).fix(src)

    def test_detects_block_before_decl(self) -> None:
        src = '.a {\n  .nested { c: 1; }\n  color: red;\n}\n'
        out = lint(src, only={'decls-before-rulesets'})
        self.assertEqual([f.rule_id for f in out], ['decls-before-rulesets'])

    def test_fix_reorders_decls_first(self) -> None:
        src = '.a {\n  .nested { c: 1; }\n  color: red;\n}\n'
        fixed = self._fix(src)
        self.assertIn('color: red;', fixed)
        # Decl comes before the nested block.
        self.assertLess(fixed.index('color: red'), fixed.index('.nested'))

    def test_detects_missing_blank_line(self) -> None:
        # Order is right but the blank line between groups is missing.
        src = '.a {\n  color: red;\n  .nested { c: 1; }\n}\n'
        out = lint(src, only={'decls-before-rulesets'})
        self.assertEqual([f.rule_id for f in out], ['decls-before-rulesets'])

    def test_clean_already_ordered_with_blank_line(self) -> None:
        # Decl-before-block with the canonical blank separator: no finding.
        src = '.a {\n  color: red;\n\n  .nested {\n    c: 1;\n  }\n}\n'
        out = lint(src, only={'decls-before-rulesets'})
        self.assertEqual(out, [])

    def test_bare_amp_selector_skipped(self) -> None:
        # `& { ... }` merges into the parent at emit; rule refuses to
        # reorder around it.
        src = '.a {\n  & { c: 1; }\n  color: red;\n}\n'
        self.assertEqual(lint(src, only={'decls-before-rulesets'}), [])

    def test_extend_in_child_selector_skipped(self) -> None:
        # `:extend()` lives in the Selector's extend_list, not in the
        # body — re-emitting would lose it.
        src = '.a {\n  .x:extend(.b) { c: 1; }\n  color: red;\n}\n'
        self.assertEqual(lint(src, only={'decls-before-rulesets'}), [])

    def test_mixin_call_in_body_skipped(self) -> None:
        # MixinCall in body makes the children "ineligible" — the rule
        # bails out so it doesn't accidentally drop the call.
        src = '.a {\n  .mixcall();\n  .nested { c: 1; }\n  color: red;\n}\n'
        self.assertEqual(lint(src, only={'decls-before-rulesets'}), [])

    def test_fix_with_ineligible_nested_block_kept_verbatim(self) -> None:
        # Inner block contains a MixinCall (ineligible to recurse) —
        # the formatter copies it verbatim from source.
        src = '.a {\n  .inner { .mixcall(); color: 1; }\n  color: red;\n}\n'
        fixed = self._fix(src)
        # The inner block survives verbatim with its `.mixcall();`.
        self.assertIn('.mixcall();', fixed)

    def test_fix_groups_comments_with_following_node(self) -> None:
        src = '.a {\n  /* nested */\n  .inner { c: 1; }\n  /* decl */\n  color: red;\n}\n'
        fixed = self._fix(src)
        # `/* decl */` rides with `color: red;` to the decl group;
        # `/* nested */` rides with `.inner` to the block group.
        self.assertLess(fixed.index('/* decl */'), fixed.index('color: red'))
        self.assertLess(fixed.index('/* nested */'), fixed.index('.inner'))

    def test_fix_preserves_trailing_comments(self) -> None:
        src = '.a {\n  color: red;\n  .inner { c: 1; }\n  /* trailing */\n}\n'
        fixed = self._fix(src)
        # Trailing comments end up at the bottom, after a blank line.
        self.assertIn('/* trailing */', fixed)
        self.assertLess(fixed.index('.inner'), fixed.index('/* trailing */'))


if __name__ == '__main__':
    unittest.main()
