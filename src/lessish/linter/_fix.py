"""Apply a batch of Findings' fixes to a source string.

Strategy: sort fixes by start position (descending) and splice into the
text from the end backwards so earlier indices stay stable. Overlapping
fixes are dropped (the later fix wins, the earlier is reported as a
conflict and re-emitted on the next pass).
"""

from __future__ import annotations

from collections.abc import Iterable

from ._findings import Finding


def apply_fixes(source: str, findings: Iterable[Finding], *, safe_only: bool = True) -> tuple[str, int]:
    """Return `(new_source, applied_count)`. Only findings with a
    `fix` attribute matching the safety filter are applied.
    """
    eligible: list[Finding] = []
    for f in findings:
        if f.fix is None:
            continue
        if safe_only and f.fix.safety != 'safe':
            continue
        eligible.append(f)
    eligible.sort(key=lambda f: f.span[0], reverse=True)

    text = source
    applied = 0
    last_start: int | None = None
    for f in eligible:
        start, end = f.span
        if last_start is not None and end > last_start:
            # Overlap with a later (already-applied) fix; skip.
            continue
        assert f.fix is not None
        text = text[:start] + f.fix.replacement + text[end:]
        applied += 1
        last_start = start
    return text, applied
