"""Tier-1: declarations before nested rulesets in a block.

For a block whose siblings are exactly Declarations + nested Rulesets
(+ MixinDefinitions + Comments — none of which inject decls at the
parent level), reorder so:

    [variable decls + regular decls (+ attached comments)]
    <blank line>
    [nested Rulesets + MixinDefinitions (+ attached comments)]

Within each group the source order is preserved. A Comment on the same
line as (and after) a preceding item travels WITH that item as a trailing
comment — so `color: red; /* lessish-disable-line … */` keeps its
directive on the declaration's line. Every other Comment leads the next
non-comment item. Comments left over at the end of the block stay last.

Safety: the compiled CSS is unchanged for blocks that contain ONLY
decl/ruleset/mixin-def/comment children (no MixinCall, no AtRule, no
Extend). The rule refuses to fire otherwise.

The fix is recursive — when an outer block reorders, it emits the
formatted text for the entire subtree so a single autofix pass
converges.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...ast_nodes import (
    AtRule,
    Comment,
    Declaration,
    MixinCall,
    MixinDefinition,
    Node,
    Ruleset,
    Selector,
)
from .._findings import Finding, Fix
from ._base import LintContext, Rule

# (leading comments, the item, trailing same-line comments)
_Group = tuple[list[Comment], Node, list[Comment]]


class DeclsBeforeRulesetsRule(Rule):
    id = 'decls-before-rulesets'
    severity = 'info'
    fix_tier = 'safe'
    description = 'Group declarations before nested rulesets, separated by a blank line.'
    node_types = (Ruleset,)

    def on_node(  # type: ignore[override]
        self, node: Ruleset, ctx: LintContext, state: Any
    ) -> Iterable[Finding]:  # noqa: ARG002
        if node.root:
            # The root ruleset has no `{...}` braces — `_block_inner_span`
            # would find the first nested block's `{` and mangle the file.
            return
        if not _eligible(node):
            return
        children = list(node.rules)
        if not _needs_reorder(children):
            return
        block_span = _block_inner_span(node, ctx.text)
        if block_span is None:
            return
        width = int(ctx.options.get('width', 2))
        outer_indent = _outer_indent(node, ctx.text)
        # The block is at depth `len(outer_indent) / width` from the
        # left; its content is one indent deeper.
        depth = len(outer_indent) // width if width else 0
        formatted = _format_block(children, ctx.text, depth + 1, width)
        # The fix replaces inner content of the block (between `{` and
        # `}`). Surround with a leading newline and a trailing newline +
        # outer indent so the `}` lands on its own line.
        replacement = '\n' + formatted + '\n' + outer_indent
        if ctx.text[block_span[0] : block_span[1]] == replacement:
            return
        yield Finding(
            rule_id=self.id,
            severity=self.severity,
            message='declarations should come before nested rulesets',
            location=ctx.location_at(node.index),
            span=block_span,
            fix=Fix(
                replacement=replacement,
                safety='safe',
                description='reorder: decls first, blank line, rulesets after',
            ),
        )


def _children_eligible(children: list[Node]) -> bool:
    """True if every child is something we can format-as-reorder around.

    Pulled out so `add_block` can probe whether to recurse vs emit a
    nested block verbatim from source.
    """
    for child in children:
        if isinstance(child, (Declaration, Comment)):
            continue
        if isinstance(child, (Ruleset, MixinDefinition)):
            if isinstance(child, Ruleset) and _has_bare_amp_selector(child):
                return False
            if isinstance(child, Ruleset) and _has_extend(child):
                # `&:extend(...)` lives in the Selector's extend_list,
                # not in the body. Re-emitting would lose it.
                return False
            continue
        return False
    return True


def _has_extend(rs: Ruleset) -> bool:
    return any(sel.extend_list for sel in rs.selectors)


def _eligible(rs: Ruleset) -> bool:
    """True if every child is something we can safely reorder around.

    Bare-`&` (parent-reference) child rulesets are excluded: they
    compile to the SAME selector as the parent and less.js merges
    them with the parent's decls in source order. Moving such a
    nested ruleset across sibling decls visibly reorders the merged
    output, so we refuse to touch the block.
    """
    return _children_eligible(rs.rules)


def _has_bare_amp_selector(rs: Ruleset) -> bool:
    """True if any of the ruleset's selectors is bare `&` (just the
    parent reference, no compound or pseudo). Those merge with parent
    decls and aren't safe to reorder around.
    """
    for sel in rs.selectors:
        if len(sel.elements) == 1 and sel.elements[0].value == '&':
            return True
    return False


def _needs_reorder(children: list[Node]) -> bool:
    """Fire when both groups are present AND either (a) order is wrong
    or (b) the decl group isn't followed by a blank line."""
    has_decl = any(isinstance(c, Declaration) for c in children)
    has_block = any(isinstance(c, (Ruleset, MixinDefinition)) for c in children)
    return has_decl and has_block


