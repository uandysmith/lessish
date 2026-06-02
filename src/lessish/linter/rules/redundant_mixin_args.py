"""Tier-2: a mixin call whose positional args are all equal to the
definition's defaults can be reduced to `name()`.

Accumulator pattern: collect every MixinDefinition (by name) and every
MixinCall. At file end, match each call to its single-candidate def
and check arg-vs-default equality before emitting a fix.

Conservative matching:
* Only fires when exactly ONE mixin definition with that name exists.
* Compares args to defaults via AST structural equality.
* Skips named-arg calls and definitions with pattern parameters.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from dataclasses import fields as _dc_fields

from ...ast_nodes import (
    MixinArg,
    MixinCall,
    MixinDefinition,
    MixinParam,
    Node,
)
from .._findings import Finding, Fix
from ._base import LintContext, Rule


@dataclass
class _State:
    defs: dict[str, list[MixinDefinition]] = field(default_factory=dict)
    calls: list[MixinCall] = field(default_factory=list)


class RedundantMixinArgsRule(Rule):
    id = 'redundant-mixin-args'
    severity = 'info'
    fix_tier = 'risky'
    description = 'Mixin call passes only default-equal args; strip them.'
    node_types = (MixinDefinition, MixinCall)

    def state_factory(self) -> _State:
        return _State()

    def on_node(self, node: Node, ctx: LintContext, state: _State) -> Iterable[Finding]:  # noqa: ARG002
        if isinstance(node, MixinDefinition):
            state.defs.setdefault(node.name, []).append(node)
        elif isinstance(node, MixinCall):
            state.calls.append(node)
        return ()

    def on_file_end(self, ctx: LintContext, state: _State) -> Iterable[Finding]:
        for call in state.calls:
            candidates = state.defs.get(call.name, [])
            if len(candidates) != 1:
                continue
            mdef = candidates[0]
            if any(p.pattern is not None for p in mdef.params):
                continue
            if any(arg.name is not None for arg in call.args):
                continue
            if not _all_args_match_defaults(call.args, mdef.params):
                continue
            if not call.args:
                continue
            span = _arglist_span(call, ctx)
            if span is None:
                continue
            yield Finding(
                rule_id=self.id,
                severity=self.severity,
                message=f'`{call.name}(...)` passes only default values; use `{call.name}()`',
                location=ctx.location_at(span[0]),
                span=span,
                fix=Fix(
                    replacement='()',
                    safety='risky',
                    description=f'strip default-equal args from {call.name}',
                ),
            )


def _all_args_match_defaults(args: list[MixinArg], params: list[MixinParam]) -> bool:
    if len(args) > len(params):
        return False
    for i, arg in enumerate(args):
        if i >= len(params):
            return False
        param = params[i]
        if param.default is None or param.variadic:
            return False
        if not _values_match(arg.value, param.default):
            return False
    return True


def _values_match(a: Node, b: Node) -> bool:
    if type(a) is not type(b):
        return False
    # Compare across every typed field except `index` (source-offset,
    # not part of structural identity). Uses `dataclasses.fields()`
    # rather than `__dict__` for compatibility with `slots=True`
    # classes (slots removes per-instance `__dict__`).
    for f in _dc_fields(a):
        if f.name == 'index':
            continue
        av = getattr(a, f.name)
        bv = getattr(b, f.name)
        if isinstance(av, list) and isinstance(bv, list):
            if len(av) != len(bv):
                return False
            for x, y in zip(av, bv):
                if isinstance(x, Node) and isinstance(y, Node):
                    if not _values_match(x, y):
                        return False
                elif x != y:
                    return False
        elif isinstance(av, Node) and isinstance(bv, Node):
            if not _values_match(av, bv):
                return False
        elif av != bv:
            return False
    return True


def _arglist_span(call: MixinCall, ctx: LintContext) -> tuple[int, int] | None:
    text = ctx.text
    n = len(text)
    i = call.index
    while i < n and text[i] != '(':
        if text[i] in (';', '{', '}', '\n'):
            return None
        i += 1
    if i >= n:
        return None
    paren_start = i
    depth = 1
    i += 1
    while i < n and depth > 0:
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return paren_start, i
