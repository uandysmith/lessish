"""Small text / location / value-promotion utilities used across the
evaluator. Kept dependency-light: only `ast_nodes`, `errors`, and
`source` at module load. Anything that needs other evaluator sub-
modules (`eval_value`, `value_to_css`, color lookup) is pulled in via
function-body imports so the import graph stays acyclic.
"""

from __future__ import annotations

import re

from ..ast_nodes import (
    Anonymous,
    Call,
    Color,
    Declaration,
    Dimension,
    Expression,
    Keyword,
    MixinCall,
    Negative,
    Node,
    Operation,
    Paren,
    Ruleset,
    Value,
)
from ..colors import parse_color
from ..context import EvalContext
from ..errors import DeclAnchor, LessError
from ..source import Source
from ..visitors import value_to_css

_AT_NAME_RE = re.compile(r'@[_a-zA-Z][\w-]*')


def _format_call_args(call: MixinCall, ctx: EvalContext) -> str:
    """Render a MixinCall's args back to text for error messages.
    Mirrors how less.js prints the call signature in
    `No matching definition was found for ...` — args are evaluated
    first so a `@var` reference shows its resolved value.
    """
    from .values import eval_value

    parts: list[str] = []
    for a in call.args:
        try:
            evaled = eval_value(a.value, ctx)
            text = value_to_css(evaled)
        except LessError:
            text = value_to_css(a.value)
        if a.name is not None:
            parts.append(f'{a.name}:{text}')
        else:
            parts.append(text)
    return ', '.join(parts)


def _locate(err: LessError, index: int, ctx: EvalContext, *, source: Source | None = None) -> None:
    """Attach source location to `err` if not already set.

    Inner specific catches (PropertyAccess, Variable, Operation) win
    over outer wrappers (Declaration, MixinCall) — the more specific
    column matches less.js's reporting better. Now safe to set in
    multiple layers: value AST nodes carry indices anchored to the
    outer source thanks to `parse_value_text(base_offset=...)`.

    `source` overrides `ctx.source` when the index belongs to an
    imported sub-file (recorded on the offending node's `_source`
    attribute by the importer).

    If `err` carries a `_base_index` (set by a parser that knew its
    own anchor — e.g. value-position structural rejections), that
    wins over the caller-supplied `index`.
    """
    if err.location is None:
        eff_source = source if source is not None else ctx.source
        if eff_source is not None:
            base = err._base_index
            anchor = int(base) if base is not None else index
            err.location = eff_source.location_at(anchor)
            if base is not None and not err.snippet:
                err.snippet = eff_source.snippet_around(anchor)


def _propagate_important(node: Node) -> Node:
    """Add `!important` to every declaration in a (possibly nested) tree.
    Used when a mixin call is suffixed with `!important`.
    """
    if isinstance(node, Declaration):
        if node.variable or node.important:
            return node
        return Declaration(
            index=node.index,
            name=node.name,
            value=node.value,
            important=True,
            variable=node.variable,
            merge=node.merge,
            important_origin='source',
        )
    if isinstance(node, Ruleset):
        return Ruleset(
            index=node.index,
            selectors=node.selectors,
            rules=[_propagate_important(r) for r in node.rules],
            root=node.root,
            paths=node.paths,
        )
    return node


def _attach_decl_anchor(err: LessError, d: Declaration, mode: DeclAnchor) -> None:
    """Stamp `_base_index` on `err` so the outer error formatter
    anchors at the declaration-level position less.js uses.

    Modes:
    * `DeclAnchor.DECL_START` — anchor at `d.index` (start of the
      property name; column less.js uses for `Invalid % without number`).
    * `DeclAnchor.VALUE_END`  — anchor at the first source offset past
      the declaration's value text (column less.js uses for an invalid
      hex color that's followed by a trailing comment / `;`).
    """
    if isinstance(d.value, Anonymous):
        value_end = d.value.index + len(d.value.value)
    else:
        value_end = d.index
    err._base_index = value_end if mode is DeclAnchor.VALUE_END else d.index


def _maybe_promote_color(node: Node) -> Node:
    """A `Keyword` that names a CSS color (`red`, `transparent`, ...)
    becomes a `Color` for arithmetic. Mirrors less.js's behavior where
    `Color.fromKeyword(value)` is consulted when an operand isn't already
    a Color.
    """
    if not isinstance(node, Keyword):
        return node
    parsed = parse_color(node.value)
    if parsed is None:
        return node
    rgb, alpha = parsed
    return Color(index=node.index, value=node.value, rgb=rgb, alpha=alpha)


def _dim_to_color(d: Dimension) -> Color:
    """less.js promotion: a bare Dimension in a Color operation becomes a
    grey color (R=G=B=value). Used so `#444 + 1` shifts every channel."""
    return Color(index=d.index, value='', rgb=(d.value, d.value, d.value), alpha=1.0, color_function='')


def _strip_tilde_quotes(text: str) -> str:
    """Scan `text` and replace each `~'…'` / `~"…"` with its body.
    Used by `_eval_atrule_prelude` so interpolated tilde-strings emit
    bare content in `@media`/`@supports` preludes.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '~' and i + 1 < n and text[i + 1] in ('"', "'"):
            q = text[i + 1]
            j = i + 2
            body_start = j
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    j += 2
                    continue
                if text[j] == q:
                    break
                j += 1
            out.append(text[body_start:j])
            i = j + 1 if j < n else j
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _extract_var_name(message: str) -> str:
    """Pull a `@name` token out of an UndefinedNameError message.
    Returns '' when none is found. Used to refine the source column
    reported for prelude/value undefined-variable errors.
    """
    m = _AT_NAME_RE.search(message)
    return m.group(0) if m else ''


# Suppress unused-import warnings: these names are part of the typed
# public surface (callable signatures reference them).
_ = (Call, Expression, MixinCall, Negative, Operation, Paren, Value)
