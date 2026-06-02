from __future__ import annotations

import io
import json
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


class TestLintCommand(unittest.TestCase):
    def test_lint_clean_file_returns_0(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'clean.less'
            p.write_text('.a {\n  color: red;\n}\n', encoding='utf-8')
            rc, stdout, _ = _capture(['lint', str(p)])
            self.assertEqual(rc, 0)
            self.assertEqual(stdout, '')

    def test_lint_dirty_file_returns_1(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            p.write_text('.a { color: #FFFFFF; }\n', encoding='utf-8')
            rc, stdout, _ = _capture(['lint', str(p)])
            self.assertEqual(rc, 1)
            self.assertIn('hex-short', stdout)

    def test_lint_fix_rewrites_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            p.write_text('.a { color: #FFFFFF; }\n', encoding='utf-8')
            _capture(['lint', '--fix', str(p)])
            # `--fix` applies safe (Tier 0/1) fixes: hex-short + the
            # canonical multi-line block layout.
            self.assertEqual(
                p.read_text(encoding='utf-8'),
                '.a {\n  color: #fff;\n}\n',
            )

    def test_lint_json_format(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            p.write_text('.a { color: #FFFFFF; }\n', encoding='utf-8')
            rc, stdout, _ = _capture(['lint', '--format', 'json', str(p)])
            self.assertEqual(rc, 1)
            data = json.loads(stdout)
            self.assertTrue(any(item['rule'] == 'hex-short' for item in data))

    def test_lint_github_format(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            p.write_text('.a { color: #FFFFFF; }\n', encoding='utf-8')
            rc, stdout, _ = _capture(['lint', '--format', 'github', str(p)])
            self.assertEqual(rc, 1)
            self.assertIn('::warning ', stdout)
            self.assertIn('hex-short', stdout)

    def test_lint_disable_filter(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            # Multi-line layout to avoid triggering format rules.
            p.write_text('.a {\n  color: #FFFFFF;\n}\n', encoding='utf-8')
            rc, stdout, _ = _capture(['lint', '--disable', 'hex-short,hex-case', str(p)])
            self.assertEqual(rc, 0, msg=stdout)

    def test_lint_enable_filter(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            # Trailing whitespace AND uppercase hex; --enable should
            # filter to just the latter.
            p.write_text('.a { color: #FFFFFF; }  \n', encoding='utf-8')
            rc, stdout, _ = _capture(['lint', '--enable', 'hex-case', str(p)])
            self.assertEqual(rc, 1)
            self.assertIn('hex-case', stdout)
            self.assertNotIn('trailing-whitespace', stdout)

    def test_lint_missing_file_returns_3(self) -> None:
        rc, _, stderr = _capture(['lint', '/no/such/file.less'])
        self.assertEqual(rc, 3)
        self.assertIn('lessish: error:', stderr)

    def test_telemetry_out(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            p.write_text('.a { color: #FFFFFF; }\n', encoding='utf-8')
            out_path = Path(d) / 'tel.json'
            rc, _, _ = _capture(['lint', '--telemetry-out', str(out_path), str(p)])
            self.assertEqual(rc, 1)
            self.assertTrue(out_path.is_file())
            data = json.loads(out_path.read_text(encoding='utf-8'))
            self.assertEqual(data['schemaVersion'], 1)
            self.assertEqual(data['totalFiles'], 1)
            rule_ids = [r['rule'] for r in data['rules']]
            self.assertIn('hex-short', rule_ids)
            self.assertIn('hex-case', rule_ids)

    def test_lint_no_inline_config_flag(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'dirty.less'
            # Multi-line layout so the format rules don't fire and we
            # exercise the inline-directive logic in isolation.
            p.write_text(
                '/* lessish-disable hex-case */\n.a {\n  color: #FFAB00;\n}\n',
                encoding='utf-8',
            )
            # Without --no-inline-config: directive suppresses hex-case.
            rc, stdout, _ = _capture(['lint', str(p)])
            self.assertEqual(rc, 0, msg=stdout)
            # With --no-inline-config: directive is ignored, hex-case fires.
            rc, stdout, _ = _capture(['lint', '--no-inline-config', str(p)])
            self.assertEqual(rc, 1)
            self.assertIn('hex-case', stdout)


if __name__ == '__main__':
    unittest.main()
