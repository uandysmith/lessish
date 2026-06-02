"""Tier-0: after `}` (block close), the next sibling should start on
a new line.

Catches `} font-weight: bold;` and similar same-line-after-close
shapes that survive when an outer block has both nested rulesets
and declarations.

Skipped when the `}` is the close of an `@{...}` / `${...}`
interpolation — those don't end a block. Skipped at end-of-file.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule


@dataclass
class _State:
    stack: list[Kind] = field(default_factory=list)
    paren_depth: int = 0


class BlockClosingBraceNewlineAfterRule(Rule):
    id = 'block-closing-brace-newline-after'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Next sibling after `}` should start on a new line.'
    token_kinds = (
        Kind.LBRACE,
        Kind.INTERP_OPEN,
        Kind.DOLLAR_INTERP_OPEN,
        Kind.RBRACE,
        Kind.LPAREN,
        Kind.RPAREN,
    )

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind is Kind.LPAREN:
            state.paren_depth += 1
            return
        if tok.kind is Kind.RPAREN:
            state.paren_depth = max(0, state.paren_depth - 1)
            return
        if tok.kind in (Kind.LBRACE, Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
            if state.paren_depth == 0:
                state.stack.append(tok.kind)
            return
        # RBRACE
        if state.paren_depth > 0:
            # `}` inside `(...)` closes a detached-ruleset arg, not a block.
            return
        opener = state.stack.pop() if state.stack else Kind.LBRACE
        if opener is not Kind.LBRACE:
            return
        toks = ctx.tokens
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        if nxt.kind is Kind.EOF:
            return
        # `};` — the semicolon terminates the preceding statement (an
        # at-rule, a detached-ruleset assignment, etc.). Don't break
        # them up.
        if nxt.kind is Kind.SEMICOLON:
            return
        # `} ,` — the comma is a list separator (multi-selector etc.).
        if nxt.kind is Kind.COMMA:
            return
        # If we just closed a top-level block, blank-line-before-block
        # owns the separator. This rule only handles the in-block case.
        depth = sum(1 for k in state.stack if k is Kind.LBRACE)
        between = ctx.text[tok.index + 1 : nxt.index]
        if '\n' in between:
            return
        if depth == 0:
            # Top-level — `blank-line-before-block` handles this when
            # enabled. We still insert a single newline as a safety
            # net so the next ruleset doesn't visually run into `}`.
            replacement = '\n'
        else:
            width = int(ctx.options.get('width', 2))
            replacement = '\n' + ' ' * (width * depth)
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='next sibling after `}` should be on a new line',
            location=ctx.location_at(tok.index + 1),
            span=(tok.index + 1, nxt.index),
            fix=Fix(
                replacement=replacement,
                safety='safe',
                description='insert newline + indent',
            ),
        )
