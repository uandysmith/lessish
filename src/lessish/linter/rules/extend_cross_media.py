"""Tier-3: `:extend(.foo)` that would cross an `@media` boundary.

less.js silently does nothing when an extend's target is outside the
current @media context — surface this as a warning so the author
doesn't assume their extend took effect.

Detection: walk the AST, tracking @media scope. For each Extend in a
selector inside @media, check whether the same target appears at
top-level (where it would NOT participate in the extend). If yes, flag.
This is approximate but catches the most common bug.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...ast_nodes import AtRule, Extend, Node, Ruleset, Selector
from .._findings import Finding
from ._base import LintContext, Rule


class ExtendCrossMediaRule(Rule):
    id = 'extend-cross-media'
    severity = 'warning'
    fix_tier = 'none'
    description = '`:extend(...)` inside @media may not reach top-level rulesets.'

    def check(self, ctx: LintContext) -> Iterable[Finding]:
        root = ctx.ast()
        if root is None:
            return

        # Pass 1: collect top-level selector strings and at-media targets.
        top_level_selectors: set[str] = set()
        media_extends: list[tuple[Extend, int]] = []
        _collect(root, top_level_selectors, media_extends, in_media=False)

        for ext, idx in media_extends:
            target = _normalize_target(ext.target)
            if target in top_level_selectors:
                yield Finding(
                    rule_id=self.id,
                    severity=self.severity,
                    message=(f'`:extend({ext.target})` inside `@media` cannot reach the matching top-level ruleset'),
                    location=ctx.location_at(idx),
                    span=(idx, idx),
                )


def _collect(
    node: Node,
    top_selectors: set[str],
    media_extends: list[tuple[Extend, int]],
    *,
    in_media: bool,
) -> None:
    if isinstance(node, Ruleset):
        if not in_media:
            for sel in node.selectors:
                top_selectors.add(_selector_text(sel))
        else:
            for sel in node.selectors:
                for ext in sel.extend_list:
                    media_extends.append((ext, node.index))
        for child in node.rules:
            _collect(child, top_selectors, media_extends, in_media=in_media)
    elif isinstance(node, AtRule):
        is_media = node.name == '@media'
        if node.body is not None:
            for child in node.body:
                _collect(
                    child,
                    top_selectors,
                    media_extends,
                    in_media=in_media or is_media,
                )


def _selector_text(sel: Selector) -> str:
    parts: list[str] = []
    for el in sel.elements:
        comb = el.combinator
        if comb in ('', ' '):
            parts.append(el.value)
        else:
            parts.append(f' {comb} {el.value}')
    return ''.join(parts).lstrip()


def _normalize_target(target: str) -> str:
    return target.strip()
