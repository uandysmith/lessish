from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

from .errors import SourceLocation


@dataclass
class Source:
    text: str
    filename: str = '<input>'
    _line_starts: list[int] | None = field(default=None, init=False, repr=False, compare=False)

    def location_at(self, index: int) -> SourceLocation:
        starts = self._ensure_line_starts()
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= index:
                lo = mid
            else:
                hi = mid - 1
        return SourceLocation(
            filename=self.filename,
            line=lo + 1,
            column=index - starts[lo] + 1,
            index=index,
        )

    def snippet_around(self, index: int, context: int = 1) -> str:
        starts = self._ensure_line_starts()
        loc = self.location_at(index)
        first = max(0, loc.line - 1 - context)
        last = min(len(starts) - 1, loc.line - 1 + context)
        lines: list[str] = []
        for i in range(first, last + 1):
            line_start = starts[i]
            line_end = starts[i + 1] - 1 if i + 1 < len(starts) else len(self.text)
            line_text = self.text[line_start:line_end].rstrip('\n')
            prefix = f'{i + 1:4d} | '
            lines.append(prefix + line_text)
            if i == loc.line - 1:
                lines.append(' ' * len(prefix) + ' ' * (loc.column - 1) + '^')
        return '\n'.join(lines)

    def _ensure_line_starts(self) -> list[int]:
        if self._line_starts is None:
            starts = [0]
            for i, ch in enumerate(self.text):
                if ch == '\n':
                    starts.append(i + 1)
            self._line_starts = starts
        return self._line_starts

    @classmethod
    def from_path(cls, path: str) -> Self:
        from pathlib import Path

        p = Path(path)
        return cls(text=p.read_text(encoding='utf-8'), filename=str(p))
