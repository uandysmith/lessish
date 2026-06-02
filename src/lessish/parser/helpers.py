"""Small text- and token-level helpers shared by the block-, value-,
and guard-position parsers.

These functions are intentionally Parser-agnostic: they operate on raw
strings or token lists and have no dependency on Parser state. They live
here so both `blocks.py` (the main Parser class) and `values.py` /
`guards.py` (the entry-point wrappers) can use them without forming a
circular import.
"""

from __future__ import annotations

from ..ast_nodes import Comment, Extend, Node
from ..lexer import Kind, Token, tokenize
from ..source import Source


def _strip_block_comments(text: str) -> tuple[str, list[Comment]]:
    """Remove every top-level `/* ... */` block comment from `text` and
    return (cleaned_text, comment_nodes). Strings (`"..."`, `'...'`,
    `~"..."`) are skipped over so embedded `/* */` inside literals is
    untouched.

    Whitespace adjacent to a removed comment collapses sensibly:
    * `a /* c */ b`  → `a b`   (single space — keep separation)
    * `a /* c */b`  → `a b`   (left-side ws preserved)
    * `a/* c */ b`  → `a b`   (right-side ws preserved)
    * `a/* c */b`   → `ab`    (no ws on either side — concat)
    * `screen /* c */,` → `screen,` (right-side has non-ws punct, drop left ws)
    """
    if '/*' not in text:
        return text, []
    out_chars: list[str] = []
    comments: list[Comment] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'") or (ch == '~' and i + 1 < n and text[i + 1] in ('"', "'")):
            start_q = i if ch in ('"', "'") else i + 1
            quote = text[start_q]
            j = start_q + 1
            out_chars.append(text[i:start_q])
            out_chars.append(text[start_q])
            while j < n:
                out_chars.append(text[j])
                if text[j] == '\\' and j + 1 < n:
                    out_chars.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            i = j
            continue
        if ch == '/' and i + 1 < n and text[i + 1] == '*':
            end = text.find('*/', i + 2)
            if end == -1:
                # Unterminated — leave the rest verbatim.
                out_chars.append(text[i:])
                break
            comments.append(Comment(index=0, text=text[i : end + 2], silent=False))
            # Determine adjacency: did we just emit whitespace?
            had_ws_left = bool(out_chars) and out_chars[-1] in (' ', '\t', '\n')
            j = end + 2
            had_ws_right = j < n and text[j] in (' ', '\t', '\n')
            # Strip left and right whitespace, then re-add a single
            # space only when whitespace existed on either side AND the
            # result would otherwise glue two non-whitespace tokens.
            while out_chars and out_chars[-1] in (' ', '\t', '\n'):
                out_chars.pop()
            while j < n and text[j] in (' ', '\t', '\n'):
                j += 1
            if (had_ws_left or had_ws_right) and out_chars and j < n:
                # Skip the synthetic separator when the right-side
                # neighbour is punctuation that's normally tight against
                # its left token (`,`, `;`, `)`).
                if text[j] not in (',', ';', ')'):
                    out_chars.append(' ')
            i = j
            continue
        out_chars.append(ch)
        i += 1
    return ''.join(out_chars), comments


def _retokenize_inline(text: str, base_index: int) -> list[Token]:
    """Re-tokenize a snippet of source text against the main lexer.
    Used by the value parser when it needs to split a token mid-text
    (e.g. carving `-1px` out of an IDENT that the lexer greedily ate
    along with a leading unit). Returned tokens have their `index`
    fields shifted by `base_index` so error messages still point at
    the original source location.
    """
    sub = Source(text=text, filename='<retok>')
    stream = tokenize(sub)
    out: list[Token] = []
    for t in stream.tokens:
        if t.kind is Kind.EOF:
            continue
        out.append(Token(kind=t.kind, text=t.text, index=base_index + t.index, leading_trivia=t.leading_trivia))
    return out


def _parse_extend_args(text: str, *, base_index: int) -> list[Extend]:
    """Parse the contents of `:extend(...)` into one Extend per target.

    Input examples:
      ".foo"                 → [Extend(target='.foo', option='')]
      ".foo all"             → [Extend(target='.foo', option='all')]
      ".foo, .bar all"       → [Extend(.foo, ''), Extend(.bar, 'all')]
      ".foo .bar all"        → [Extend(target='.foo .bar', option='all')]

    `all` is a trailing-keyword option per-target. The remainder of the
    target string (with `all` stripped) is what we'll match against
    rule paths.
    """
    text = text.strip()
    if not text:
        return []
    out: list[Extend] = []
    for raw in _split_top_level_commas(text):
        piece = raw.strip()
        if not piece:
            continue
        option = ''
        if piece.endswith(' all'):
            option = 'all'
            piece = piece[: -len(' all')].rstrip()
        elif piece == 'all':
            # Pathological — `:extend(all)` has no target. Skip.
            continue
        if not piece:
            continue
        out.append(Extend(index=base_index, target=piece, option=option))
    return out


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts


def _shift_indices(node: Node, delta: int) -> None:
    """Walk a value-level AST mutating every `index` by `delta`.

    Every `Node` subclass declares `index: int`, so the assignment is
    unconditional (no `try/except` — slots-backed AST nodes always
    have the attribute). The structural-child attr tuple is stamped
    on each AST class as `__lessish_child_attrs__` at module load
    (see `ast_nodes._stamp_child_attrs`); reading it is a single
    class-attribute lookup per node, no per-call dict probe and no
    8-attr getattr fallback.

    `rules` is in the attr set so a DetachedRuleset's body Declarations
    (parsed in the `<value>` source) get re-anchored too — otherwise
    their `index` points into the synthetic source and error reporting
    picks the wrong line/column.
    """
    seen: set[int] = set()

    def visit(n: Node) -> None:
        nid = id(n)
        if nid in seen:
            return
        seen.add(nid)
        n.index = n.index + delta
        for attr in type(n).__lessish_child_attrs__:
            child = getattr(n, attr)
            if isinstance(child, Node):
                visit(child)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, Node):
                        visit(item)

    visit(node)
