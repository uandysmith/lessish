"""Mixin call-name → namespace-path splitting.

A namespace mixin call like ``#a.b.c.mixin()`` is split into a list of
path segments (``['#a', '.b', '.c', '.mixin']``); the matcher walks
into rulesets segment by segment.
"""

from __future__ import annotations

_SPLIT_NAMESPACE_PATH_CACHE: dict[str, list[str]] = {}


def _split_namespace_path(name: str) -> list[str]:
    """Split a mixin call name on `>`, descendant whitespace, and segment
    boundaries (`.` or `#` after the first segment). Examples:
      `#a > .b`      → ['#a', '.b']
      `.foo .bar`    → ['.foo', '.bar']
      `#ns.mixin`    → ['#ns', '.mixin']
      `#a.b.c`       → ['#a', '.b', '.c']
      `#DEF.colors`  → ['#DEF', '.colors']
    Components are stripped of surrounding whitespace.
    """
    cached = _SPLIT_NAMESPACE_PATH_CACHE.get(name)
    if cached is not None:
        # Return a copy because the caller may mutate the result list.
        return list(cached)
    parts: list[str] = []
    depth_paren = 0
    depth_bracket = 0
    current: list[str] = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == '(':
            depth_paren += 1
            current.append(ch)
        elif ch == ')':
            depth_paren = max(0, depth_paren - 1)
            current.append(ch)
        elif ch == '[':
            depth_bracket += 1
            current.append(ch)
        elif ch == ']':
            depth_bracket = max(0, depth_bracket - 1)
            current.append(ch)
        elif ch == '>' and depth_paren == 0 and depth_bracket == 0:
            piece = ''.join(current).strip()
            if piece:
                parts.append(piece)
            current = []
        elif ch == ' ' and depth_paren == 0 and depth_bracket == 0:
            piece = ''.join(current).strip()
            if piece:
                parts.append(piece)
            current = []
        elif ch in ('.', '#') and depth_paren == 0 and depth_bracket == 0 and current and current[0] in ('.', '#'):
            # New path segment after a complete `.name` / `#name` head:
            # `#ns.mixin` → split into `#ns` | `.mixin`. We require the
            # current piece to already be a class/id-led segment so we
            # don't accidentally split a leading identifier like `each`.
            piece = ''.join(current).strip()
            if piece:
                parts.append(piece)
            current = [ch]
        else:
            current.append(ch)
        i += 1
    piece = ''.join(current).strip()
    if piece:
        parts.append(piece)
    _SPLIT_NAMESPACE_PATH_CACHE[name] = parts
    return list(parts)
