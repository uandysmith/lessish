"""Unknown compile-option names are rejected loudly.

A misspelled keyword (`compres` → `compress`) raises `TypeError`
with a did-you-mean hint instead of silently dropping the option.
`strict_imports` stays accepted as a no-op (matches less.js).
"""

from __future__ import annotations

import unittest
import warnings

from lessish import Lessish
from lessish.errors import LessishSecurityWarning


class UnknownOptionTests(unittest.TestCase):
    def test_constructor_typo_rejected_with_hint(self) -> None:
        with self.assertRaises(TypeError) as cm:
            Lessish(compres=True)
        msg = str(cm.exception)
        self.assertIn('Lessish()', msg)
        self.assertIn("'compres'", msg)
        self.assertIn("'compress'", msg)  # did-you-mean

    def test_per_call_typo_rejected_with_hint(self) -> None:
        with self.assertRaises(TypeError) as cm:
            Lessish().compile('.a { b: c; }', kompress=True)
        msg = str(cm.exception)
        self.assertIn('compile()', msg)
        self.assertIn("'compress'", msg)

    def test_compile_with_source_map_also_validated(self) -> None:
        with self.assertRaises(TypeError):
            Lessish().compile_with_source_map('.a { b: c; }', kompress=True)

    def test_unknown_without_close_match_still_rejected(self) -> None:
        with self.assertRaises(TypeError) as cm:
            Lessish(zzzzzzzz=1)
        self.assertIn("'zzzzzzzz'", str(cm.exception))

    def test_multiple_unknowns_all_reported(self) -> None:
        with self.assertRaises(TypeError) as cm:
            Lessish().compile('.a { b: c; }', kompress=True, rewrit_urls='all')
        msg = str(cm.exception)
        self.assertIn("'kompress'", msg)
        self.assertIn("'rewrit_urls'", msg)

    def test_internal_kwargs_are_not_user_settable(self) -> None:
        # `src` / `want_source_map_result` are driver-internal — a caller
        # must not be able to set them as options.
        with self.assertRaises(TypeError):
            Lessish().compile('.a { b: c; }', src='x')

    def test_all_valid_options_accepted(self) -> None:
        # Every documented pipeline option must pass validation.
        ls = Lessish(
            filename='x.less',
            paths=(),
            process_imports=True,
            global_vars=None,
            modify_vars=None,
            banner='',
            compress=False,
            rewrite_urls='off',
            rootpath='',
            url_args='',
            strict_units=False,
            math='parens-division',
            source_map=None,
            file_io='jail',
            mixin_depth_limit=None,
            mixin_total_limit=None,
            interp_expansion_limit=1_000_000,
            replace_input_limit=100_000,
        )
        self.assertEqual(ls.compile('.a { b: c; }'), '.a {\n  b: c;\n}\n')

    def test_strict_imports_still_accepted(self) -> None:
        # Accepted-but-no-op; it emits its own deprecation warning but
        # must NOT raise as an unknown option.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            out = Lessish(strict_imports=True).compile('.a { b: c; }')
        self.assertIn('b: c', out)

    def test_valid_compile_unaffected(self) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=LessishSecurityWarning)
            out = Lessish(compress=True).compile('.a { b: c; }', compress=False)
        self.assertEqual(out, '.a {\n  b: c;\n}\n')


if __name__ == '__main__':
    unittest.main()
