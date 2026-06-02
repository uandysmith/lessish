"""Tier-3: mixin default that references another parameter by forward
order. less.js resolves `.m(@x: @y, @y: 5)` but the ordering is
surprising — the reader might expect @x to bind 5 by default.

Detection: walk MixinDefinition params; for each default value, scan
for `@<name>` references that match a later parameter's name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ...ast_nodes import MixinDefinition
from .._findings import Finding
from ._base import LintContext, Rule

_AT_REF_RE = re.compile(r'@([a-zA-Z_][\w-]*)')


class ConfusingDefaultValueRule(Rule):
    id = 'confusing-default-value'
    severity = 'warning'
    fix_tier = 'none'
    description = 'Mixin default references a parameter declared later.'
    node_types = (MixinDefinition,)

    def on_node(  # type: ignore[override]
        self, mdef: MixinDefinition, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        # For each param, set of NAMES of params declared AFTER it.
        following: set[str] = set()
        names_after: list[set[str]] = [set()] * len(mdef.params)
        for i in range(len(mdef.params) - 1, -1, -1):
            names_after[i] = set(following)
            p = mdef.params[i]
            if p.name and p.name.startswith('@'):
                following.add(p.name[1:])
        text = ctx.text
        for i, p in enumerate(mdef.params):
            if p.default is None:
                continue
            default_text = _default_source_text(text, mdef.index, i)
            for m in _AT_REF_RE.finditer(default_text):
                if m.group(1) in names_after[i]:
                    yield Finding(
                        rule_id=self.id,
                        severity=self.severity,
                        message=(f'parameter `{p.name}` default references `@{m.group(1)}` which is declared later'),
                        location=ctx.location_at(mdef.index),
                        span=(mdef.index, mdef.index),
                    )
                    break


def _default_source_text(source: str, def_start: int, param_index: int) -> str:
    n = len(source)
    i = def_start
    while i < n and source[i] != '(':
        i += 1
    if i >= n:
        return ''
    i += 1
    depth = 1
    chunks: list[str] = []
    chunk_start = i
    while i < n and depth > 0:
        ch = source[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                chunks.append(source[chunk_start:i])
                break
        elif ch == ',' and depth == 1:
            chunks.append(source[chunk_start:i])
            chunk_start = i + 1
        i += 1
    if param_index >= len(chunks):
        return ''
    chunk = chunks[param_index]
    if ':' in chunk:
        return chunk.split(':', 1)[1]
    return ''