def _block_inner_span(rs: Ruleset, text: str) -> tuple[int, int] | None:
    """Span between `{` and `}` of this block, exclusive of the braces.
    Skips past `@{…}` / `${…}` interpolation in selectors.
    """
    open_brace = _block_open_brace(rs, text)
    if open_brace is None:
        return None
    close_end = _block_close_brace(rs, text)
    if close_end is None:
        return None
    return open_brace + 1, close_end - 1


def _outer_indent(rs: Ruleset, text: str) -> str:
    """Whitespace at start of the line where the selector begins."""
    i = rs.index
    while i > 0 and text[i - 1] != '\n':
        i -= 1
    out = ''
    while i < rs.index and text[i] in (' ', '\t'):
        out += text[i]
        i += 1
    return out


def _format_block(children: list[Node], source: str, depth: int, width: int) -> str:
    """Produce the formatted text for the block's children at the given
    indent depth. Recurses into nested Rulesets / MixinDefinitions so
    the entire subtree comes out reordered in a single pass.
    """
    indent = ' ' * (width * depth)
    groups, block_trailing = _group_with_comments(children, source)
    decl_groups: list[_Group] = []
    block_groups: list[_Group] = []
    for leading, item, trailing in groups:
        if isinstance(item, Declaration):
            decl_groups.append((leading, item, trailing))
        elif isinstance(item, (Ruleset, MixinDefinition)):
            block_groups.append((leading, item, trailing))
        else:
            # Eligibility check should have rejected this; defensive.
            return source  # signals "leave alone"

    lines: list[str] = []

    def add_comment(c: Comment) -> None:
        lines.append(indent + _comment_text(c, source))

    def trailing_suffix(trailing: list[Comment]) -> str:
        # Trailing comments stay on the item's own line, space-separated —
        # this is what keeps a `lessish-disable-line` directive governing
        # the code it was written against after the reorder.
        return ''.join(' ' + _comment_text(c, source) for c in trailing)

    def add_decl(d: Declaration, trailing: list[Comment]) -> None:
        end = _decl_end(d, source)
        text = source[d.index : end].strip()
        # Ensure trailing `;` — source may omit it on the last decl in
        # a block, but the reordered output isn't necessarily last.
        if not text.endswith(';'):
            text += ';'
        lines.append(indent + text + trailing_suffix(trailing))

    def add_block(b: Ruleset | MixinDefinition, trailing: list[Comment]) -> None:
        # Inner block whose content has anything we can't safely
        # reorder (MixinCall, AtRule, …) gets emitted verbatim from
        # the source — the outer reorder remains valid because the
        # inner block is treated as opaque text.
        before = len(lines)
        inner_children = list(b.rules)
        inner_eligible = _children_eligible(inner_children)
        if not inner_eligible:
            full_end = _block_close_brace(b, source)
            if full_end is None:
                lines.append(indent + source[b.index :].rstrip())
            else:
                lines.append(indent + source[b.index : full_end].rstrip())
            _append_trailing(lines, before, trailing_suffix(trailing))
            return

        end = _block_open_brace(b, source)
        if end is None:
            full_end = _block_close_brace(b, source)
            if full_end is None:
                lines.append(indent + source[b.index :].rstrip())
            else:
                lines.append(indent + source[b.index : full_end].rstrip())
            _append_trailing(lines, before, trailing_suffix(trailing))
            return
        header = source[b.index : end].rstrip()
        lines.append(indent + header + ' {')
        if inner_children:
            inner_text = _format_block(inner_children, source, depth + 1, width)
            if inner_text:
                lines.append(inner_text)
        lines.append(indent + '}')
        _append_trailing(lines, before, trailing_suffix(trailing))

    for leading, item, trailing in decl_groups:
        for c in leading:
            add_comment(c)
        assert isinstance(item, Declaration)
        add_decl(item, trailing)

    if decl_groups and block_groups:
        lines.append('')

    for leading, item, trailing in block_groups:
        for c in leading:
            add_comment(c)
        assert isinstance(item, (Ruleset, MixinDefinition))
        add_block(item, trailing)

    if block_trailing:
        if lines:
            lines.append('')
        for c in block_trailing:
            add_comment(c)

    return '\n'.join(lines)


