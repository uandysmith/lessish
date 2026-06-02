"""Tier-1: `0px` / `0em` / `0%` should be `0` (outside `calc(...)`).

The CSS Values spec keeps units mandatory inside `calc(...)`; the
linter respects this by skipping any NUMBER token whose nearest
enclosing function call is `calc`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_UNIT_KINDS = (Kind.IDENT, Kind.PERCENT)


@dataclass
class _State:
    # One entry per open `(`: True if it was `calc(`.
    calc_stack: list[bool] = field(default_factory=list)


class ZeroUnitRule(Rule):
    id = 'zero-unit'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Drop the unit on a literal `0` outside `calc(...)`.'
    token_kinds = (Kind.LPAREN, Kind.RPAREN, Kind.NUMBER)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        toks = ctx.tokens
        if tok.kind is Kind.LPAREN:
            is_calc = idx > 0 and toks[idx - 1].kind is Kind.IDENT and toks[idx - 1].text.lower() == 'calc'
            state.calc_stack.append(is_calc)
            return
        if tok.kind is Kind.RPAREN:
            if state.calc_stack:
                state.calc_stack.pop()
            return
        # NUMBER
        if any(state.calc_stack):
            return
        try:
            v = float(tok.text)
        except ValueError:
            return
        if v != 0.0:
            return
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        if nxt.kind not in _UNIT_KINDS:
            return
        if nxt.leading_trivia or nxt.index != tok.index + len(tok.text):
            return
        if nxt.kind is Kind.IDENT and not _is_css_unit(nxt.text):
            return
        start = tok.index
        end = nxt.index + len(nxt.text)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'`{tok.text}{nxt.text}` simplifies to `0`',
            location=ctx.location_at(start),
            span=(start, end),
            fix=Fix(replacement='0', safety='safe', description='drop unit'),
        )


_CSS_UNITS: frozenset[str] = frozenset(
    {
        # length
        'px',
        'em',
        'rem',
        'ex',
        'ch',
        'vh',
        'vw',
        'vmin',
        'vmax',
        'cm',
        'mm',
        'in',
        'pt',
        'pc',
        'q',
        'fr',
        # angles, durations, frequencies, resolution
        'deg',
        'grad',
        'rad',
        'turn',
        's',
        'ms',
        'hz',
        'khz',
        'dpi',
        'dpcm',
        'dppx',
    }
)


def _is_css_unit(text: str) -> bool:
    return text.lower() in _CSS_UNITS
