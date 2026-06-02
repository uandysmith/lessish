"""Small, dependency-free string utilities.

Leaf module — imports nothing from the rest of the package, so any
module can use it without risking an import cycle. Keep it that way:
only string-in / value-out helpers with no AST or context knowledge
belong here.
"""

from __future__ import annotations


def strip_quotes(s: str) -> str:
    """Strip one matching pair of surrounding `"` or `'` quotes from `s`.

    Returns `s` unchanged when it isn't a quoted literal (too short,
    mismatched, or unquoted). Does NOT trim surrounding whitespace —
    callers that need that (e.g. interpolation) strip first.
    """
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def has_block_comment(text: str) -> bool:
    """True if `text` contains a `/* ... */` block comment outside of
    string literals.

    Strings (`"..."`, `'...'`) are skipped — including escaped quotes —
    so a `/*` that lives inside a literal doesn't count. Used by both
    the importer's variable-substitution path and the declaration
    evaluator's raw-text fast-path (a preserved comment forces verbatim
    emit). Deliberately not clever about comment *significance*
    (less.js's `/*! ... */` "kept" distinction): every block comment
    counts, which matches the corpus expectations.
    """
    if '/*' not in text:
        return False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < len(text):
                if text[j] == '\\' and j + 1 < len(text):
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            i = j
            continue
        if ch == '/' and i + 1 < len(text) and text[i + 1] == '*':
            return True
        i += 1
    return False
