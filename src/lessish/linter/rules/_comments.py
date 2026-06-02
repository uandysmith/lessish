"""Comment-preserving helpers for the line-breaking formatter rules.

`tokenize()` strips trivia, so the linter's token stream carries no
comment tokens. The line-breaking Tier-0 rules
(`block-opening-brace-newline-after`, `semicolon-newline-after`,
`closing-brace-newline-before`) reflow a single-line block by replacing
the *gap* between two adjacent non-trivia tokens with a newline + indent.
A gap between two consecutive non-trivia tokens contains nothing but
whitespace and comments, so any comment sitting on the brace/semicolon
line — including a `/* lessish-disable-line … */` directive — would be
discarded by a naive replacement.

`comments_in_gap` recovers those comments verbatim so each rule can
re-emit them on their own indented lines instead of dropping them. This
keeps `compile(format(s)) == compile(s)` (comments survive compilation)
and keeps inline lint directives attached to the code they govern.
"""

from __future__ import annotations

import re

# A trivia-only gap holds just whitespace and comments, so a plain scan
# is exact. Block comments may span lines (DOTALL); line comments run to
# the end of their line.
_COMMENT_RE = re.compile(r'/\*.*?\*/|//[^\n]*', re.DOTALL)


def comments_in_gap(gap: str) -> list[str]:
    """Return the comment substrings inside a trivia-only gap, in order.

    `gap` is the source text between two consecutive non-trivia tokens
    (e.g. between `{` and the first declaration). Returns `[]` when the
    gap is pure whitespace.
    """
    return _COMMENT_RE.findall(gap)
