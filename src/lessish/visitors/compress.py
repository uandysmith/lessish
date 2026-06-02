"""Compress-mode text post-processors for at-rule preludes,
selectors, and declaration values.

These three are pure string transforms — they don't touch the AST.
Kept in their own module so the emitter / at-rule path can import
just the bits it needs without dragging in the full visitor surface.
"""

from __future__ import annotations


def _compress_atrule_prelude(text: str) -> str:
    """Collapse `, ` → `,` at the top level (outside `()` and string
    literals) of an at-rule prelude. Used in compress mode for
    `@media screen, print` → `@media screen,print` and friends.
    Quoted strings and parenthesised expressions are skipped so
    `@supports (foo: bar)` and quoted media-type literals round-trip.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    depth = 0
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
            continue
        if ch == ')':
            depth = max(0, depth - 1)
            out.append(ch)
            i += 1
            continue
        if depth == 0 and ch == ',':
            out.append(',')
            i += 1
            while i < n and text[i] in (' ', '\t'):
                i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _compress_selector(sel: str) -> str:
    """Strip surrounding spaces from CSS combinators in a selector
    string for compress-mode emit. Combinators handled: `>` `+` `~`
    `^` `^^` (less.js's shadow-piercing forms). The descendant
    combinator stays as a single space — browsers require it.

    Depth-tracking: only the spaces *between top-level elements*
    (depth 0) are stripped — spaces inside `[attr~="x"]` or
    `:not(.a + .b)` come from the user's element text and must round-
    trip verbatim.
    """
    out: list[str] = []
    i = 0
    n = len(sel)
    bracket = 0
    paren = 0
    while i < n:
        ch = sel[i]
        if ch == '[':
            bracket += 1
            out.append(ch)
            i += 1
            continue
        if ch == ']':
            bracket = max(0, bracket - 1)
            out.append(ch)
            i += 1
            continue
        if ch == '(':
            paren += 1
            out.append(ch)
            i += 1
            continue
        if ch == ')':
            paren = max(0, paren - 1)
            out.append(ch)
            i += 1
            continue
        if bracket > 0 or paren > 0:
            out.append(ch)
            i += 1
            continue
        # Top-level: collapse ` OP ` → `OP` for known combinators.
        # `^^` (two chars) must be checked before single-char `^`.
        if ch == ' ' and i + 2 < n and sel[i + 1] == '^' and sel[i + 2] == '^':
            # ` ^^ ` → `^^`
            j = i + 3
            while j < n and sel[j] == ' ':
                j += 1
            out.append('^^')
            i = j
            continue
        if ch == ' ' and i + 1 < n and sel[i + 1] in '>+~^|':
            op = sel[i + 1]
            j = i + 2
            while j < n and sel[j] == ' ':
                j += 1
            out.append(op)
            i = j
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _compress_value(text: str, compress: bool) -> str:
    """Apply compress-mode tweaks to a declaration value's text:

    * drop spaces after `,` inside function-call args (`rgba(255, 0, 0)` → `rgba(255,0,0)`)
    * drop the leading `0` from sub-1 decimals OUTSIDE function-call parens
      (`0.5px` → `.5px`, but `rgba(0,0,0,0.1)` keeps `0.1` — matches less.js)

    Done as string post-processing so the value-serializer stays simple.
    """
    if not compress:
        return text
    # First: collapse `, ` → `,` everywhere (safe in CSS function args).
    text = text.replace(', ', ',')
    # Now drop leading `0` only when paren depth is 0.
    out: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '(':
            depth += 1
            out.append(ch)
            i += 1
            continue
        if ch == ')':
            depth = max(0, depth - 1)
            out.append(ch)
            i += 1
            continue
        # Detect `0.\d` at paren depth 0 not preceded by digit/letter/dot.
        if depth == 0 and ch == '0' and i + 1 < n and text[i + 1] == '.' and i + 2 < n and text[i + 2].isdigit():
            prev = out[-1] if out else ''
            if not (prev.isdigit() or prev.isalpha() or prev == '.'):
                # Skip the `0`.
                i += 1
                continue
        out.append(ch)
        i += 1
    return ''.join(out)
