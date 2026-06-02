"""Pathologically nested input must fail as a `LessError`, never as a
raw `RecursionError`.

The public contract (README "Errors") is that everything thrown out of
the pipeline inherits from `LessError`. A `RecursionError` is a
`RuntimeError`, outside that hierarchy — so deeply nested source (a
denial-of-service-shaped input for the "compile LLM-produced Less" use
case) must surface through the parser's `MAX_PARSE_DEPTH` guard or the
public-API `RecursionError` backstop, both of which raise `LessError`
subclasses.
"""

from __future__ import annotations

import unittest

from lessish import Lessish
from lessish.errors import LessError
from lessish.parser import parse_value_text
from lessish.parser._state import MAX_PARSE_DEPTH


class TestRecursionLimits(unittest.TestCase):
    def setUp(self) -> None:
        self.ls = Lessish()

    def _assert_lesserror(self, source: str) -> None:
        # The key guarantee: a `LessError` (or nothing), but never a raw
        # `RecursionError` / other `RuntimeError`.
        try:
            self.ls.compile(source)
        except LessError:
            pass
        except RecursionError as e:  # pragma: no cover - regression guard
            self.fail(f'raw RecursionError escaped: {e!r}')

    def test_deeply_nested_blocks(self) -> None:
        src = '.a{' * 400 + 'color:red' + '}' * 400
        with self.assertRaises(LessError):
            self.ls.compile(src)

    def test_deeply_nested_blocks_is_parse_error(self) -> None:
        from lessish.errors import ParseError

        src = '.a{' * 400 + 'color:red' + '}' * 400
        with self.assertRaises(ParseError):
            self.ls.compile(src)

    def test_deeply_nested_selectors(self) -> None:
        # Eval-time recursion path (selector combinator chain), covered
        # by the public-API backstop rather than the parser counter.
        self._assert_lesserror('&' + ' &' * 1500 + '{x:1}')

    def test_deeply_nested_value_parens_does_not_crash(self) -> None:
        # Declaration values are captured raw and only re-parsed lazily;
        # the eval path tolerates an unparseable deep value by falling
        # back to verbatim text. The contract under test is just "no
        # raw RecursionError".
        self._assert_lesserror('.a{width:' + '(' * 800 + '1' + ')' * 800 + '}')

    def test_value_parser_raises_parse_error_on_deep_parens(self) -> None:
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            parse_value_text('(' * (MAX_PARSE_DEPTH + 50) + '1' + ')' * (MAX_PARSE_DEPTH + 50))

    def test_modest_nesting_still_compiles(self) -> None:
        # Well below the limit — must compile cleanly, proving the guard
        # doesn't clip legitimate input.
        depth = 30
        src = '.a{' * depth + 'color:red' + '}' * depth
        out = self.ls.compile(src)
        self.assertIn('color: red;', out)

    def test_modest_value_parens_compile(self) -> None:
        out = self.ls.compile('.a{width:' + '(' * 20 + '1px' + ')' * 20 + '}')
        self.assertIn('width:', out)

    def test_limit_is_sane(self) -> None:
        # Guard against an accidental edit pushing the cap up near the
        # interpreter's stack budget (which would reintroduce crashes).
        self.assertLess(MAX_PARSE_DEPTH, 400)
        self.assertGreater(MAX_PARSE_DEPTH, 50)


if __name__ == '__main__':
    unittest.main()
