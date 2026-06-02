"""Public dataclasses for the linter API: Finding, Fix, FixOptions."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import SourceLocation


@dataclass(frozen=True, slots=True)
class Fix:
    """A source-text replacement carried alongside a Finding.

    `safety` is `'safe'` (Tier 0–1) or `'risky'` (Tier 2). The fix
    engine applies safe-tier fixes when `--fix` is on; risky-tier
    fixes need `--unsafe-fix`.
    """

    replacement: str
    safety: str = 'safe'
    description: str = ''


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    severity: str
    message: str
    location: SourceLocation
    span: tuple[int, int]
    fix: Fix | None = None


@dataclass(frozen=True, slots=True)
class FixOptions:
    safe_only: bool = True
    # Higher than 3 lets the recursive reorder rule converge after the
    # line-breaking rules establish the multi-line layout.
    max_passes: int = 6
