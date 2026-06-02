"""Linter framework / engine infrastructure tests.

Not rule-specific: covers the `FileBundle` + `LintContext`
lazy-parse / `--full` machinery, the `Rule` base class's
default hook behaviour, and the rule-by-id lookup registry.
"""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING, Any

from lessish.linter import Finding, FixOptions, LessLinter, LinterConfig

if TYPE_CHECKING:
    from lessish.linter.rules._base import FileBundle


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


class TestFullModeMachinery(unittest.TestCase):
    def test_full_mode_does_not_break_engine(self) -> None:
        # Sanity: --full doesn't blow up on simple inputs.
        out = lint('.a {\n  color: red;\n}\n', full=True)
        self.assertEqual(out, [])


class TestLinterRulesBase(unittest.TestCase):
    """Direct unit tests for `linter.rules._base` — `FileBundle` /
    `LintContext` / `Rule` default-method machinery that no individual
    rule exercises end-to-end.
    """

    def _bundle(self, text: str, *, full_mode: bool = False) -> FileBundle:
        from lessish.lexer import tokenize
        from lessish.linter.rules._base import FileBundle
        from lessish.source import Source

        src = Source(text=text, filename='<test>')
        stream = tokenize(src)
        return FileBundle(source=src, tokens=stream.tokens, full_mode=full_mode)

    def test_ast_lazy_parse(self) -> None:
        b = self._bundle('.a { c: 1; }\n')
        # First call parses; second returns cached.
        a1 = b.ast()
        a2 = b.ast()
        self.assertIs(a1, a2)
        self.assertIsNotNone(a1)

    def test_evaluated_ast_skipped_when_not_full_mode(self) -> None:
        b = self._bundle('.a { c: 1; }\n', full_mode=False)
        self.assertIsNone(b.evaluated_ast())

    def test_evaluated_ast_runs_in_full_mode(self) -> None:
        b = self._bundle('.a { c: 1; }\n', full_mode=True)
        e1 = b.evaluated_ast()
        self.assertIsNotNone(e1)
        # Cached on second call.
        self.assertIs(b.evaluated_ast(), e1)

    def test_evaluated_ast_swallows_eval_errors(self) -> None:
        # `@a: @a;` raises during eval; the bundle records the error
        # but `evaluated_ast()` returns None rather than propagating.
        b = self._bundle('@a: @a;\n.x { c: @a; }\n', full_mode=True)
        self.assertIsNone(b.evaluated_ast())
        self.assertIsNotNone(b._evaluated_error)

    def test_evaluated_ast_skipped_when_parse_failed(self) -> None:
        # Parse failure → ast() returns None → evaluated_ast() bails.
        b = self._bundle('@import garbage "x";', full_mode=True)
        self.assertIsNone(b.evaluated_ast())

    def test_context_properties_proxy_bundle(self) -> None:
        from lessish.linter.rules._base import LintContext

        b = self._bundle('.a { c: 1; }\n', full_mode=True)
        ctx = LintContext(bundle=b, options={})
        self.assertIs(ctx.source, b.source)
        self.assertIs(ctx.tokens, b.tokens)
        self.assertEqual(ctx.text, '.a { c: 1; }\n')
        self.assertTrue(ctx.full_mode)
        self.assertIsNotNone(ctx.evaluated_ast())

    def test_rule_default_token_and_node_hooks_return_empty(self) -> None:
        from lessish.linter.rules._base import LintContext, Rule

        b = self._bundle('.a { c: 1; }\n')
        ctx = LintContext(bundle=b, options={})
        rule = Rule()
        self.assertEqual(list(rule.on_token(b.tokens[0], 0, ctx, None)), [])
        # The base hook signature requires a `Node`; the default impl
        # returns [] regardless of input and we pass None to exercise it.
        self.assertEqual(list(rule.on_node(None, ctx, None)), [])  # type: ignore[arg-type]
        self.assertEqual(list(rule.on_file_end(ctx, None)), [])
        self.assertEqual(list(rule.check(ctx)), [])


class TestRuleById(unittest.TestCase):
    def test_lookup_known_rule(self) -> None:
        from typing import cast as _cast

        from lessish.linter.rules import rule_by_id
        from lessish.linter.rules._base import Rule as _Rule

        cls = rule_by_id('trailing-whitespace')
        self.assertIsNotNone(cls)
        self.assertEqual(_cast(type[_Rule], cls).id, 'trailing-whitespace')

    def test_lookup_unknown_returns_none(self) -> None:
        from lessish.linter.rules import rule_by_id

        self.assertIsNone(rule_by_id('not-a-real-rule'))


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
