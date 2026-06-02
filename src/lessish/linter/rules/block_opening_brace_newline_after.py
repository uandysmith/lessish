"""Tier-0: content inside `{...}` should start on a new line.

Forces every non-empty block to put its first statement on the next
line, indented by one more level than the opener. Combined with
`block-closing-brace-newline-before` (extended) and
`declaration-block-semicolon-newline-after`, this gives a Prettier-
style multi-line block layout.

Empty blocks (`.a {}`) stay inline.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule
from ._comments import comments_in_gap


@dataclass
class _State:
    depth: int = 0
    paren_depth: int = 0


class BlockOpeningBraceNewlineAfterRule(Rule):
    id = 'block-opening-brace-newline-after'
    severity = 'warning'
    fix_tier = 'safe'
    description = 'Block content should start on a new line, indented.'
    token_kinds = (Kind.LBRACE, Kind.RBRACE, Kind.LPAREN, Kind.RPAREN)

    def state_factory(self) -> _State:
        return _State()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if tok.kind is Kind.LPAREN:
            state.paren_depth += 1
            return
        if tok.kind is Kind.RPAREN:
            state.paren_depth = max(0, state.paren_depth - 1)
            return
        if tok.kind is Kind.RBRACE:
            if state.paren_depth == 0:
                state.depth = max(0, state.depth - 1)
            return
        # LBRACE
        if state.paren_depth > 0:
            # `{` inside `(...)` is a detached-ruleset argument or
            # similar value-position construct — not a block.
            return
        state.depth += 1
        toks = ctx.tokens
        if idx + 1 >= len(toks):
            return
        nxt = toks[idx + 1]
        if nxt.kind is Kind.RBRACE:
            # Empty block — leave inline.
            return
        between = ctx.text[tok.index + 1 : nxt.index]
        if '\n' in between:
            return
        width = int(ctx.options.get('width', 2))
        indent = ' ' * (width * state.depth)
        # A comment sitting between `{` and the first statement (e.g.
        # `.a { /* lessish-disable … */ color: red; }`) lives in this gap.
        # Re-emit each such comment on its own indented line so it isn't
        # discarded by the reflow.
        comments = comments_in_gap(between)
        replacement = ''.join('\n' + indent + c for c in comments) + '\n' + indent
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='block content should start on a new line',
            location=ctx.location_at(tok.index + 1),
            span=(tok.index + 1, nxt.index),
            fix=Fix(
                replacement=replacement,
                safety='safe',
                description='insert newline + indent',
            ),
        )
