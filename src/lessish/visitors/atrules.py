"""Frozen sets + helpers for at-rule classification and prelude
normalisation.

Shared by `extends.py` (media-scope tracking), `structure.py` (bubble /
merge passes), and `emit.py` (block/inline routing). Centralising the
sets here avoids duplication and keeps the "what counts as a media-
like at-rule" knowledge in one place.
"""

from __future__ import annotations

import re as _re

from ..ast_nodes import AtRule

# At-rules that bubble OUT of a surrounding Ruleset. less.js's set
# includes @media, @supports, @container, @document.
_BUBBLE_AT_RULES: frozenset[str] = frozenset({'@media', '@supports', '@container', '@document'})


# At-rules whose same-name nested children merge their preludes
# (`@media (a) { @media (b) {…} }` → `@media (a) and (b) {…}`).
# `@supports` / `@document` do NOT merge — less.js leaves them nested.
_MERGE_AT_RULES: frozenset[str] = frozenset({'@media', '@container'})


# At-rules that, when used inside a Ruleset body, should remain inside
# that body in the output (rather than emerging as a top-level rule).
# `@media`, `@supports`, `@layer`, `@container` etc. are *not* in this
# set — they bubble out per CSS nesting/cascade rules.
_INLINE_ATRULES: frozenset[str] = frozenset(
    {
        '@apply',  # Tailwind / postcss-style mixin
        '@charset',
        '@font-face',
        '@page',
        '@viewport',
        '@counter-style',
        '@namespace',
    }
)


_MEDIA_LIKE_AT_RULES: frozenset[str] = frozenset({'@media', '@supports', '@container'})


_NESTED_BLOCK_ATRULES: frozenset[str] = frozenset(
    # Block-form at-rules that stay INSIDE their surrounding ruleset
    # instead of bubbling out to top level. less.js: `@starting-style`
    # nests by CSS spec; `@container`/`@layer` (block-form) are also
    # kept inline so the surrounding selector context is preserved.
    {'@starting-style'}
)


# `screen and(max-width:1280px)` → `screen and (max-width: 1280px)`.
# Matches `and|or|not|only` followed *directly* by `(` (no space) and
# inserts the missing space.
_re_and_paren = _re.compile(r'\b(and|or|not|only)\(')


def _is_inline_atrule(at: AtRule) -> bool:
    """A statement-form `@xxx ...;` (body=None) inside a ruleset stays
    inside the ruleset when its name is in the inline set or when it
    has no body (i.e. it's a bare directive, not a block). Block-form
    at-rules in `_NESTED_BLOCK_ATRULES` also stay inline.
    """
    if at.body is not None:
        return at.name in _NESTED_BLOCK_ATRULES
    return at.name in _INLINE_ATRULES


def _normalize_media_prelude(text: str) -> str:
    """Normalise the prelude of `@media`, `@container`, `@supports` to
    match less.js's output. Operates inside top-level parens only and
    skips quoted strings:

    * Strip whitespace immediately after `(` and before `)`.
    * Add a space after `:` (`(min-width:600px)` → `(min-width: 600px)`).
    * Add spaces around `<` / `>` range comparisons (and `<=` / `>=`)
      so `(width<500px)` emits as `(width < 500px)` per CSS Containment.
    * Insert a space between `and`/`or`/`not`/`only` and an immediately-
      following `(` (`screen and(max-width:1280px)` → `screen and (...)`).
    """
    # Cheap pre-pass: separate combinator keywords from a following `(`.
    text = _re_and_paren.sub(r'\1 (', text)
    out: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            j = i + 1
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    j += 2
                    continue
                if text[j] == ch:
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if ch == '(':
            depth += 1
            out.append(ch)
            i += 1
            # Skip any whitespace after `(`.
            while i < n and text[i] in (' ', '\t', '\n'):
                i += 1
            continue
        if ch == ')':
            # Trim trailing whitespace before the close paren.
            while out and out[-1] in (' ', '\t', '\n'):
                out.pop()
            depth = max(0, depth - 1)
            out.append(ch)
            i += 1
            continue
        if depth > 0 and ch == ':' and i + 1 < n and text[i + 1] not in (' ', '\t', '\n', ':'):
            out.append(': ')
            i += 1
            continue
        if depth > 0 and ch in ('<', '>'):
            # Strip any whitespace already written before the operator.
            while out and out[-1] in (' ', '\t', '\n'):
                out.pop()
            out.append(' ')
            out.append(ch)
            # Honour `<=` / `>=` — keep the `=` glued.
            if i + 1 < n and text[i + 1] == '=':
                out.append('=')
                i += 2
            else:
                i += 1
            # Always emit exactly one trailing space after the operator.
            while i < n and text[i] in (' ', '\t', '\n'):
                i += 1
            out.append(' ')
            continue
        out.append(ch)
        i += 1
    return ''.join(out)
