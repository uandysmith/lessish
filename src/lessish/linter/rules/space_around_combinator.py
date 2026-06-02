"""Tier-0: combinators `>`, `+`, `~` should have spaces on each side.

Distinguishes selector position from declaration-value position via a
small state machine over LBRACE / RBRACE / COLON / SEMICOLON events.
Only the outer "selector" state treats `>`/`+`/`~` as combinators
(inside values they're arithmetic operators).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_SEL_ENDERS = {
    Kind.IDENT,
    Kind.DOT_IDENT,
    Kind.HASH,
    Kind.HASH_BARE,
    Kind.AMPERSAND,
    Kind.RBRACKET,
    Kind.RPAREN,
    Kind.STAR,
}
_SEL_STARTERS = {
    Kind.IDENT,
    Kind.DOT_IDENT,
    Kind.HASH,
    Kind.HASH_BARE,
    Kind.AMPERSAND,
    Kind.LBRACKET,
    Kind.STAR,
    Kind.COLON,
    Kind.DOUBLECOLON,
}
_COMBINATORS = (Kind.GT, Kind.PLUS, Kind.TILDE)


@dataclass
class _State:
    in_block: bool = False
    in_value: bool = False


class SpaceAroundCombinatorRule(Rule):
    id = 'space-around-combinator'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Insert spaces around child / sibling combinators.'
    token_kinds = (Kind.LBRACE, Kind.RBRACE, Kind.SEMICOLON, Kind.COLON, *_COMBINATORS)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind is Kind.LBRACE:
            state.in_block = True
            state.in_value = False
            return
        if tok.kind is Kind.RBRACE:
            state.in_block = False
            state.in_value = False
            return
        if tok.kind is Kind.SEMICOLON:
            state.in_value = False
            return
        if tok.kind is Kind.COLON:
            if state.in_block and not state.in_value:
                state.in_value = True
            return
        # combinator token
        if state.in_value:
            return
        toks = ctx.tokens
        if idx == 0 or idx + 1 >= len(toks):
            return
        prev = toks[idx - 1]
        nxt = toks[idx + 1]
        if prev.kind not in _SEL_ENDERS or nxt.kind not in _SEL_STARTERS:
            return
        prev_end = prev.index + len(prev.text)
        tok_end = tok.index + len(tok.text)
        text = ctx.text
        need_left = text[prev_end : tok.index] == ''
        need_right = text[tok_end : nxt.index] == ''
        if not need_left and not need_right:
            return
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'space around `{tok.text}` combinator',
            location=ctx.location_at(tok.index),
            span=(prev_end, nxt.index),
            fix=Fix(
                replacement=f' {tok.text} ',
                safety='safe',
                description='add spaces around combinator',
            ),
        )
