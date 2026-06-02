"""Inline lint directives parsed from source comments.

Supported syntax (case-insensitive verb, rule IDs comma-separated; empty
rule list means "all rules"):

    /* lessish-disable */                    → block: force every rule OFF
    /* lessish-disable hex-short, hex-case */
    /* lessish-enable */                     → block: force every rule ON
    /* lessish-enable hex-short */
    /* lessish-disable-line */               → this line only
    /* lessish-disable-line hex-case */
    /* lessish-disable-next-line hex-case */ → the next source line only
    /* lessish-enable-line hex-short */
    /* lessish-enable-next-line hex-short */

`//` line comments work too.

Both verbs OVERRIDE the project config: `enable` force-on, `disable`
force-off. Inline state is the highest-precedence layer
(`inline > CLI > config > defaults`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..lexer import Kind, Token
from ..source import Source

_DIRECTIVE_RE = re.compile(
    r'lessish-(?P<verb>disable-next-line|disable-line|enable-next-line|enable-line|disable|enable)'
    r'(?:\s*:?\s*(?P<rules>[\w\-,\s\*]+))?',
    re.IGNORECASE,
)


@dataclass
class DirectiveMap:
    """Per-line inline overrides for every rule that has one.

    `overrides_by_line[line][rule_id]` is `True` (force-enable) or
    `False` (force-disable). `'*'` is the wildcard.
    """

    overrides_by_line: dict[int, dict[str, bool]] = field(default_factory=dict)
    touched_rules: set[str] = field(default_factory=set)

    def lookup(self, rule_id: str, line: int) -> bool | None:
        """Return the inline state at line L for rule R, or None if no
        inline directive covers this combination.
        """
        line_overrides = self.overrides_by_line.get(line)
        if line_overrides is None:
            return None
        if rule_id in line_overrides:
            return line_overrides[rule_id]
        if '*' in line_overrides:
            return line_overrides['*']
        return None

    def touches(self, rule_id: str) -> bool:
        """True if any inline directive in the file mentions this rule
        (by name or via `*`). The engine uses this to decide whether to
        run a rule even when config has it disabled — an inline
        `enable` might want findings from it.
        """
        return '*' in self.touched_rules or rule_id in self.touched_rules


def scan_directives(tokens: list[Token], source: Source) -> DirectiveMap:
    """Build the directive map by walking comment tokens."""
    events_by_line: dict[int, list[tuple[str, set[str]]]] = {}
    touched: set[str] = set()

    for tok in _iter_comments(tokens):
        text = _strip_comment(tok.text)
        m = _DIRECTIVE_RE.search(text)
        if m is None:
            continue
        verb = m.group('verb').lower()
        rules = _parse_rule_list(m.group('rules'))
        line = source.location_at(tok.index).line
        events_by_line.setdefault(line, []).append((verb, rules))
        touched.update(rules or {'*'})

    if not events_by_line:
        return DirectiveMap()

    total_lines = _line_count(source.text)
    overrides_by_line: dict[int, dict[str, bool]] = {}
    block_state: dict[str, bool] = {}
    next_line_override: dict[str, bool] = {}

    for line in range(1, total_lines + 2):
        line_override: dict[str, bool] = dict(next_line_override)
        next_line_override = {}

        for verb, rules in events_by_line.get(line, []):
            value = _value_for_verb(verb)
            targets = rules or {'*'}

            if verb in ('disable', 'enable'):
                if not rules:
                    # Bare directive clears the block state and sets `*`.
                    block_state.clear()
                    block_state['*'] = value
                else:
                    for r in targets:
                        block_state[r] = value
            elif verb in ('disable-line', 'enable-line'):
                for r in targets:
                    line_override[r] = value
            elif verb in ('disable-next-line', 'enable-next-line'):
                for r in targets:
                    next_line_override[r] = value

        effective = {**block_state, **line_override}
        if effective:
            overrides_by_line[line] = effective

    return DirectiveMap(overrides_by_line=overrides_by_line, touched_rules=touched)


def _value_for_verb(verb: str) -> bool:
    """`enable*` → True (force-on); `disable*` → False (force-off)."""
    return verb.startswith('enable')


def _iter_comments(tokens: list[Token]) -> Iterator[Token]:
    for tok in tokens:
        for tr in tok.leading_trivia:
            if tr.kind in (Kind.COMMENT_BLOCK, Kind.COMMENT_LINE):
                yield tr


def _strip_comment(text: str) -> str:
    if text.startswith('/*') and text.endswith('*/'):
        return text[2:-2]
    if text.startswith('//'):
        return text[2:]
    return text


def _parse_rule_list(raw: str | None) -> set[str]:
    if not raw:
        return set()
    out: set[str] = set()
    for chunk in raw.split(','):
        token = chunk.strip()
        if token:
            out.add(token)
    return out


def _line_count(text: str) -> int:
    if not text:
        return 1
    n = text.count('\n')
    return n + (0 if text.endswith('\n') else 1)
