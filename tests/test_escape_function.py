"""`escape()` behaviour + an import-cost regression guard.

`escape()` mirrors less.js: `encodeURI` (which percent-encodes
non-ASCII as UTF-8 bytes and leaves the unreserved/reserved ASCII set
alone) plus a secondary pass that encodes `= : # ; ( )`. The `safe`
argument handed to `urllib.parse.quote` only needs the ASCII chars —
`quote` drops any non-ASCII from `safe` internally — so the module
must NOT rebuild a million-codepoint passthrough string at import (it
did once, costing ~220ms of import time for zero behavioural effect).
"""

from __future__ import annotations

import re
import unittest

from lessish import Lessish
from lessish.functions import string as _string_mod


def _escape(value: str) -> str:
    out = Lessish().compile(f'.a{{x:escape("{value}")}}')
    m = re.search(r'x: (.*);', out)
    assert m is not None
    return m.group(1)


class TestEscapeFunction(unittest.TestCase):
    def test_secondary_replacements_encoded(self) -> None:
        # The six chars less.js re-encodes after encodeURI.
        self.assertEqual(_escape('a=b'), 'a%3Db')
        self.assertEqual(_escape('a:b'), 'a%3Ab')
        self.assertEqual(_escape('a#b'), 'a%23b')
        self.assertEqual(_escape('a;b'), 'a%3Bb')
        self.assertEqual(_escape('a(b)'), 'a%28b%29')

    def test_reserved_and_unreserved_pass_through(self) -> None:
        self.assertEqual(_escape('a/b?c@d&e+f$g,h'), 'a/b?c@d&e+f$g,h')
        self.assertEqual(_escape('A-Z_a.z~9'), 'A-Z_a.z~9')

    def test_space_and_percent(self) -> None:
        self.assertEqual(_escape('hello world'), 'hello%20world')
        self.assertEqual(_escape('100%'), '100%25')

    def test_non_ascii_percent_encoded_utf8(self) -> None:
        # café -> UTF-8 bytes of é (0xC3 0xA9) percent-encoded; matches
        # encodeURI. (The old megastring intended passthrough, but quote
        # dropped it — current behaviour is the less.js-correct one.)
        self.assertEqual(_escape('café'), 'caf%C3%A9')
        self.assertEqual(_escape('日本'), '%E6%97%A5%E6%9C%AC')

    def test_safe_set_is_ascii_only_and_small(self) -> None:
        # Regression guard against re-materialising the ~1.1M-codepoint
        # passthrough string at import time.
        safe = _string_mod._ESCAPE_SAFE_CHARS
        self.assertLess(len(safe), 100)
        self.assertTrue(safe.isascii())


if __name__ == '__main__':
    unittest.main()
