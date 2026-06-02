"""Tier 0 rules — whitespace, indent, and per-character normalisation.

These rules tighten the spacing around tokens (`color:red` →
`color: red`), the indentation of statement-starting lines,
and similar visual cleanups that never affect compiled CSS
(verified by the `compile(fix(s)) == compile(s)` invariant).
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


class TestTrailingWhitespace(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('a {  \n  color: red;\n}\n', only={'trailing-whitespace'})
        self.assertEqual([f.rule_id for f in out], ['trailing-whitespace'])

    def test_fix_strips(self) -> None:
        self.assertEqual(
            fix('a {  \n  color: red;\n}\n', only={'trailing-whitespace'}),
            'a {\n  color: red;\n}\n',
        )

    def test_clean_source_no_finding(self) -> None:
        self.assertEqual(lint('a {\n  color: red;\n}\n', only={'trailing-whitespace'}), [])


class TestFinalNewline(unittest.TestCase):
    def test_missing_newline(self) -> None:
        out = lint('a { color: red; }', only={'final-newline'})
        self.assertEqual([f.rule_id for f in out], ['final-newline'])

    def test_fix_appends(self) -> None:
        self.assertEqual(
            fix('a { color: red; }', only={'final-newline'}),
            'a { color: red; }\n',
        )

    def test_multiple_newlines_collapse(self) -> None:
        self.assertEqual(
            fix('a {}\n\n\n', only={'final-newline'}),
            'a {}\n',
        )

    def test_clean_no_finding(self) -> None:
        self.assertEqual(lint('a {}\n', only={'final-newline'}), [])


class TestNoTabs(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('a {\n\tcolor: red;\n}\n', only={'no-tabs'})
        self.assertTrue(any(f.rule_id == 'no-tabs' for f in out))

    def test_fix_replaces_with_spaces(self) -> None:
        self.assertEqual(
            fix('a {\n\tcolor: red;\n}\n', only={'no-tabs'}),
            'a {\n  color: red;\n}\n',
        )

    def test_no_finding_for_inline_tabs(self) -> None:
        # Only flag tabs in indentation.
        self.assertEqual(lint('a { color:\tred; }\n', only={'no-tabs'}), [])


class TestNoMultipleBlankLines(unittest.TestCase):
    def test_collapses(self) -> None:
        self.assertEqual(
            fix('a {}\n\n\n\nb {}\n', only={'no-multiple-blank-lines'}),
            'a {}\n\n\nb {}\n',
        )


class TestSpaceAfterColon(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('a { color:red; }\n', only={'space-after-colon'})
        self.assertEqual([f.rule_id for f in out], ['space-after-colon'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('a { color:red; }\n', only={'space-after-colon'}),
            'a { color: red; }\n',
        )

    def test_pseudo_class_not_flagged(self) -> None:
        # `a:hover` has a colon between two IDENTs but it's a pseudo-class,
        # not a declaration — the rule should ignore it.
        out = lint('a:hover { color: red; }\n', only={'space-after-colon'})
        self.assertEqual(out, [])


class TestSpaceBeforeLBrace(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('a{ color: red; }\n', only={'space-before-lbrace'})
        self.assertEqual([f.rule_id for f in out], ['space-before-lbrace'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('a{ color: red; }\n', only={'space-before-lbrace'}),
            'a { color: red; }\n',
        )


if __name__ == '__main__':
    unittest.main()


class TestIndentWidth(unittest.TestCase):
    def test_detects_wrong_indent(self) -> None:
        # 4 spaces inside a block, expected 2.
        out = lint('.a {\n    color: red;\n}\n', only={'indent-width'})
        self.assertTrue(any(f.rule_id == 'indent-width' for f in out))

    def test_correct_indent_is_clean(self) -> None:
        out = lint('.a {\n  color: red;\n}\n', only={'indent-width'})
        self.assertEqual(out, [])

    def test_fix_normalises(self) -> None:
        self.assertEqual(
            fix('.a {\n    color: red;\n}\n', only={'indent-width'}),
            '.a {\n  color: red;\n}\n',
        )

    def test_configurable_width(self) -> None:
        # With width=4 the original is OK.
        self.assertEqual(
            lint('.a {\n    color: red;\n}\n', only={'indent-width'}, options={'indent-width': {'width': 4}}),
            [],
        )


class TestIndentConsistency(unittest.TestCase):
    def test_detects_mixed(self) -> None:
        src = '.a {\n  color: red;\n\tpadding: 0;\n}\n'
        out = lint(src, only={'indent-consistency'})
        self.assertTrue(any(f.rule_id == 'indent-consistency' for f in out))

    def test_all_spaces_clean(self) -> None:
        out = lint('.a {\n  color: red;\n}\n', only={'indent-consistency'})
        self.assertEqual(out, [])


class TestNoSpaceAfterLBrace(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a {  color: red; }\n', only={'no-space-after-lbrace'})
        self.assertEqual([f.rule_id for f in out], ['no-space-after-lbrace'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a {  color: red; }\n', only={'no-space-after-lbrace'}),
            '.a { color: red; }\n',
        )

    def test_newline_after_lbrace_ok(self) -> None:
        self.assertEqual(
            lint('.a {\n  color: red;\n}\n', only={'no-space-after-lbrace'}),
            [],
        )


class TestNoSpaceBeforeRBrace(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('.a { color: red;  }\n', only={'no-space-before-rbrace'})
        self.assertEqual([f.rule_id for f in out], ['no-space-before-rbrace'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a { color: red;  }\n', only={'no-space-before-rbrace'}),
            '.a { color: red; }\n',
        )


class TestNoSpaceAroundAttrEq(unittest.TestCase):
    def test_detects(self) -> None:
        out = lint('[type = "x"] { }\n', only={'no-space-around-attr-eq'})
        # Two findings: space before AND space after `=`.
        ids = [f.rule_id for f in out]
        self.assertEqual(ids.count('no-space-around-attr-eq'), 2)

    def test_fix(self) -> None:
        self.assertEqual(
            fix('[type = "x"] { }\n', only={'no-space-around-attr-eq'}),
            '[type="x"] { }\n',
        )


class TestSpaceAroundCombinator(unittest.TestCase):
    def test_detects_gt(self) -> None:
        out = lint('.a>.b { color: red; }\n', only={'space-around-combinator'})
        self.assertEqual([f.rule_id for f in out], ['space-around-combinator'])

    def test_fix(self) -> None:
        self.assertEqual(
            fix('.a>.b { color: red; }\n', only={'space-around-combinator'}),
            '.a > .b { color: red; }\n',
        )

    def test_spaced_clean(self) -> None:
        self.assertEqual(
            lint('.a > .b { color: red; }\n', only={'space-around-combinator'}),
            [],
        )

    def test_plus_in_value_not_flagged(self) -> None:
        out = lint('.a { width: (1px+2px); }\n', only={'space-around-combinator'})
        self.assertEqual(out, [])


class TestSpaceAroundBinaryOp(unittest.TestCase):
    def test_off_by_default(self) -> None:
        out = lint('.a { width: (1px+2px); }\n', only={'space-around-binary-op'})
        self.assertEqual(out, [])

    def test_detects_when_enabled(self) -> None:
        out = lint(
            '.a { width: (1px+2px); }\n',
            only={'space-around-binary-op'},
            options={'space-around-binary-op': {'enabled': True}},
        )
        self.assertTrue(any(f.rule_id == 'space-around-binary-op' for f in out))


if __name__ == '__main__':
    unittest.main()


class TestSpaceAroundBinaryOpExtra(unittest.TestCase):
    """Off-by-default rule; exercises configured-on edge cases."""

    @staticmethod
    def _lint(src: str, options: dict[str, object] | None = None) -> list[Finding]:
        cfg = LinterConfig(
            enabled={'space-around-binary-op'},
            rule_options={'space-around-binary-op': options or {}},
        )
        return LessLinter(config=cfg).check(src)

    def test_disabled_by_default(self) -> None:
        cfg = LinterConfig(enabled={'space-around-binary-op'})
        self.assertEqual(LessLinter(config=cfg).check('.a { c: 1+2; }'), [])

    def test_flags_no_spaces(self) -> None:
        out = self._lint('.a { c: 1+2; }', {'enabled': True})
        self.assertEqual([f.rule_id for f in out], ['space-around-binary-op'])

    def test_clean_when_both_sides_spaced(self) -> None:
        # `1 + 2` — both sides already have spaces → rule short-circuits.
        self.assertEqual(self._lint('.a { c: 1 + 2; }', {'enabled': True}), [])

    def test_skip_after_lparen(self) -> None:
        # `(-1px)` — unary minus, not a binary op.
        self.assertEqual(self._lint('.a { c: (-1px); }', {'enabled': True}), [])

    def test_skip_after_comma(self) -> None:
        # `rgb(1,-1,2)` — comma + unary minus.
        self.assertEqual(self._lint('.a { c: rgb(1,-1,2); }', {'enabled': True}), [])

    def test_skip_after_colon(self) -> None:
        # `c:-1px` — colon + unary minus.
        self.assertEqual(self._lint('.a { c:-1px; }', {'enabled': True}), [])


class TestQuotePref(unittest.TestCase):
    def test_off_by_default(self) -> None:
        # No options → rule disabled internally.
        self.assertEqual(
            lint('.a { content: "x"; }\n', only={'quote-pref'}),
            [],
        )

    def test_swap_to_single(self) -> None:
        self.assertEqual(
            fix(
                '.a { content: "hello"; }\n',
                only={'quote-pref'},
                options={'quote-pref': {'enabled': True, 'prefer': 'single'}},
            ),
            ".a { content: 'hello'; }\n",
        )

    def test_skip_when_inner_has_target_quote(self) -> None:
        # Source uses double quotes and inner string has a single quote.
        self.assertEqual(
            lint(
                '.a { content: "it\\\'s"; }\n',
                only={'quote-pref'},
                options={'quote-pref': {'enabled': True, 'prefer': 'single'}},
            ),
            [],
        )


if __name__ == '__main__':
    unittest.main()
