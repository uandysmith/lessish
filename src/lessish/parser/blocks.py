"""Final `Parser` class composition.

The recursive-descent parser is split into themed mixins (see the
sibling `_*.py` files in this package + `_state.py` for the shared
state / cross-mixin signature protocol). This module composes them
into the public `Parser` class.

Each method has exactly one home, so MRO order is informational. The
order below mirrors typical caller-to-callee depth so a debugger
walking `Parser.parse` sees the source-order it'd expect.
"""

from __future__ import annotations

from ..lexer import TokenStream, tokenize
from ..source import Source
from ._block_level import _BlockLevelMethods
from ._calls import _CallsMethods
from ._declarations import _DeclarationsMethods
from ._guards import _GuardsMethods
from ._selectors import _SelectorsMethods
from ._state import _ParserState
from ._statements import _StatementsMethods
from ._utils import _UtilsMethods
from ._value_expr import _ValueExprMethods
from ._value_primary import _ValuePrimaryMethods


class Parser(
    _BlockLevelMethods,
    _SelectorsMethods,
    _DeclarationsMethods,
    _StatementsMethods,
    _ValuePrimaryMethods,
    _ValueExprMethods,
    _CallsMethods,
    _GuardsMethods,
    _UtilsMethods,
    _ParserState,
):
    """Recursive-descent parser for Less.

    Produces an AST that captures both the structural skeleton
    (selectors, blocks, declarations, at-rules) and value-level
    expressions (Dimensions, Colors, Calls, Operations, Negatives,
    Parens, Quoted, Url, …). The block-level dispatch keeps
    declaration values as raw `Anonymous` text; the value-position
    parser (see `_value_expr.py` + `_value_primary.py`) re-tokenises
    that text into the structured tree the evaluator consumes
    (entry point: `parse_value_text` in `values.py`).
    """

    def __init__(self, source: Source | TokenStream) -> None:
        if isinstance(source, TokenStream):
            # Pre-tokenised entry: the stream already carries its
            # source. Reset the cursor so a stream that was passed
            # through `Lessish.tokenize` (or otherwise positioned)
            # restarts from token 0.
            self.source = source.source
            self.stream = source
            self.stream.pos = 0
        else:
            self.source = source
            self.stream = tokenize(source)
        # Dedupe block-comment harvest: `_harvest_block_comments` may
        # be invoked at several positions (root primary, ruleset
        # primary, after-RBRACE trailing), and the same trivia attaches
        # to the token that follows the comment. Track by source index.
        self._seen_comment_indices = set()
        # Recursive-descent nesting counter (see `_descend`).
        self._depth = 0
