"""Tier-3: numeric literal repeated `min-occurrences` times without
ever appearing as a variable value.

Accumulator pattern: every NUMBER token is recorded; every
`@name: <literal>;` declaration marks that literal as "extracted".
At file end, literals seen ≥ N times AND not extracted are reported
at every occurrence.

The fix is human — extract into a variable. No autofix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ...lexer import Kind, Token
from .._findings import Finding
from ._base import LintContext, Rule

_UNIT_RE = re.compile(r'^([\w%]+)$')


@dataclass(frozen=True)
class _Literal:
    value: str
    unit: str


@dataclass
class _State:
    occurrences: dict[_Literal, list[tuple[int, int]]] = field(default_factory=dict)
    defined_as_var: set[_Literal] = field(default_factory=set)


class MagicNumberRule(Rule):
    id = 'magic-number'
    severity = 'info'
    fix_tier = 'none'
    description = 'Numeric literal repeated without an extracted variable.'
    token_kinds = (Kind.NUMBER, Kind.AT_NAME)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        toks = ctx.tokens
        if tok.kind is Kind.NUMBER:
            lit = _literal_at(toks, idx)
            occ_end = _lit_end(toks, idx)
            state.occurrences.setdefault(lit, []).append((tok.index, occ_end))
            return ()
        # AT_NAME: detect `@name: <number-literal>;` and remember.
        if idx + 2 >= len(toks):
            return ()
        if toks[idx + 1].kind is not Kind.COLON or toks[idx + 2].kind is not Kind.NUMBER:
            return ()
        lit = _literal_at(toks, idx + 2)
        j = _lit_end_index(toks, idx + 2)
        if j < len(toks) and toks[j].kind is Kind.SEMICOLON:
            state.defined_as_var.add(lit)
        return ()

    def on_file_end(self, ctx: LintContext, state: _State) -> Iterable[Finding]:
        threshold = int(ctx.options.get('min-occurrences', 3))
        for lit, occs in state.occurrences.items():
            if len(occs) < threshold:
                continue
            if lit in state.defined_as_var:
                continue
            for start, end in occs:
                yield Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    message=f'`{lit.value}{lit.unit}` appears {len(occs)} times; consider a variable',
                    location=ctx.location_at(start),
                    span=(start, end),
                )


def _literal_at(toks: list[Token], i: int) -> _Literal:
    num = toks[i]
    unit = ''
    if i + 1 < len(toks):
        nxt = toks[i + 1]
        if nxt.kind is Kind.PERCENT and nxt.index == num.index + len(num.text):
            unit = '%'
        elif nxt.kind is Kind.IDENT and nxt.index == num.index + len(num.text):
            if _UNIT_RE.match(nxt.text):
                unit = nxt.text
    return _Literal(value=num.text, unit=unit)


def _lit_end(toks: list[Token], i: int) -> int:
    num = toks[i]
    end = num.index + len(num.text)
    if i + 1 < len(toks):
        nxt = toks[i + 1]
        if (nxt.kind is Kind.PERCENT or nxt.kind is Kind.IDENT) and nxt.index == end:
            end = nxt.index + len(nxt.text)
    return end


def _lit_end_index(toks: list[Token], i: int) -> int:
    if i + 1 < len(toks):
        nxt = toks[i + 1]
        num = toks[i]
        if (nxt.kind is Kind.PERCENT or nxt.kind is Kind.IDENT) and nxt.index == num.index + len(num.text):
            return i + 2
    return i + 1
