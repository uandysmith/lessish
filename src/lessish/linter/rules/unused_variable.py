"""Tier-2: `@x: …;` declared but never referenced in the file.

Accumulator pattern: collect every variable Declaration (via on_node)
and every variable reference token (via on_token). At file end, emit
a finding for any declared-but-not-referenced variable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ...ast_nodes import Declaration
from ...lexer import Kind, Token
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_IMPLICIT = frozenset({'arguments', 'rest'})


@dataclass
class _State:
    decls: list[Declaration] = field(default_factory=list)
    # All AT_NAME positions seen on the token pass. Filtered against
    # declaration positions during on_file_end (the AST pass runs
    # AFTER tokens, so we can't filter at token-visit time).
    raw_at_refs: list[tuple[int, str]] = field(default_factory=list)
    at_at_refs: set[str] = field(default_factory=set)
    interp_refs: set[str] = field(default_factory=set)


class UnusedVariableRule(Rule):
    id = 'unused-variable'
    severity = 'warning'
    fix_tier = 'risky'
    description = 'Variable declared but never referenced.'
    token_kinds = (Kind.AT_NAME, Kind.AT_AT_NAME, Kind.INTERP_OPEN)
    node_types = (Declaration,)

    def state_factory(self) -> _State:
        return _State()

    def on_node(  # type: ignore[override]
        self, node: Declaration, ctx: LintContext, state: _State
    ) -> Iterable[Finding]:  # noqa: ARG002
        if node.variable:
            state.decls.append(node)
        return ()

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: _State) -> Iterable[Finding]:
        toks = ctx.tokens
        if tok.kind is Kind.AT_NAME:
            state.raw_at_refs.append((tok.index, tok.text[1:]))
        elif tok.kind is Kind.AT_AT_NAME:
            state.at_at_refs.add(tok.text[2:])
        elif tok.kind is Kind.INTERP_OPEN and idx + 1 < len(toks):
            inner = toks[idx + 1]
            if inner.kind is Kind.IDENT:
                state.interp_refs.add(inner.text)
        return ()

    def on_file_end(self, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if not state.decls:
            return
        decl_positions = {d.index for d in state.decls}
        references: set[str] = set(state.at_at_refs)
        references.update(state.interp_refs)
        for pos, name in state.raw_at_refs:
            if pos not in decl_positions:
                references.add(name)

        project_index = ctx.project_index
        global_refs: set[str] = set()
        if project_index is not None:
            global_refs = set(getattr(project_index, 'variable_references', set()))

        for d in state.decls:
            name = d.name[1:] if d.name.startswith('@') else d.name
            if name in _IMPLICIT:
                continue
            if name in references:
                continue
            if name in global_refs:
                continue
            safety = 'safe' if project_index is not None else 'risky'
            span = _decl_line_span(d, ctx)
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'variable `{d.name}` is declared but never used',
                location=ctx.location_at(d.index),
                span=span,
                fix=Fix(
                    replacement='',
                    safety=safety,
                    description=f'remove unused {d.name}',
                ),
            )


def _decl_line_span(d: Declaration, ctx: LintContext) -> tuple[int, int]:
    text = ctx.text
    n = len(text)
    i = d.value.index
    depth = 0
    while i < n:
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0 and ch == ';':
            i += 1
            break
        elif depth == 0 and ch == '}':
            break
        i += 1
    start = d.index
    while start > 0 and text[start - 1] in (' ', '\t'):
        start -= 1
    if i < n and text[i] == '\n':
        i += 1
    return start, i
