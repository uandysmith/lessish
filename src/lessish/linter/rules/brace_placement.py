"""Tier-0: brace placement.

- `block-opening-brace-line`: `.a\n{` (brace on next line) → `.a {`.
  Disabled by default since some teams prefer Allman style.
- `closing-brace-newline-before`: in a multi-decl block, `}` should be
  on its own line. Single-decl one-liners pass.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule
from ._comments import comments_in_gap


class BlockOpeningBraceLineRule(Rule):
    id = 'block-opening-brace-line'
    severity = 'info'
    fix_tier = 'safe'
    description = 'Opening `{` should sit on the selector line.'
    token_kinds = (Kind.LBRACE,)

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        if not ctx.options.get('enabled', False):
            return
        if idx == 0:
            return
        prev = ctx.tokens[idx - 1]
        between = ctx.text[prev.index + len(prev.text) : tok.index]
        if '\n' not in between:
            return
        start = prev.index + len(prev.text)
        end = tok.index
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='opening `{` should follow selector on same line',
            location=ctx.location_at(end),
            span=(start, end),
            fix=Fix(replacement=' ', safety='safe', description='join to same line'),
        )


@dataclass
class _CBState:
    # Stack entries: (opener_kind, lbrace_token_index, semicolon_count).
    # Only LBRACE openers participate in the rule — INTERP_OPEN closes
    # on RBRACE too but isn't a block.
    stack: list[tuple[Kind, int, int]] = field(default_factory=list)
    paren_depth: int = 0


class ClosingBraceNewlineBeforeRule(Rule):
    id = 'closing-brace-newline-before'
    severity = 'warning'
    fix_tier = 'safe'
    description = '`}` should be on its own line.'
    token_kinds = (
        Kind.LBRACE,
        Kind.INTERP_OPEN,
        Kind.DOLLAR_INTERP_OPEN,
        Kind.SEMICOLON,
        Kind.RBRACE,
        Kind.LPAREN,
        Kind.RPAREN,
    )

    def state_factory(self) -> _CBState:
        return _CBState()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _CBState) -> Iterable[Finding]:
        toks = ctx.tokens
        text = ctx.text
        if tok.kind is Kind.LPAREN:
            state.paren_depth += 1
            return
        if tok.kind is Kind.RPAREN:
            state.paren_depth = max(0, state.paren_depth - 1)
            return
        if tok.kind is Kind.LBRACE:
            if state.paren_depth == 0:
                state.stack.append((Kind.LBRACE, idx, 0))
            return
        if tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
            state.stack.append((tok.kind, idx, 0))
            return
        if tok.kind is Kind.SEMICOLON and state.stack and state.stack[-1][0] is Kind.LBRACE:
            opener, lbi, c = state.stack[-1]
            state.stack[-1] = (opener, lbi, c + 1)
            return
        # RBRACE
        if state.paren_depth > 0:
            return
        if not state.stack:
            return
        opener, lbrace_idx, sem_count = state.stack.pop()
        if opener is not Kind.LBRACE:
            return
        # `threshold` controls how many semicolons (=declarations) inside
        # the block trigger the "`}` on own line" rule. Default 1 ⇒
        # every non-empty block. Set to 2 to allow `.a { color: red; }`
        # on one line.
        threshold = int(ctx.options.get('threshold', 1))
        if sem_count < threshold:
            return
        if idx == 0:
            return
        prev = toks[idx - 1]
        between = text[prev.index + len(prev.text) : tok.index]
        if '\n' in between:
            return
        start = prev.index + len(prev.text)
        lbrace_pos = toks[lbrace_idx].index
        line_start = lbrace_pos
        while line_start > 0 and text[line_start - 1] != '\n':
            line_start -= 1
        indent = text[line_start:lbrace_pos]
        outer_indent = ''
        for ch in indent:
            if ch in (' ', '\t'):
                outer_indent += ch
            else:
                break
        # A comment trailing the last declaration on the same line as `}`
        # (e.g. `color: #FFFFFF; /* lessish-disable-line … */ }`) lives in
        # this gap. Dropping it silently stops the directive applying and
        # makes `compile(format(s))` differ from `compile(s)`. Keep each
        # such comment on the declaration's line — line-scoped directives
        # must stay on the line they govern — then put `}` on its own line.
        comments = comments_in_gap(between)
        replacement = ''.join(' ' + c for c in comments) + '\n' + outer_indent
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='`}` should be on its own line',
            location=ctx.location_at(start),
            span=(start, tok.index),
            fix=Fix(replacement=replacement, safety='safe', description='move `}` to new line'),
        )
