from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from lessish.cli import main


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


class TestCompileCommand(unittest.TestCase):
    def test_compile_file_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'in.less'
            src.write_text('.a { color: red; }', encoding='utf-8')
            rc, stdout, stderr = _capture(['compile', str(src)])
            self.assertEqual(rc, 0, msg=stderr)
            self.assertEqual(stdout, '.a {\n  color: red;\n}\n')

    def test_compile_stdin(self) -> None:
        rc, stdout, stderr = _capture(['compile', '-'], stdin_text='@c: red; .a { color: @c; }')
        self.assertEqual(rc, 0, msg=stderr)
        self.assertEqual(stdout, '.a {\n  color: red;\n}\n')

    def test_compile_compress(self) -> None:
        rc, stdout, _ = _capture(['compile', '--compress', '-'], stdin_text='.a { color: red; }')
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, '.a{color:red}')

    def test_compile_writes_out_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'in.less'
            dst = Path(d) / 'out.css'
            src.write_text('.a { color: red; }', encoding='utf-8')
            rc, stdout, _ = _capture(['compile', str(src), '--out', str(dst)])
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, '')
            self.assertEqual(dst.read_text(encoding='utf-8'), '.a {\n  color: red;\n}\n')

    def test_compile_global_var(self) -> None:
        rc, stdout, _ = _capture(
            ['compile', '--global-var', 'c=red', '-'],
            stdin_text='.a { color: @c; }',
        )
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, '.a {\n  color: red;\n}\n')

    def test_compile_modify_var_overrides(self) -> None:
        rc, stdout, _ = _capture(
            ['compile', '--modify-var', 'c=blue', '-'],
            stdin_text='@c: red; .a { color: @c; }',
        )
        self.assertEqual(rc, 0)
        self.assertEqual(stdout, '.a {\n  color: blue;\n}\n')

    def test_compile_banner(self) -> None:
        rc, stdout, _ = _capture(
            ['compile', '--banner', '/* hi */\n', '-'],
            stdin_text='.a { color: red; }',
        )
        self.assertEqual(rc, 0)
        self.assertTrue(stdout.startswith('/* hi */\n'))

    def test_compile_math_always(self) -> None:
        rc, stdout, _ = _capture(['compile', '--math', 'always', '-'], stdin_text='.a { width: 10px / 2; }')
        self.assertEqual(rc, 0)
        self.assertIn('width: 5px', stdout)

    def test_compile_source_map_emits_annotation(self) -> None:
        rc, stdout, _ = _capture(['compile', '--source-map', '-'], stdin_text='.a { color: red; }')
        self.assertEqual(rc, 0)
        self.assertIn('sourceMappingURL=', stdout)

    def test_compile_source_map_out_writes_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'in.less'
            dst = Path(d) / 'out.css'
            mp = Path(d) / 'out.css.map'
            src.write_text('.a { color: red; }', encoding='utf-8')
            rc, _, stderr = _capture(
                [
                    'compile',
                    str(src),
                    '--out',
                    str(dst),
                    '--source-map',
                    '--source-map-out',
                    str(mp),
                ]
            )
            self.assertEqual(rc, 0, msg=stderr)
            self.assertTrue(mp.exists())
            map_data = json.loads(mp.read_text(encoding='utf-8'))
            self.assertEqual(map_data.get('version'), 3)
            self.assertIn('sourceMappingURL=', dst.read_text(encoding='utf-8'))

    def test_compile_source_map_url_override(self) -> None:
        rc, stdout, _ = _capture(
            ['compile', '--source-map-url', 'custom.map', '-'],
            stdin_text='.a { color: red; }',
        )
        self.assertEqual(rc, 0)
        self.assertIn('sourceMappingURL=custom.map', stdout)

    def test_compile_strict_units_raises(self) -> None:
        rc, _, stderr = _capture(['compile', '--strict-units', '-'], stdin_text='.a { width: (1px * 2em); }')
        self.assertNotEqual(rc, 0)
        self.assertIn('lessish: error:', stderr)

    def test_compile_paths_for_import(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            inc = Path(d) / 'inc'
            inc.mkdir()
            (inc / 'lib.less').write_text('.a { color: red; }', encoding='utf-8')
            rc, stdout, stderr = _capture(
                ['compile', '--paths', str(inc), '-'],
                stdin_text='@import "lib";',
            )
            self.assertEqual(rc, 0, msg=stderr)
            self.assertEqual(stdout, '.a {\n  color: red;\n}\n')

    def test_compile_parse_error_returns_1(self) -> None:
        rc, _, stderr = _capture(['compile', '-'], stdin_text='.a { color: red')
        self.assertEqual(rc, 1)
        self.assertIn('ParseError', stderr)

    def test_compile_undefined_variable_returns_2(self) -> None:
        rc, _, stderr = _capture(['compile', '-'], stdin_text='.a { color: @undef; }')
        self.assertEqual(rc, 2)
        self.assertIn('NameError', stderr)

    def test_compile_missing_file_returns_3(self) -> None:
        rc, _, stderr = _capture(['compile', '/nonexistent/path/to/nothing.less'])
        self.assertEqual(rc, 3)
        self.assertIn('lessish: error:', stderr)

    def test_compile_unsupported_feature_returns_4(self) -> None:
        rc, _, stderr = _capture(['compile', '-'], stdin_text='@plugin "x";')
        self.assertEqual(rc, 4)

    def test_compile_does_not_emit_security_warning(self) -> None:
        # The CLI always reads the filesystem with `file_io='allow'` and
        # passes it explicitly, so the `LessishSecurityWarning` (which is
        # only meant to nudge embedders off the implicit default) must not
        # leak out of a plain `lessish compile` run.
        import warnings

        from lessish import Lessish, LessishSecurityWarning

        # The warning latches once per process; reset it so a warning
        # emitted earlier in the test session can't mask a regression here.
        Lessish._file_io_default_warning_emitted = False
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            rc, stdout, stderr = _capture(['compile', '-'], stdin_text='.a { color: red; }')
        security = [w for w in caught if issubclass(w.category, LessishSecurityWarning)]
        self.assertEqual(rc, 0, msg=stderr)
        self.assertEqual(stdout, '.a {\n  color: red;\n}\n')
        self.assertEqual(security, [], msg='CLI compile should not emit LessishSecurityWarning')


class TestVersionCommand(unittest.TestCase):
    def test_version_command(self) -> None:
        rc, stdout, _ = _capture(['version'])
        self.assertEqual(rc, 0)
        self.assertTrue(stdout.startswith('lessish '))

    def test_version_flag(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        rc = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                main(['--version'])
            except SystemExit as e:
                rc = int(e.code or 0)
        text = out.getvalue() + err.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('lessish', text)


class TestTopLevelHelp(unittest.TestCase):
    def test_no_args_prints_help(self) -> None:
        rc, stdout, _ = _capture([])
        self.assertEqual(rc, 0)
        self.assertIn('compile', stdout)
        self.assertIn('lint', stdout)


if __name__ == '__main__':
    unittest.main()
