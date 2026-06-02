"""Tier-2: mixin definition with no invocation in the file.

Accumulator pattern: collect every MixinDefinition, MixinCall (name
segments), and Selector with Extend list. At file end, emit a finding
for any definition whose name doesn't appear in calls or extends.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ...ast_nodes import (
    Extend,
    MixinCall,
    MixinDefinition,
    Node,
    Selector,
)
from .._findings import Finding, Fix
from ._base import LintContext, Rule

_SEGMENT_RE = re.compile(r'[#.>][^.#>\s]+')


@dataclass
class _State:
    defs: list[MixinDefinition] = field(default_factory=list)
    call_segments: set[str] = field(default_factory=set)
    extend_targets: set[str] = field(default_factory=set)


class UnusedMixinRule(Rule):
    id = 'unused-mixin'
    severity = 'warning'
    fix_tier = 'risky'
    description = 'Mixin defined but never invoked in this file.'
    node_types = (MixinDefinition, MixinCall, Selector)

    def state_factory(self) -> _State:
        return _State()

    def on_node(self, node: Node, ctx: LintContext, state: _State) -> Iterable[Finding]:  # noqa: ARG002
        if isinstance(node, MixinDefinition):
            state.defs.append(node)
        elif isinstance(node, MixinCall):
            for seg in _SEGMENT_RE.findall(node.name) or [node.name]:
                state.call_segments.add(seg)
        elif isinstance(node, Selector):
            for ext in node.extend_list:
                if isinstance(ext, Extend):
                    for seg in _SEGMENT_RE.findall(ext.target) or [ext.target]:
                        state.extend_targets.add(seg)
        return ()

    def on_file_end(self, ctx: LintContext, state: _State) -> Iterable[Finding]:
        if not state.defs:
            return
        project_index = ctx.project_index
        global_calls: set[str] = set()
        global_extends: set[str] = set()
        if project_index is not None:
            global_calls = set(getattr(project_index, 'mixin_call_segments', set()))
            global_extends = set(getattr(project_index, 'extend_targets', set()))

        for d in state.defs:
            if d.is_ruleset_wrapper:
                continue
            if d.name in state.call_segments or d.name in global_calls:
                continue
            if d.name in state.extend_targets or d.name in global_extends:
                continue
            safety = 'safe' if project_index is not None else 'risky'
            span = _def_span(d, ctx)
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'mixin `{d.name}` is defined but never invoked',
                location=ctx.location_at(d.index),
                span=span,
                fix=Fix(
                    replacement='',
                    safety=safety,
                    description=f'remove unused mixin {d.name}',
                ),
            )


def _def_span(d: MixinDefinition, ctx: LintContext) -> tuple[int, int]:
    text = ctx.text
    n = len(text)
    start = d.index
    while start > 0 and text[start - 1] in (' ', '\t'):
        start -= 1
    i = d.index
    while i < n and text[i] != '{':
        i += 1
    depth = 0
    while i < n:
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                i += 1
                break
        i += 1
    if i < n and text[i] == '\n':
        i += 1
    return start, i