def _group_with_comments(children: list[Node], source: str) -> tuple[list[_Group], list[Comment]]:
    """Partition children into `(leading, item, trailing)` groups plus a
    list of block-level trailing comments.

    A comment that sits on the same source line as (and after) the
    previous item attaches to it as a *trailing* comment; any other
    comment becomes a *leading* comment of the next item. Comments after
    the last item that aren't same-line-trailing are returned separately
    as block-trailing comments (emitted at the end of the block).
    """
    groups: list[_Group] = []
    pending: list[Comment] = []
    for c in children:
        if isinstance(c, Comment):
            if groups and _trails_same_line(groups[-1][1], c, source):
                groups[-1][2].append(c)
            else:
                pending.append(c)
        else:
            groups.append(([*pending], c, []))
            pending = []
    return groups, pending


def _trails_same_line(item: Node, comment: Comment, source: str) -> bool:
    """True when `comment` begins on the same source line as the end of
    `item` (i.e. it trails that item rather than leading the next one)."""
    end = _item_end(item, source)
    if end is None or end > comment.index:
        return False
    return '\n' not in source[end : comment.index]


def _item_end(item: Node, source: str) -> int | None:
    if isinstance(item, Declaration):
        return _decl_end(item, source)
    if isinstance(item, (Ruleset, MixinDefinition)):
        return _block_close_brace(item, source)
    return None


def _comment_text(c: Comment, source: str) -> str:
    return source[c.index : c.index + len(c.text)]


def _append_trailing(lines: list[str], before: int, suffix: str) -> None:
    """Append `suffix` to the last line emitted since index `before`."""
    if suffix and len(lines) > before:
        lines[-1] = lines[-1] + suffix


def _decl_end(d: Declaration, source: str) -> int:
    """End position of a declaration: past its terminating `;`, or up
    to the enclosing block's `}` if the semicolon is missing.

    Tracks `(...)`, `[...]`, `{...}` depth — detached-rulesets in
    value position (`@rules: { color: red; };`) carry inner `;` that
    must be ignored. Also skips string literals (which can contain
    `{` from `@{interp}` without it being a brace event).
    """
    n = len(source)
    i = d.value.index
    paren = 0
    bracket = 0
    brace = 0
    while i < n:
        ch = source[i]
        if ch in ('"', "'"):
            i = _skip_string(source, i)
            continue
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren -= 1
        elif ch == '[':
            bracket += 1
        elif ch == ']':
            bracket -= 1
        elif ch == '{':
            brace += 1
        elif ch == '}':
            if brace > 0:
                brace -= 1
            else:
                return i
        elif paren == 0 and bracket == 0 and brace == 0 and ch == ';':
            return i + 1
        i += 1
    return n


def _skip_string(source: str, i: int) -> int:
    """Walk past a string literal starting at `i` (which points at the
    opening quote). Returns the index just past the closing quote.
    """
    quote = source[i]
    n = len(source)
    i += 1
    while i < n:
        ch = source[i]
        if ch == '\\' and i + 1 < n:
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return i


def _block_open_brace(b: Ruleset | MixinDefinition, source: str) -> int | None:
    """Find the BLOCK `{` for the ruleset starting at `b.index`,
    skipping past `@{…}` / `${…}` interpolation braces and string
    literals that may live inside the selector or its prelude.
    """
    n = len(source)
    i = b.index
    while i < n:
        ch = source[i]
        if ch in ('"', "'"):
            i = _skip_string(source, i)
            continue
        if ch == '{':
            if i > 0 and source[i - 1] in ('@', '$'):
                i = _skip_interp(source, i)
                continue
            return i
        i += 1
    return None


def _block_close_brace(b: Ruleset | MixinDefinition, source: str) -> int | None:
    n = len(source)
    open_idx = _block_open_brace(b, source)
    if open_idx is None:
        return None
    depth = 1
    i = open_idx + 1
    while i < n and depth > 0:
        ch = source[i]
        if ch in ('"', "'"):
            i = _skip_string(source, i)
            continue
        if ch == '{':
            if i > 0 and source[i - 1] in ('@', '$'):
                i = _skip_interp(source, i)
                continue
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _skip_interp(source: str, i: int) -> int:
    """Walk past a `@{...}` or `${...}` interpolation. `i` points at the
    `{`. Returns index just past the matching `}`.
    """
    n = len(source)
    depth = 1
    i += 1
    while i < n and depth > 0:
        c = source[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    return i


_ = (AtRule, MixinCall, Selector)  # imported for type-narrowing readers
