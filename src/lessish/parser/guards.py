"""Guard-position entry point.

`parse_guard_text` wraps the block-level `Parser` (which carries the
guard-grammar methods `_parse_guard_root` and friends) so the evaluator
can re-parse guard substrings (the text after `when`) without needing
to know the Parser internals.
"""

from __future__ import annotations

from ..ast_nodes import Condition
from ..errors import ParseError
from ..source import Source
from .blocks import Parser
from .helpers import _shift_indices


def parse_guard_text(text: str, *, base_offset: int = 0) -> Condition:
    """Parse a guard expression (the part after `when`) into a Condition.

    The leading `when` keyword should already have been stripped. The
    text *may* include the surrounding parens (e.g. `(@a > 5)`) — the
    parser absorbs them; both forms produce the same Condition.

    `base_offset` shifts every Node's `index` so error reporting picks
    up the right column inside the original source rather than the
    1-based start of the guard substring.
    """
    src = Source(text=text, filename='<guard>')
    parser = Parser(src)
    try:
        root = parser._parse_guard_root()
    except ParseError as pe:
        # Structural rejections raised during guard parsing carry a
        # synthetic-source `_base_index`; shift it into outer source.
        if base_offset and pe._base_index is not None:
            pe._base_index = pe._base_index + base_offset
        raise
    if base_offset:
        _shift_indices(root, base_offset)
    return root
