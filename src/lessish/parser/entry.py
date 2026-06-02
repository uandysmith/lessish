"""Top-level `parse(source)` entry point.

Lives in its own module so importing the public `parse` function does
not have to drag in the value/guard entry-point modules. The `Parser`
class itself is in `blocks.py`.
"""

from __future__ import annotations

from ..ast_nodes import Ruleset
from ..lexer import TokenStream
from ..source import Source
from .blocks import Parser


def parse(source: Source | TokenStream) -> Ruleset:
    """Parse a `Source` or pre-built `TokenStream` into a root `Ruleset`.

    Passing a `TokenStream` skips the lex pass — useful when the same
    input has already been tokenised (e.g. via `Lessish.tokenize`) and
    the caller wants to reuse those tokens without paying the regex
    cost again. The stream's cursor is reset to 0 on entry.
    """
    return Parser(source).parse()
