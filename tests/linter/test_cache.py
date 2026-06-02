"""Lint cache behaviour."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lessish.linter import Finding, LintCache, LinterConfig


def _make_finding(rule_id: str = 'x', line: int = 1) -> Finding:
    from lessish.errors import SourceLocation

    return Finding(
        rule_id=rule_id,
        severity='warning',
        message='hi',
        location=SourceLocation(filename='<f>', line=line, column=1, index=0),
        span=(0, 1),
        fix=None,
    )


class TestCache(unittest.TestCase):
    def test_put_then_get(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = LintCache(cache_dir=Path(d))
            key = cache.key('.a { color: red; }', LinterConfig())
            findings = [_make_finding('rule-a'), _make_finding('rule-b', line=3)]
            cache.put(key, findings)
            loaded = cache.get(key)
            assert loaded is not None
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].rule_id, 'rule-a')
            self.assertEqual(loaded[1].location.line, 3)

    def test_miss_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = LintCache(cache_dir=Path(d))
            self.assertIsNone(cache.get('nonexistent'))

    def test_different_content_different_keys(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = LintCache(cache_dir=Path(d))
            k1 = cache.key('a', LinterConfig())
            k2 = cache.key('b', LinterConfig())
            self.assertNotEqual(k1, k2)

    def test_different_config_different_keys(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = LintCache(cache_dir=Path(d))
            cfg_a = LinterConfig()
            cfg_b = LinterConfig(disabled={'hex-short'})
            k1 = cache.key('source', cfg_a)
            k2 = cache.key('source', cfg_b)
            self.assertNotEqual(k1, k2)


if __name__ == '__main__':
    unittest.main()
