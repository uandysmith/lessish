"""Tier-0: arithmetic operators in expressions should have spaces on
each side: `1+2px` → `1 + 2px`.

Off by default — adding spaces around `/` changes Less arithmetic
semantics under `math: parens-division`.

Recognised only in value position (after `:` and before `;`/`}`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_BINARY = (Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH)


@dataclass
class _State:
    in_block: bool = False
    in_value: bool = False


class SpaceAroundBinaryOpRule(Rule):
    id = 'space-around-binary-op'
    severity = 'info'
    fix_tier = 'safe'
    description = 'Insert space around arithmetic operators.'
    token_kinds = (Kind.LBRACE, Kind.RBRACE, Kind.SEMICOLON, Kind.COLON, *_BINARY)

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
        # binary op token
        if not state.in_value:
            return
        if not ctx.options.get('enabled', False):
            return
        toks = ctx.tokens
        if idx == 0 or idx + 1 >= len(toks):
            return
        prev = toks[idx - 1]
        nxt = toks[idx + 1]
        prev_end = prev.index + len(prev.text)
        tok_end = tok.index + len(tok.text)
        text = ctx.text
        need_left = text[prev_end : tok.index] == ''
        need_right = text[tok_end : nxt.index] == ''
        if not need_left and not need_right:
            return
        if prev.kind in (Kind.LPAREN, Kind.COMMA, Kind.COLON):
            return
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message=f'space around `{tok.text}` operator',
            location=ctx.location_at(tok.index),
            span=(prev_end, nxt.index),
            fix=Fix(
                replacement=f' {tok.text} ',
                safety='safe',
                description='add spaces around operator',
            ),
        )
