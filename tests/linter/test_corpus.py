"""Corpus-level invariants for the linter.

Three claims, verified on real Less fixtures:

1. **No crashes** — the linter survives every fixture in the
   conformance corpus (parse errors are surfaced as findings, not
   exceptions).
2. **Fix idempotency** — `fix(fix(source)) == fix(source)` for every
   sampled file.
3. **Tier-0 compile invariant** — `compile(tier0_fix(source)) ==
   compile(source)` for every file the compiler accepts. A Tier-0
   "formatting" fix must never change the emitted CSS.

The full real-world corpus (~800 files) is gated behind the env var
`LESSISH_CORPUS_FULL`; CI defaults to the 133-file `tests-unit` set
plus a 50-file slice of real-world, which runs in seconds.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from lessish import Lessish
from lessish.linter import LessLinter, LinterConfig
from lessish.linter.rules import TIER_0_RULES

_REPO_ROOT = Path(__file__).resolve().parents[2].parent
_CONFORMANCE = _REPO_ROOT / 'less-conformance' / 'data'
_UNIT = _CONFORMANCE / 'test-data' / 'tests-unit'
_REAL_WORLD = _CONFORMANCE / 'data' / 'real-world'
if not _REAL_WORLD.is_dir():
    _REAL_WORLD = _CONFORMANCE / 'real-world'


def _unit_files() -> list[Path]:
    if not _UNIT.is_dir():
        return []
    return sorted(_UNIT.rglob('*.less'))[:200]


def _real_world_files() -> list[Path]:
    if not _REAL_WORLD.is_dir():
        return []
    files = sorted(_REAL_WORLD.rglob('*.less'))
    if os.environ.get('LESSISH_CORPUS_FULL'):
        return files
    return files[:50]


@unittest.skipUnless(_UNIT.is_dir(), 'conformance corpus not available')
class TestCorpusNoCrash(unittest.TestCase):
    def test_unit_corpus_does_not_crash(self) -> None:
        linter = LessLinter()
        for f in _unit_files():
            try:
                text = f.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            try:
                linter.check(text, filename=str(f))
            except Exception as e:  # noqa: BLE001
                self.fail(f'linter crashed on {f}: {type(e).__name__}: {e}')

    def test_real_world_corpus_does_not_crash(self) -> None:
        linter = LessLinter()
        for f in _real_world_files():
            try:
                text = f.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            try:
                linter.check(text, filename=str(f))
            except Exception as e:  # noqa: BLE001
                self.fail(f'linter crashed on {f}: {type(e).__name__}: {e}')


@unittest.skipUnless(_UNIT.is_dir(), 'conformance corpus not available')
class TestFixIdempotency(unittest.TestCase):
    def test_safe_fix_is_idempotent(self) -> None:
        linter = LessLinter()
        for f in _unit_files():
            try:
                text = f.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            try:
                once = linter.fix(text, filename=str(f))
                twice = linter.fix(once, filename=str(f))
            except Exception:  # noqa: BLE001
                continue
            self.assertEqual(once, twice, msg=f'fix not idempotent for {f}')


@unittest.skipUnless(_UNIT.is_dir(), 'conformance corpus not available')
class TestTier0CompileInvariant(unittest.TestCase):
    """`compile(tier0_fix(s)) == compile(s)` — every Tier-0 fix preserves
    the compiled CSS. Tier 1+ fixes legitimately change output, so we
    restrict the linter to Tier 0 here.
    """

    def test_tier0_fix_does_not_change_compile_output(self) -> None:
        tier0_ids = {cls.id for cls in TIER_0_RULES}
        linter = LessLinter(config=LinterConfig(enabled=tier0_ids))
        ls = Lessish()

        checked = 0
        for f in _unit_files():
            try:
                text = f.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            # Skip files the compiler rejects — they're not "valid Less"
            # samples for this invariant.
            try:
                orig_css = ls.compile(text, filename=str(f))
            except Exception:  # noqa: BLE001
                continue
            try:
                fixed = linter.fix(text, filename=str(f))
            except Exception:  # noqa: BLE001
                continue
            if fixed == text:
                continue
            try:
                fixed_css = ls.compile(fixed, filename=str(f))
            except Exception as e:  # noqa: BLE001
                self.fail(f'tier-0 fix broke compile for {f}: {e}')
            self.assertEqual(
                fixed_css,
                orig_css,
                msg=f'tier-0 fix changed CSS output for {f}',
            )
            checked += 1

        # Sanity: we actually exercised the invariant on some files.
        self.assertGreater(checked, 0, 'no fixture exercised the invariant')


if __name__ == '__main__':
    unittest.main()
