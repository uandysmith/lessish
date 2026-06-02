"""Selector interpolation pass.

`_eval_selector` resolves `@{name}` / `${name}` interpolations inside
each element, then post-splits the combined text on top-level commas
so an interpolated list (`@{inputs}` → `a, b`) becomes one Selector
per piece.
"""

from __future__ import annotations

from ..ast_nodes import Element, Selector
from ..context import EvalContext
from ..visitors import selector_to_str
from .atrules import _split_top_level_commas


def _eval_selector(sel: Selector, ctx: EvalContext) -> list[Selector]:

    # Selectors keep quoted values verbatim: `[data=@{x}]` with
    # `@x: "test"` interpolates as `[data="test"]` (less.js convention).
    new_elements = [
        Element(index=e.index, combinator=e.combinator, value=ctx.substitute_text(e.value, strip_quotes=False))
        for e in sel.elements
    ]
    # Track whether any element carried `@{var}` / `${prop}` interpolation
    # in the source. less.js's extend pass treats interpolated extenders
    # as opaque (no extend fires) — likely a quirk from binding the
    # extender's element list before the interpolation pass. Mirror it
    # via this marker; `_collect_extends` consults it to skip the extend.
    had_interpolation = any('@{' in e.value or '${' in e.value for e in sel.elements)
    # Post-interpolation comma split: when an interpolated variable
    # resolves to a comma-list (`@{inputs}` → `input[type=text], textarea`),
    # the selector splits into one Selector per piece. The split happens
    # against the *combined* element text so a prefix/suffix element
    # (`.d@{classes}&:hover` → `.d.a, .b, .c&:hover`) attaches only to
    # the first/last piece, matching less.js. Only the resulting flat
    # text of each piece is captured; structural element boundaries are
    # collapsed but the joiner / `&`-resolver works off text anyway.
    new_sel = Selector(index=sel.index, elements=new_elements, extend_list=sel.extend_list)
    if had_interpolation:
        new_sel._had_interpolation = True
    combined = selector_to_str(new_sel)
    if ',' not in combined:
        return [new_sel]
    pieces = _split_top_level_commas(combined)
    if len(pieces) <= 1:
        return [new_sel]
    out_sels: list[Selector] = []
    for p in pieces:
        s = Selector(
            index=sel.index,
            elements=[Element(index=sel.index, combinator='', value=p)],
            extend_list=sel.extend_list,
        )
        if had_interpolation:
            s._had_interpolation = True
        out_sels.append(s)
    return out_sels
