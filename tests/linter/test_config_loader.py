"""Tests for `[tool.lessish.lint]` config loading and shared lessish.toml."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lessish.linter._config import load_from_path, load_from_pyproject


class TestLinterConfigLoader(unittest.TestCase):
    def test_loads_from_pyproject_lint_section(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish.lint]\ndisabled = ["hex-short"]\n', encoding='utf-8')
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.disabled, {'hex-short'})

    def test_loads_severity_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text(
                '[tool.lessish.lint.severity]\nhex-short = "error"\n',
                encoding='utf-8',
            )
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.severity_overrides, {'hex-short': 'error'})

    def test_loads_rule_options(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text(
                '[tool.lessish.lint.rules.deep-nesting]\nmax-depth = 5\n',
                encoding='utf-8',
            )
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.rule_options.get('deep-nesting'), {'max-depth': 5})

    def test_loads_from_lessish_toml_lint_subtable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'lessish.toml').write_text(
                'compress = true\n[lint]\ndisabled = ["hex-case"]\n',
                encoding='utf-8',
            )
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.disabled, {'hex-case'})

    def test_no_config_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.disabled, set())
            self.assertIsNone(cfg.enabled)

    def test_explicit_path_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'pyproject.toml'
            p.write_text('[tool.lessish.lint]\ndisabled = ["x"]\n', encoding='utf-8')
            cfg = load_from_path(p)
            self.assertEqual(cfg.disabled, {'x'})

    def test_explicit_path_lessish_toml(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'lessish.toml'
            p.write_text('[lint]\ndisabled = ["x"]\n', encoding='utf-8')
            cfg = load_from_path(p)
            self.assertEqual(cfg.disabled, {'x'})

    def test_walks_upward(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'pyproject.toml').write_text('[tool.lessish.lint]\ndisabled = ["hex-short"]\n', encoding='utf-8')
            sub = root / 'a' / 'b'
            sub.mkdir(parents=True)
            cfg = load_from_pyproject(sub)
            self.assertEqual(cfg.disabled, {'hex-short'})


class TestSharedLessishToml(unittest.TestCase):
    """`lessish.toml` is read by both compile (top-level) and lint ([lint]).
    Verify they don't step on each other.
    """

    def test_compile_and_lint_coexist(self) -> None:
        from lessish.cli._compile_config import load_from_pyproject as load_compile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'lessish.toml').write_text(
                'compress = true\n[lint]\ndisabled = ["hex-short"]\n',
                encoding='utf-8',
            )
            compile_cfg = load_compile(Path(d))
            lint_cfg = load_from_pyproject(Path(d))
            self.assertTrue(compile_cfg.compress)
            self.assertEqual(lint_cfg.disabled, {'hex-short'})

    def test_pyproject_compile_and_lint_coexist(self) -> None:
        from lessish.cli._compile_config import load_from_pyproject as load_compile

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text(
                '[tool.lessish]\ncompress = true\n[tool.lessish.lint]\ndisabled = ["hex-short"]\n',
                encoding='utf-8',
            )
            compile_cfg = load_compile(Path(d))
            lint_cfg = load_from_pyproject(Path(d))
            self.assertTrue(compile_cfg.compress)
            self.assertEqual(lint_cfg.disabled, {'hex-short'})


if __name__ == '__main__':
    unittest.main()
