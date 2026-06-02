"""AST transform pass: rewrite parsed Rulesets that look like mixin
definitions into ``MixinDefinition`` nodes, and ``MixinCallStatement``
placeholders into structured ``MixinCall`` nodes.

Also handles CSS-guard extraction (``.x when (cond) { ... }``) and
statement-form extends (``&:extend(.b);``).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ast_nodes import (
    AtRule,
    Condition,
    Element,
    Extend,
    MixinCall,
    MixinCallStatement,
    MixinDefinition,
    Node,
    Ruleset,
    Selector,
    VariableCall,
)
from ..errors import EvalError
from ..parser import _parse_extend_args, parse_guard_text
from .params import _parse_mixin_call_text, parse_mixin_params

# At-rule names that legitimately appear as a bare `@name <prelude>;`
# statement (no body, no `()` after). Anything else with body=None and
# an empty/`()` prelude is treated as a variable call.
_KNOWN_STATEMENT_AT_RULES: frozenset[str] = frozenset(
    {
        '@import',
        '@charset',
        '@namespace',
        '@apply',
        '@plugin',
    }
)


@dataclass(kw_only=True, slots=True)
class _ExtendStatement(Node):
    """Internal sentinel produced for statement-form `&:extend(.b);`
    inside a block. The enclosing Ruleset hoists the extends out onto
    its selectors and the sentinel is dropped from `rules`.
    """

    extends: list[Extend]


# `_ExtendStatement` is defined after ast_nodes.py has already
# stamped `__lessish_child_attrs__` on every other Node subclass.
# Re-run the stamp so this one picks it up too (idempotent for
# already-stamped classes; see `ast_nodes._stamp_child_attrs`).
from ..ast_nodes import _stamp_child_attrs as _ast_stamp  # noqa: E402

_ast_stamp()


def transform_mixins(node: Node) -> Node:
    """Recursively rewrite mixin-definition Rulesets and MixinCallStatements
    into the structured `MixinDefinition` / `MixinCall` nodes. Other node
    types pass through with their children transformed.

    Also extracts guard clauses (`when (...)`) from selectors: from a
    mixin definition's tail element, or from a trailing `when (...)`
    pair on a plain CSS-guarded ruleset.
    """
    if isinstance(node, Ruleset):
        new_rules = [transform_mixins(r) for r in node.rules]
        # Hoist statement-form extends onto every selector of this ruleset,
        # EXCEPT when this Ruleset is about to become a MixinDefinition —
        # there are no caller-bound selectors yet, so hoisting drops the
        # extend. Keep `_ExtendStatement` in the body so the extend
        # travels with the splice and attaches to the call site's
        # surrounding selectors (`.x { .clearfix-mixin(); }` →
        # `.x:extend(.clearfix)`).
        will_be_mixin_def = _is_mixin_definition(node)
        hoisted_extends: list[Extend] = []
        kept_rules: list[Node] = []
        for r in new_rules:
            if isinstance(r, _ExtendStatement) and not will_be_mixin_def:
                hoisted_extends.extend(r.extends)
            else:
                kept_rules.append(r)
        new_rules = kept_rules
        if hoisted_extends and node.selectors:
            new_selectors_with_extends: list[Selector] = []
            for sel in node.selectors:
                # Each selector needs its OWN Extend instances. The collect
                # pass writes `extender_paths` directly on the Extend node,
                # so a shared object would have its paths overwritten by
                # the last selector iterated. `.ext3, .ext4 { &:extend(.x); }`
                # is the classic case — both selectors must extend `.x`.
                sel_extends = [Extend(index=e.index, target=e.target, option=e.option) for e in hoisted_extends]
                new_selectors_with_extends.append(
                    Selector(
                        index=sel.index,
                        elements=sel.elements,
                        extend_list=sel.extend_list + sel_extends,
                    )
                )
            node = Ruleset(
                index=node.index,
                selectors=new_selectors_with_extends,
                rules=node.rules,
                root=node.root,
                paths=node.paths,
                condition=node.condition,
            )
        if _is_mixin_definition(node):
            elements = node.selectors[0].elements
            name = _extract_mixin_name(elements)
            params_text, guard_text, guard_offset = _split_signature_and_guard(elements[-1].value)
            params = parse_mixin_params(params_text)
            guard = _parse_guard_if_any(guard_text, base_offset=elements[-1].index + guard_offset)
            return MixinDefinition(
                index=node.index,
                name=name,
                params=params,
                rules=new_rules,
                guard=guard,
            )
        # CSS guard: `.x when (cond) { ... }` — selectors that end in a
        # `when` element followed by `(...)`. Strip the guard tail from
        # selectors and attach to Ruleset.condition.
        new_selectors, guard = _extract_css_guard(node.selectors)
        return Ruleset(
            index=node.index,
            selectors=new_selectors,
            rules=new_rules,
            root=node.root,
            paths=node.paths,
            condition=guard,
        )
    if isinstance(node, MixinCallStatement):
        # `&:extend(...)` and `:extend(...)`: the statement-form extend.
        # Parse out the target list and return Extend nodes; these are
        # then attached by `_attach_statement_extends_to_selectors`
        # when the enclosing Ruleset is processed.
        stripped = node.text.lstrip()
        if stripped.startswith('&:extend') or stripped.startswith(':extend'):
            after = stripped.split(':extend', 1)[1].lstrip()
            if after.startswith('('):
                # Find balanced ).
                depth = 0
                end = -1
                for i, ch in enumerate(after):
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end != -1:
                    inside = after[1:end]
                    extends = _parse_extend_args(inside, base_index=node.index)
                    # Wrap into a tagged sentinel so the surrounding
                    # Ruleset can hoist these onto its selectors.
                    return _ExtendStatement(extends=extends, index=node.index)
            return node
        name, args, important = _parse_mixin_call_text(node.text, base_index=node.index)
        # `@my();` (or `@my`) — detached-ruleset variable call rather
        # than a mixin call. Route to VariableCall so the evaluator can
        # look up the variable and splice its body.
        if name.startswith('@') and not args:
            return VariableCall(index=node.index, name=name)
        return MixinCall(
            index=node.index,
            name=name,
            args=args,
            important=important,
        )
    if isinstance(node, AtRule):
        # `@my();` and `@my;` parse as AtRules (any AT_NAME without a
        # trailing `:` enters the at-rule path). Recognise them here as
        # variable calls when the form has no body and an empty / `()`
        # prelude, and the name isn't one of the known statement at-rules.
        if node.body is None and node.prelude in ('', '()') and node.name not in _KNOWN_STATEMENT_AT_RULES:
            return VariableCall(index=node.index, name=node.name)
        new_body = [transform_mixins(r) for r in node.body] if node.body is not None else None
        return AtRule(
            index=node.index,
            name=node.name,
            prelude=node.prelude,
            body=new_body,
            trailing_comments=node.trailing_comments,
        )
    return node


def _is_mixin_definition(rs: Ruleset) -> bool:
    """A Ruleset is a mixin definition if its single selector ends with
    a `(...)` tail element (optionally followed by ` when (...)`) and
    starts with `.name` or `#name`.

    A plain CSS-guarded ruleset like `.x when (cond) { ... }` is **not**
    a mixin def — its `(cond)` lives in its own element preceded by a
    standalone `when` element, which we detect and reject here.
    """
    if len(rs.selectors) != 1:
        return False
    elements = rs.selectors[0].elements
    if len(elements) < 2:
        return False
    head = elements[0].value
    if not (head.startswith('.') or head.startswith('#')):
        return False
    last = elements[-1].value
    if not last.startswith('('):
        return False
    # If the element before the `(...)` is the literal `when` keyword
    # (or `when not`), this is a CSS guard, not a mixin signature.
    if len(elements) >= 2 and elements[-2].value == 'when':
        return False
    if len(elements) >= 3 and elements[-2].value == 'not' and elements[-3].value == 'when':
        return False
    # The tail must be either `(...)` alone or `(...) when (...)`.
    sig, _, _ = _split_signature_and_guard(last)
    return _is_paren_balanced('(' + sig + ')')


def _split_signature_and_guard(text: str) -> tuple[str, str | None, int]:
    """Split a mixin-def tail like `(@a, @b) when (@a > 5)` into
    (`@a, @b`, `(@a > 5)`, guard_offset). When there's no `when` clause,
    returns (params_text, None, 0). The leading/trailing parens of
    the params section are stripped; the guard text is returned with
    its outer parens intact (the guard parser absorbs them).

    `guard_offset` is the position of the first non-whitespace character
    of the guard within `text` — callers use it to anchor parse errors
    raised inside the guard at the right source column.
    """
    if not text.startswith('('):
        return text, None, 0
    # Find the matching close-paren of the params group.
    depth = 0
    end = -1
    for i, ch in enumerate(text):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return _strip_outer_parens(text), None, 0
    params_text = text[1:end]
    rest_start = end + 1
    rest = text[rest_start:]
    rest_lstripped = rest.lstrip()
    if not rest_lstripped:
        return params_text, None, 0
    # The remainder should start with `when` followed by the guard.
    if not rest_lstripped.startswith('when'):
        # Anything else after the params is a syntax error in less.js,
        # but we tolerate it by treating the whole element as params.
        return _strip_outer_parens(text), None, 0
    after_when = rest_lstripped[len('when') :]
    after_when_lstripped = after_when.lstrip()
    guard_text = after_when_lstripped.rstrip()
    if not guard_text:
        return params_text, None, 0
    guard_offset = (
        rest_start + (len(rest) - len(rest_lstripped)) + len('when') + (len(after_when) - len(after_when_lstripped))
    )
    return params_text, guard_text, guard_offset


def _extract_css_guard(selectors: list[Selector]) -> tuple[list[Selector], Condition | None]:
    """If the last selector ends with a `when` element followed by a
    `(...)` element, peel those off and return the guard. Otherwise
    return (selectors, None) unchanged.

    Only the **last** selector in a comma-list carries the guard in
    less.js — `.a, .b when (cond) { ... }` guards just `.b`. A guard
    attached to anything other than the sole/last selector in a
    comma-list raises `SyntaxError: Guards are only currently allowed
    on a single selector.` (mirrors less.js's parse-time check).
    """
    if not selectors:
        return selectors, None
    # Multi-selector list with a `when (...)` tail on any non-last
    # selector — less.js rejects this outright.
    if len(selectors) > 1:
        for sel in selectors[:-1]:
            if _selector_has_guard_tail(sel):
                err = EvalError('Guards are only currently allowed on a single selector.')
                # Anchor at the next selector's start (less.js reports
                # the comma-following one — that's the bare-no-guard
                # sibling that exposed the inconsistency).
                err._base_index = selectors[-1].index
                raise err
    last = selectors[-1]
    elements = last.elements
    # Look for `... when (...)` or `... when not (...)` at the tail.
    # The `when` keyword is separated by whitespace and the parenthesised
    # condition becomes one (or two, with `not`) trailing elements.
    if len(elements) < 2:
        return selectors, None
    tail = elements[-1].value
    if not (tail.startswith('(') and tail.endswith(')')):
        return selectors, None
    when_idx: int | None = None
    if elements[-2].value == 'when':
        when_idx = len(elements) - 2
        guard_text = tail
    elif len(elements) >= 3 and elements[-2].value == 'not' and elements[-3].value == 'when':
        # Reassemble `not (...)` so `parse_guard_text` sees the standard
        # guard-clause spelling. Anchor stays on the `(` element so error
        # columns still point at the offending parens.
        when_idx = len(elements) - 3
        guard_text = 'not ' + tail
    else:
        return selectors, None
    # Guard tail on the last selector + more than one selector in the
    # list → less.js still rejects. Anchor at the column just past the
    # closing `)` (less.js's reporter points one column past the guard
    # — typically at the `{` opening when no extra whitespace sits
    # between them).
    if len(selectors) > 1:
        err = EvalError('Guards are only currently allowed on a single selector.')
        err._base_index = elements[-1].index + len(tail) + 1
        raise err
    # Trim the `when ...` elements off the last selector, preserving its
    # `extend_list` (`.x:extend(.y all) when (@cond) {}` carries the
    # extend on the same selector that holds the guard).
    trimmed = Selector(
        index=last.index,
        elements=elements[:when_idx],
        extend_list=last.extend_list,
    )
    new_selectors = list(selectors[:-1])
    if trimmed.elements:
        new_selectors.append(trimmed)
    guard = _parse_guard_if_any(guard_text, base_offset=elements[-1].index)
    return new_selectors, guard


def _selector_has_guard_tail(sel: Selector) -> bool:
    """True iff `sel` ends with a `when (...)` or `when not (...)`
    element tail — used to detect guards on non-last selectors in a
    comma-separated list.
    """
    elements = sel.elements
    if len(elements) < 2:
        return False
    tail = elements[-1].value
    if not (tail.startswith('(') and tail.endswith(')')):
        return False
    if elements[-2].value == 'when':
        return True
    return len(elements) >= 3 and elements[-2].value == 'not' and elements[-3].value == 'when'


def _parse_guard_if_any(text: str | None, *, base_offset: int = 0) -> Condition | None:
    if not text:
        return None
    return parse_guard_text(text, base_offset=base_offset)


def _extract_mixin_name(elements: list[Element]) -> str:
    """Reconstruct the mixin name from selector elements minus the
    trailing `(...)` element. Preserves combinators so `#ns > .mixin`
    round-trips.
    """
    parts: list[str] = []
    for i, e in enumerate(elements[:-1]):
        if i == 0:
            parts.append(e.value)
            continue
        if e.combinator == ' ':
            parts.append(' ' + e.value)
        elif e.combinator == '':
            parts.append(e.value)
        else:
            parts.append(f' {e.combinator} {e.value}')
    return ''.join(parts)


def _strip_outer_parens(text: str) -> str:
    if text.startswith('(') and text.endswith(')'):
        return text[1:-1]
    return text


def _is_paren_balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0
