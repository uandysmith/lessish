"""Tier-2: same property declared twice with identical value+!important.

Within a single ruleset, two declarations with the same name, value
text, and `!important` flag are redundant — the earlier one cannot
affect the cascade (a later same-property declaration always wins).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import AtRule, Declaration, Node, Ruleset
from .._findings import Finding, Fix
from ._base import LintContext, Rule


class DuplicatePropertyRule(Rule):
    id = 'duplicate-property'
    severity = 'warning'
    fix_tier = 'risky'
    description = 'Same property+value declared twice in one ruleset.'
    node_types = (Ruleset, AtRule)

    def on_node(self, node: Node, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        children: list[Node]
        if isinstance(node, Ruleset):
            children = node.rules
        elif isinstance(node, AtRule) and node.body is not None:
            children = node.body
        else:
            return
        seen: dict[tuple[str, str, bool], Declaration] = {}
        for child in children:
            if not isinstance(child, Declaration) or child.variable:
                continue
            key = (child.name, _value_text(child, ctx), child.important)
            prior = seen.get(key)
            if prior is not None:
                span = _decl_span(prior, ctx)
                yield Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    message=f'`{child.name}` repeated with same value (line {ctx.location_at(child.index).line})',
                    location=ctx.location_at(prior.index),
                    span=span,
                    fix=Fix(
                        replacement='',
                        safety='risky',
                        description=f'drop earlier `{child.name}`',
                    ),
                )
            seen[key] = child


def _value_text(decl: Declaration, ctx: LintContext) -> str:
    start = decl.value.index
    text = ctx.text
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0 and ch in (';', '}'):
            break
        i += 1
    return text[start:i].rstrip()


def _decl_span(decl: Declaration, ctx: LintContext) -> tuple[int, int]:
    text = ctx.text
    n = len(text)
    i = decl.value.index
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
    start = decl.index
    while start > 0 and text[start - 1] in (' ', '\t'):
        start -= 1
    if i < n and text[i] == '\n':
        i += 1
    return start, i
