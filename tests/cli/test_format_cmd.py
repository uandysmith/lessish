from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from lessish.cli import main


def _capture(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestFormatCommand(unittest.TestCase):
    def test_format_rewrites_tier0_issues(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            p.write_text(
                '.a{color: red;   \n  color: red;}\n',
                encoding='utf-8',
            )
            rc, stdout, _ = _capture(['format', str(p)])
            self.assertEqual(rc, 0)
            self.assertIn('reformatted', stdout)
            # Tier-0 fixes only: space-before-lbrace, trailing-whitespace,
            # closing-brace-newline-before, etc.
            self.assertIn(' {', p.read_text(encoding='utf-8'))

    def test_format_skips_tier1_rules(self) -> None:
        # Hex-short is Tier 1 — format should NOT change `#FFFFFF`.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'in.less'
            p.write_text('.a { color: #FFFFFF; }\n', encoding='utf-8')
            rc, _, _ = _capture(['format', str(p)])
            self.assertEqual(rc, 0)
            self.assertIn('#FFFFFF', p.read_text(encoding='utf-8'))

    def test_format_clean_file_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'clean.less'
            p.write_text('.a {\n  color: red;\n}\n', encoding='utf-8')
            rc, stdout, _ = _capture(['format', str(p)])
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, '')

    def test_check_flag_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            original = '.a{color: red;}\n'
            p.write_text(original, encoding='utf-8')
            rc, stdout, _ = _capture(['format', '--check', str(p)])
            self.assertEqual(rc, 1)
            self.assertIn('would reformat', stdout)
            # --check must not touch the file.
            self.assertEqual(p.read_text(encoding='utf-8'), original)

    def test_check_flag_clean(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'clean.less'
            p.write_text('.a {\n  color: red;\n}\n', encoding='utf-8')
            rc, stdout, _ = _capture(['format', '--check', str(p)])
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, '')

    def test_missing_file_returns_3(self) -> None:
        rc, _, stderr = _capture(['format', '/no/such/path.less'])
        self.assertEqual(rc, 3)
        self.assertIn('lessish: error:', stderr)


if __name__ == '__main__':
    unittest.main()
