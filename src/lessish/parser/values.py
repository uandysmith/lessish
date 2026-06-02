"""Value-position entry points.

The block-level parser keeps declaration values as raw `Anonymous` text;
`parse_value_text` re-tokenises that text into the structured Value tree
the evaluator consumes (Dimension, Color, Operation, Call, …).

Both entry points delegate to dedicated methods on the block-level
`Parser` (`_parse_value_root`, `_parse_guard_root`) — those methods stay
on the Parser class for access to its lexer/token-stream state, but the
public wrappers live here so callers don't reach into Parser internals.

`_build_tilde_paren_list` is the value-level helper used by the Parser's
`_parse_primary` for the `~( … )` lookup syntax. It calls back into
`parse_value_text`, so it lives here next to its dependency.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from ..ast_nodes import Expression, Node, Paren, Value
from ..errors import ParseError
from ..lexer import Token, TokenStream, tokenize
from ..source import Source
from .blocks import Parser
from .helpers import _shift_indices

# LRU cache of `text → tuple[Token, ...]` so repeated value strings
# (`red`, `10px`, `0`, `inherit`, `none`, …) skip the lexer's master
# regex on subsequent calls. Tokens are `frozen=True` dataclasses with
# `slots=True` — sharing them across parses is safe; the TokenStream
# wrapper carries the per-call mutable cursor.
#
# The Value tree that comes out of the parser is NOT cached: nodes
# get mutated downstream (`_lookup_important`, `_captured_frames`,
# `_evaled_node`, etc.), so reusing them across declarations would
# leak state. Caching just the tokens skips the regex-heavy lex pass
# (~40% of `parse_value_text` cost) without that hazard.
#
# Sized for real-world inputs: Bootstrap re-parses ~5-6k value strings
# of which ~80% repeat across declarations; 1024 entries covers the
# working set comfortably without bloating memory (each entry is
# typically <50 tokens × ~80 bytes = ~4KB).
_TOKEN_CACHE_MAX = 1024

# The cache is **per-thread**, not a process global. `_cached_tokenize`
# does a non-atomic get → move_to_end → setitem → popitem sequence on an
# OrderedDict; sharing one dict across threads would corrupt it (and
# break the documented guarantee that two threads compiling concurrently
# never race). `threading.local` gives each thread its own OrderedDict,
# so the sequence is single-threaded by construction. Cache contents are
# a pure function of the value text (tokenization ignores compile
# options), so per-thread isolation costs nothing semantically — each
# thread just warms its own copy. Tokens themselves are `frozen`,
# `slots=True` dataclasses, safe to hold across calls within a thread.
_local = threading.local()


def _thread_cache() -> OrderedDict[str, tuple[Token, ...]]:
    cache: OrderedDict[str, tuple[Token, ...]] | None = getattr(_local, 'token_cache', None)
    if cache is None:
        cache = OrderedDict()
        _local.token_cache = cache
    return cache


def _cached_tokenize(text: str) -> tuple[Token, ...]:
    """Return the cached token tuple for `text` or tokenize fresh.
    Promotes the entry to MRU on every hit (manual LRU since the
    builtin `functools.lru_cache` doesn't expose the underlying
    dict for the diagnostics tests want). The backing dict is
    thread-local — see `_local`.
    """
    cache = _thread_cache()
    cached = cache.get(text)
    if cached is not None:
        cache.move_to_end(text)
        return cached
    tokens = tuple(tokenize(Source(text=text, filename='<value>')).tokens)
    cache[text] = tokens
    if len(cache) > _TOKEN_CACHE_MAX:
        cache.popitem(last=False)
    return tokens


def parse_value_text(text: str, *, base_offset: int = 0) -> Value:
    """Tokenize a value-position string and parse it into a structured Value.

    This is the entry point the evaluator uses to upgrade a declaration's
    raw `Anonymous` text into typed nodes (Dimension, Color, Operation,
    Call, ...). Unrecognized tokens become Anonymous within the tree so
    the result stays serializable verbatim if needed.

    `base_offset` shifts every node's `index` by that amount so the
    resulting AST locations are anchored in the *outer* source (the
    one passed to `compile()`). The evaluator uses this so error
    messages report the column of the offending token in the user's
    file, not column-1 of an opaque `<value>` synthetic source.

    Tokens are LRU-cached across calls (see `_token_cache`) so a
    Bootstrap-shaped compile that re-parses `10px` / `red` / `0`
    thousands of times pays the lexer cost once per unique string.
    """
    src = Source(text=text, filename='<value>')
    tokens = _cached_tokenize(text)
    # Fresh TokenStream per call: cursor (`pos`) is mutated by the
    # parser, but `tokens` itself is read-only — sharing the tuple
    # across calls is safe. `list(tokens)` because TokenStream
    # internally accepts `list[Token]` (slicing semantics rely on
    # list); the Parser only reads, so the copy is one-time.
    stream = TokenStream(list(tokens), src)
    parser = Parser(stream)
    try:
        root = parser._parse_value_root()
    except ParseError as pe:
        # Structural rejections raised during value-text parsing carry
        # `_base_index` in the synthetic `<value>` source. Shift it
        # into the outer source so error reporting picks the right
        # line/column.
        if base_offset and pe._base_index is not None:
            pe._base_index = pe._base_index + base_offset
        raise
    if base_offset:
        _shift_indices(root, base_offset)
    return root


def _build_tilde_paren_list(inner: str, base_index: int) -> Node:
    """Build a value AST node for `~(<inner>)`. Uses `;` as the outer
    separator when present (each piece becomes one comma-list item);
    otherwise delegates to the regular value parser, which already
    handles `,` and space lists. Returns a Paren so it composes
    correctly in larger expressions; the emit-time `value_to_css(Paren)`
    branch strips the wrapping for `~()` (these aren't real parens).
    """
    from ..mixins import _has_top_level_separator, _split_on

    if _has_top_level_separator(inner, ';'):
        pieces = _split_on(inner, ';')
        expressions: list[Expression] = []
        for raw in pieces:
            piece = raw.strip()
            if not piece:
                continue
            v = parse_value_text(piece, base_offset=base_index)
            # Unwrap single-expression Values; wrap multi-expression
            # ones so the nested comma-list is preserved.
            if len(v.expressions) == 1:
                expressions.append(v.expressions[0])
            else:
                expressions.append(Expression(index=base_index, values=[v]))
        result = Value(index=base_index, expressions=expressions)
    else:
        result = parse_value_text(inner, base_offset=base_index)
    paren = Paren(index=base_index, value=result)
    paren._tilde_list = True
    return paren
