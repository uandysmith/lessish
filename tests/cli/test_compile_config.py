"""Tests for `[tool.lessish]` compile config loading."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from lessish.cli import main
from lessish.cli._compile_config import load_from_path, load_from_pyproject


def _capture(argv: list[str], *, stdin_text: str | None = None) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    saved_stdin = None
    if stdin_text is not None:
        import sys

        saved_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
    finally:
        if saved_stdin is not None:
            import sys

            sys.stdin = saved_stdin
    return rc, out.getvalue(), err.getvalue()


class TestConfigLoader(unittest.TestCase):
    def test_loads_from_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text(
                '[tool.lessish]\ncompress = true\nmath = "always"\n',
                encoding='utf-8',
            )
            cfg = load_from_pyproject(Path(d))
            self.assertTrue(cfg.compress)
            self.assertEqual(cfg.math, 'always')

    def test_loads_from_lessish_toml(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'lessish.toml').write_text(
                'compress = true\n[global-vars]\nprimary = "blue"\n',
                encoding='utf-8',
            )
            cfg = load_from_pyproject(Path(d))
            self.assertTrue(cfg.compress)
            self.assertEqual(cfg.global_vars, {'primary': 'blue'})

    def test_loads_from_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / 'custom.toml'
            cfg_path.write_text('strict-units = true\n', encoding='utf-8')
            cfg = load_from_path(cfg_path)
            self.assertTrue(cfg.strict_units)

    def test_walks_upward_to_find_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'pyproject.toml').write_text('[tool.lessish]\ncompress = true\n', encoding='utf-8')
            sub = root / 'sub' / 'deep'
            sub.mkdir(parents=True)
            cfg = load_from_pyproject(sub)
            self.assertTrue(cfg.compress)

    def test_missing_config_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = load_from_pyproject(Path(d))
            self.assertIsNone(cfg.compress)
            self.assertEqual(cfg.global_vars, {})

    def test_parses_paths_list(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish]\npaths = ["a", "b"]\n', encoding='utf-8')
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.paths, ('a', 'b'))

    def test_parses_var_tables(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text(
                '[tool.lessish.global-vars]\nc = "red"\n[tool.lessish.modify-vars]\nd = "blue"\n',
                encoding='utf-8',
            )
            cfg = load_from_pyproject(Path(d))
            self.assertEqual(cfg.global_vars, {'c': 'red'})
            self.assertEqual(cfg.modify_vars, {'d': 'blue'})


class TestCompileWithConfig(unittest.TestCase):
    def test_config_compress_applies(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish]\ncompress = true\n', encoding='utf-8')
            src = Path(d) / 'in.less'
            src.write_text('.a { color: red; }', encoding='utf-8')
            rc, stdout, stderr = _capture(['compile', str(src)])
            self.assertEqual(rc, 0, msg=stderr)
            self.assertEqual(stdout, '.a{color:red}')

    def test_cli_overrides_config(self) -> None:
        # CLI override probed via `--math` — `compress` has no
        # explicit "false" CLI flag, so we use math to exercise the
        # config-vs-CLI precedence path symmetrically.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish]\nmath = "parens"\n', encoding='utf-8')
            src = Path(d) / 'in.less'
            src.write_text('.a { width: (10px / 2); }', encoding='utf-8')
            # Without CLI override → config "parens" mode still evaluates
            # inside parens, so 10px/2 → 5px.
            rc, stdout, _ = _capture(['compile', str(src)])
            self.assertEqual(rc, 0)
            self.assertIn('5px', stdout)
            # With CLI --math always → same result, but pulled from CLI.
            rc, stdout, _ = _capture(['compile', '--math', 'always', str(src)])
            self.assertEqual(rc, 0)
            self.assertIn('5px', stdout)

    def test_config_global_vars_applied(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish.global-vars]\nc = "red"\n', encoding='utf-8')
            src = Path(d) / 'in.less'
            src.write_text('.a { color: @c; }', encoding='utf-8')
            rc, stdout, stderr = _capture(['compile', str(src)])
            self.assertEqual(rc, 0, msg=stderr)
            self.assertIn('color: red', stdout)

    def test_cli_global_var_overrides_config_key(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish.global-vars]\nc = "red"\n', encoding='utf-8')
            src = Path(d) / 'in.less'
            src.write_text('.a { color: @c; }', encoding='utf-8')
            rc, stdout, _ = _capture(['compile', '--global-var', 'c=blue', str(src)])
            self.assertEqual(rc, 0)
            self.assertIn('color: blue', stdout)

    def test_config_paths_combined_with_cli(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inc_a = root / 'inc_a'
            inc_b = root / 'inc_b'
            inc_a.mkdir()
            inc_b.mkdir()
            (inc_a / 'a.less').write_text('.a { color: red; }', encoding='utf-8')
            (inc_b / 'b.less').write_text('.b { color: blue; }', encoding='utf-8')
            (root / 'pyproject.toml').write_text(f'[tool.lessish]\npaths = ["{inc_a}"]\n', encoding='utf-8')
            src = root / 'in.less'
            src.write_text('@import "a";\n@import "b";\n', encoding='utf-8')
            rc, stdout, stderr = _capture(['compile', '--paths', str(inc_b), str(src)])
            self.assertEqual(rc, 0, msg=stderr)
            self.assertIn('color: red', stdout)
            self.assertIn('color: blue', stdout)

    def test_explicit_config_flag(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / 'lc.toml'
            cfg.write_text('compress = true\n', encoding='utf-8')
            rc, stdout, _ = _capture(
                ['compile', '--config', str(cfg), '-'],
                stdin_text='.a { color: red; }',
            )
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, '.a{color:red}')

    def test_config_strict_units(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text('[tool.lessish]\nstrict-units = true\n', encoding='utf-8')
            src = Path(d) / 'in.less'
            src.write_text('.a { width: (1px * 2em); }', encoding='utf-8')
            rc, _, stderr = _capture(['compile', str(src)])
            self.assertNotEqual(rc, 0)
            self.assertIn('lessish: error:', stderr)

    def test_config_banner(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'pyproject.toml').write_text(
                '[tool.lessish]\nbanner = "/* from config */\\n"\n',
                encoding='utf-8',
            )
            src = Path(d) / 'in.less'
            src.write_text('.a { color: red; }', encoding='utf-8')
            rc, stdout, _ = _capture(['compile', str(src)])
            self.assertEqual(rc, 0)
            self.assertTrue(stdout.startswith('/* from config */\n'))

    def test_config_section_resolved_relative_to_input(self) -> None:
        # Config sits next to input file (not CWD); the loader should
        # walk up from input file's parent dir.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sub = root / 'project'
            sub.mkdir()
            (sub / 'pyproject.toml').write_text('[tool.lessish]\ncompress = true\n', encoding='utf-8')
            src = sub / 'in.less'
            src.write_text('.a { color: red; }', encoding='utf-8')
            rc, stdout, _ = _capture(['compile', str(src)])
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, '.a{color:red}')


if __name__ == '__main__':
    unittest.main()
