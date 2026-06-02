"""Recursive-descent Less parser split across themed sub-modules.

  * `blocks`  — the main `Parser` class (block-level + value-level +
                guard methods all live on the same class for shared
                lexer/token-stream state)
  * `values`  — `parse_value_text` and `_build_tilde_paren_list`
                (value-position entry points around `Parser`)
  * `guards`  — `parse_guard_text` (guard-position entry point)
  * `helpers` — small text/token utilities shared between the above
  * `entry`   — top-level `parse(source)` glue

Symbols re-exported below are the package's public surface; prefer
importing from the themed sub-module directly.
"""

from __future__ import annotations

from .blocks import Parser
from .entry import parse
from .guards import parse_guard_text
from .helpers import (
    _parse_extend_args,
    _retokenize_inline,
    _shift_indices,
    _split_top_level_commas,
    _strip_block_comments,
)
from .values import _build_tilde_paren_list, parse_value_text

__all__ = [
    'Parser',
    '_build_tilde_paren_list',
    '_parse_extend_args',
    '_retokenize_inline',
    '_shift_indices',
    '_split_top_level_commas',
    '_strip_block_comments',
    'parse',
    'parse_guard_text',
    'parse_value_text',
]
