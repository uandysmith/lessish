"""`@import` prelude parsing primitives.

The `_ImportSpec` dataclass captures the decomposed shape of an
`@import` prelude — qualifiers, path, media features, plus quoting
metadata so `(css)` pass-through can reconstruct the original spelling.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _ImportSpec:
    """Parsed `@import` prelude.

    `options` is a set of qualifiers from `(opt1, opt2)`. `path` is the
    raw path string (no quotes, no `url()` wrapper). `media` is the
    media-query suffix text (e.g. `screen and (min-width: 600px)`), or
    the empty string when no suffix was present.

    `is_url_form` distinguishes `url("x")` from `"x"` because less.js
    treats `url(/absolute/...)` specially — absolute URLs aren't
    resolved on disk; they pass through.
    """

    options: set[str] = field(default_factory=set)
    path: str = ''
    media: str = ''
    is_url_form: bool = False
    # Quote character around the path in the source, or '' when the
    # path was unquoted. Tracked so a CSS pass-through preserves the
    # original spelling (`url("test.css")` vs `url(test.css)`).
    path_quote: str = ''


def parse_import_prelude(text: str) -> _ImportSpec:
    """Decompose the text after `@import` into qualifiers, path, and
    media features. Lenient — malformed preludes return whatever was
    parseable and let the caller decide whether to error.
    """
    spec = _ImportSpec()
    text = text.strip()
    # 1. Options: `(opt1, opt2)` directly after `@import`.
    if text.startswith('('):
        depth = 0
        end = -1
        for i, ch in enumerate(text):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            inside = text[1:end]
            spec.options = {q.strip() for q in inside.split(',') if q.strip()}
            text = text[end + 1 :].lstrip()
    # 2. Path: either `url(...)`, `"..."` / `'...'`, or bare.
    if text.startswith('url('):
        spec.is_url_form = True
        depth = 1
        i = 4
        while i < len(text) and depth > 0:
            if text[i] == '(':
                depth += 1
            elif text[i] == ')':
                depth -= 1
            i += 1
        inner = text[4 : i - 1].strip()
        if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ('"', "'"):
            spec.path_quote = inner[0]
            spec.path = inner[1:-1]
        else:
            spec.path = inner
        text = text[i:].lstrip()
    elif text.startswith(('"', "'")):
        q = text[0]
        spec.path_quote = q
        end = text.find(q, 1)
        if end == -1:
            spec.path = text[1:]
            text = ''
        else:
            spec.path = text[1:end]
            text = text[end + 1 :].lstrip()
    else:
        # Bare path until whitespace.
        i = 0
        while i < len(text) and not text[i].isspace():
            i += 1
        spec.path = text[:i]
        text = text[i:].lstrip()
    # 3. Media features: anything left.
    spec.media = text.strip()
    return spec


def _is_malformed_import_prelude(prelude: str) -> bool:
    """`@import malformed "x.less"` and friends: extra identifier
    sitting between `@import` and the path. less.js parses this as a
    syntax error. We detect it by: trimming `(opts)`, checking the
    remainder doesn't start with `url(`, `"`, or `'`.
    """
    text = prelude.strip()
    if text.startswith('('):
        depth = 0
        for i, ch in enumerate(text):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    text = text[i + 1 :].lstrip()
                    break
    return bool(text) and not text.startswith(('url(', '"', "'"))
