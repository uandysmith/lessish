"""Declaration evaluation + value-text classification helpers.

`eval_declaration` is the entry point. For value declarations it
re-parses the raw value text into a Value AST, evaluates it, and
re-serializes — except for a fast-path that round-trips static literal
values verbatim so multi-line / trailing-whitespace formatting (and
inline `/* … */` comments) survive intact. The helpers in this module
all support that fast-path classifier or the slow-path's post-
processing (trailing-comment splicing, important promotion, etc.).
"""

from __future__ import annotations

import re

from .._strutil import has_block_comment as _has_preservable_comment
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
    PropertyAccess,
    Quoted,
    Url,
    Value,
    Variable,
)
from ..context import EvalContext, _contains_dr
from ..errors import EvalError, LessError, ParseError, UndefinedNameError
from ..parser import parse_value_text
from ..visitors import value_to_css
from .helpers import _attach_decl_anchor, _extract_var_name, _locate
from .values import eval_call, eval_value

# Pre-compiled value-text scanners. Each fires on every declaration
# whose value is re-emit-eligible — pulling them out of the hot loop
# saves the regex-compile cost per call.
_REDUNDANT_DECIMAL_ZERO_RE = re.compile(r'\d\.\d*0(?!\d)')
_HIGH_PRECISION_NUMBER_RE = re.compile(r'\d\.\d{9,}')
_MISSING_LEADING_ZERO_RE = re.compile(r'(?<![\w.])\.\d')


def _eagerly_eval_calls_in_variable(d: Declaration, ctx: EvalContext) -> None:
    """Mirror less.js's eager-eval of variable RHSs for Call nodes only.

    Walks the parsed value AST for Call entries and evaluates each one
    in isolation. Forward variable references (`@b: @a;` where `@a` is
    declared later) stay lazy — we swallow `UndefinedNameError` raised
    when a Variable arg can't be resolved. Hard errors (`Argument
    cannot be evaluated to a color`, etc.) propagate so misuse surfaces
    at declaration time even when the variable is never referenced.
    """
    if not isinstance(d.value, Anonymous):
        return
    raw = d.value.value.lstrip()
    if '(' not in raw:
        return  # no possible Call node
    base = d.value.index
    try:
        parsed = parse_value_text(raw, base_offset=base)
    except (ParseError, LessError):
        return  # unparseable; let lazy lookup raise the right error later
    calls: list[Call] = []
    _collect_calls(parsed, calls)
    if not calls:
        return
    decl_id = id(d)
    ctx._current_decl_ids.add(decl_id)
    try:
        for c in calls:
            try:
                eval_call(c, ctx)
            except UndefinedNameError:
                # Forward references stay lazy — wait for actual lookup.
                pass
            except LessError as e:
                # Anchor at the declaration position if the inner layer
                # didn't (matches less.js's reported column for the
                # offending variable's value).
                _locate(e, d.index, ctx)
                raise
    finally:
        ctx._current_decl_ids.discard(decl_id)


def _collect_calls(node: Node, out: list[Call]) -> None:
    """Walk a value-level AST collecting Call nodes for eager-eval.
    Does NOT recurse into Call.args (each Call is eval'd whole, which
    will recursively evaluate its own args)."""
    if isinstance(node, Call):
        out.append(node)
        return
    for attr in ('expressions', 'values', 'value', 'lhs', 'rhs'):
        child = getattr(node, attr, None)
        if isinstance(child, Node):
            _collect_calls(child, out)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, Node):
                    _collect_calls(item, out)


def eval_declaration(d: Declaration, ctx: EvalContext) -> Declaration:
    """For value declarations: parse the raw value text into a Value AST,
    evaluate it, re-serialize back to `Anonymous` text. Variable
    declarations pass through (filtered at emit time).

    Fast-path: when the value text contains nothing dynamic (no `@`,
    no `$`, no top-level operators, no function call) we round-trip
    the raw text verbatim. This preserves inline comments like
    `linear-gradient(#333 /*hint*/, #111)` that the value parser would
    otherwise drop as trivia.
    """
    if d.variable:
        _eagerly_eval_calls_in_variable(d, ctx)
        return d

    decl_id = id(d)
    ctx._current_decl_ids.add(decl_id)
    try:
        return _eval_declaration_inner(d, ctx)
    except UndefinedNameError as e:
        # If the undefined name appears inside a `@{name}` / `${name}`
        # interpolation in the property NAME (e.g. `outline-@{color}:`),
        # less.js reports the `@` of the interpolation, not the start
        # of the declaration. Refine the column by searching the source.
        if e.location is None and ctx.source is not None:
            name = _extract_var_name(e.message)
            if name:
                # Look for `@{name}` first (interpolation form), then
                # bare `@name` — whichever appears earliest in the
                # declaration's source span.
                interp = ctx.source.text.find('@{' + name[1:] + '}', d.index)
                bare = ctx.source.text.find(name, d.index)
                hits = [h for h in (interp, bare) if h != -1]
                if hits:
                    _locate(e, min(hits), ctx)
                    raise
        _locate(e, d.index, ctx)
        raise
    except LessError as e:
        _locate(e, d.index, ctx)
        raise
    finally:
        ctx._current_decl_ids.discard(decl_id)


def _eval_declaration_inner(d: Declaration, ctx: EvalContext) -> Declaration:
    # The trampoline (`_drive.drive`) only steps per yielded cycle, so a
    # single large flat block of declarations never reaches it. Check
    # here too so the wall-clock budget bounds mixin-free workloads.
    if ctx._deadline is not None:
        ctx.check_deadline('evaluating declarations')
    raw_unstripped = d.value.value if isinstance(d.value, Anonymous) else ''
    leading_newline = _starts_with_newline(raw_unstripped)
    raw = raw_unstripped.lstrip()
    base = d.value.index if isinstance(d.value, Anonymous) else 0
    important = d.important
    important_origin = d.important_origin
    evaled_node: Node | None = None
    # CSS custom properties (`--name: …;`) accept arbitrary value text
    # including unbalanced braces and arrow-function syntax. Default path
    # is `substitute_text` — keeps `(*, *, iostat=1)` (Fortran-style) and
    # arrow-function bodies verbatim while resolving `@var` / `@{var}`.
    # less.js *does* still evaluate well-known Less function calls within
    # the value (`rgba(...)`, `lighten(...)`), so attempt structured eval
    # when the value cleanly tokenises into a single registered-function
    # Call. The probe is narrow on purpose: anything else (Fortran args,
    # arrow funcs) falls through to verbatim substitution.
    if d.name.startswith('--'):
        new_value_text = _try_eval_custom_property(raw, base, ctx)
        # `--@{key}: …` — property-name interpolation works for
        # custom properties too. `bare_vars=False` keeps a bare
        # `@something` from being substituted out of a literal `--`
        # identifier; only `@{…}` / `${…}` interpolations expand.
        new_name = ctx.substitute_text(d.name, bare_vars=False) if '@{' in d.name or '${' in d.name else d.name
        out_d = Declaration(
            index=d.index,
            name=new_name,
            value=Anonymous(index=d.value.index, value=new_value_text),
            important=important,
            variable=d.variable,
            merge=d.merge,
            important_origin=important_origin,
            important_prefix_ws=d.important_prefix_ws,
        )
        src = d._source
        if src is not None:
            out_d._source = src
        return out_d
    if _has_preservable_comment(raw):
        # Values with inline `/* ... */` comments preserve the comment
        # but try to evaluate the surrounding expression. We peel the
        # TRAILING block comment off, eval the rest, then re-attach the
        # comment to the result. For comments that aren't trailing
        # (mid-value), fall back to verbatim text — the structured value
        # parser would treat them as trivia and drop them.
        body, trailing_comment = _split_trailing_block_comment(raw)
        if trailing_comment is not None and '/*' not in body:
            try:
                parsed = parse_value_text(body, base_offset=base)
                evaled = eval_value(parsed, ctx)
                if _value_carries_lookup_important(evaled):
                    important = True
                    if not important_origin:
                        important_origin = 'lookup'
                new_value_text = value_to_css(evaled) + ' ' + trailing_comment
            except (ParseError, LessError) as pe:
                # Structural rejections from the value parser
                # (`_base_index` set, or `_propagate` marker) need to
                # surface even when the value carried a trailing
                # comment — the comment branch otherwise falls back to
                # verbatim text and silently masks the error.
                if pe._base_index is not None:
                    raise
                mode = pe._propagate
                if mode is not None:
                    _attach_decl_anchor(pe, d, mode)
                    raise
                new_value_text = raw
        else:
            new_value_text = raw
    else:
        try:
            anon = d.value if isinstance(d.value, Anonymous) else None
            cached_ast: Value | None = anon._parsed_ast if anon is not None else None
            if cached_ast is None:
                parsed = parse_value_text(raw, base_offset=base)
                if anon is not None:
                    anon._parsed_ast = parsed
            else:
                parsed = cached_ast
            if _can_emit_verbatim(parsed, raw, leading_newline=leading_newline):
                # Static literal whose source spelling already matches
                # less.js output — round-trip verbatim to preserve
                # whitespace (multi-line, trailing space). See
                # `_can_emit_verbatim` for the full eligibility contract.
                new_value_text = raw
            else:
                # `font` shorthand: less.js temporarily switches
                # `math: always` → `math: parens-division` so
                # `font-size/line-height` keeps the literal `/`
                # (matches the CSS shorthand syntax). See
                # less.js tree/declaration.js (`name === 'font' &&
                # context.math === MATH.ALWAYS`).
                if d.name == 'font' and ctx.math == 'always':
                    with ctx.math_mode('parens-division'):
                        evaled = eval_value(parsed, ctx)
                else:
                    evaled = eval_value(parsed, ctx)
                # A DetachedRuleset cannot serve as a property value —
                # less.js raises `Rulesets cannot be evaluated on a
                # property.` at the declaration's position.
                if _contains_dr(evaled):
                    err = EvalError('Rulesets cannot be evaluated on a property.')
                    err._less_js_name = 'SyntaxError'
                    raise err
                # If any value-tree node carries `_lookup_important` (set
                # by eval_lookup when the target captured `!important`),
                # promote this declaration to `!important`.
                if _value_carries_lookup_important(evaled):
                    important = True
                    if not important_origin:
                        important_origin = 'lookup'
                new_value_text = value_to_css(evaled)
                evaled_node = evaled
        except ParseError as pe:
            # Structural rejections (`@a()` w/o lookup, etc.) carry
            # `_base_index` set by the parser and should propagate; the
            # text-substitution fallback exists only for unparseable
            # value-position constructs like ie-filter args that the
            # structured parser refuses to tokenise.
            if pe._base_index is not None:
                raise
            mode = pe._propagate
            if mode is not None:
                # `_propagate` errors get their anchor injected here so
                # the column matches less.js's reporting:
                #   * `decl_start` — start of the declaration name.
                #   * `value_end`  — first source position past the value
                #                    text (typically the trailing `;`).
                _attach_decl_anchor(pe, d, mode)
                raise
            new_value_text = ctx.substitute_text(raw)
    value_anon = Anonymous(index=d.value.index, value=new_value_text)
    # Attach the structured evaluated value so emit-time `value_to_css`
    # can re-format Dimensions with `fround` (less.js's numPrecision=8)
    # applied. The stored text stays at full precision so variable
    # interpolation (`@{p}` in property names, strings) doesn't see the
    # rounded form — mirroring less.js's distinction between the eval
    # context (no numPrecision) and the emit context.
    if evaled_node is not None:
        value_anon._evaled_node = evaled_node
    out_d = Declaration(
        index=d.index,
        name=ctx.substitute_text(d.name, bare_vars=False),
        value=value_anon,
        important=important,
        variable=d.variable,
        merge=d.merge,
        important_origin=important_origin,
        important_prefix_ws=d.important_prefix_ws,
    )
    src = d._source
    if src is not None:
        out_d._source = src
    return out_d


def _try_eval_custom_property(raw: str, base: int, ctx: EvalContext) -> str:
    """Structured eval for a custom-property value.

    Bails to `substitute_text` for arrow-function literals (`() => {…}`)
    and other exotic syntax that doesn't round-trip cleanly through the
    structured parser. Otherwise attempts structured parse + eval; the
    `@var lighten(red, 10%)` mixed shape, single calls, and bare
    variable refs all evaluate this way. On parse or eval failure the
    raw `substitute_text` form is returned so partial-Less values never
    surface as exceptions.
    """
    if _has_non_less_value_syntax(raw):
        return ctx.substitute_text(raw)
    # Trailing-comment preservation: less.js normalises `a/* x */` →
    # `a /* x */` (single space before inline comments). Peel the
    # comment off, evaluate the body, re-attach with a space — matches
    # the `_has_preservable_comment` logic used for non-custom decls.
    body, trailing_comment = _split_trailing_block_comment(raw)
    if trailing_comment is not None and '/*' not in body:
        body_stripped = body.rstrip()
        try:
            parsed_body = parse_value_text(body_stripped, base_offset=base)
            evaled_body = eval_value(parsed_body, ctx)
            return value_to_css(evaled_body) + ' ' + trailing_comment
        except (ParseError, LessError):
            return ctx.substitute_text(raw)
    try:
        parsed = parse_value_text(raw, base_offset=base)
    except ParseError:
        return ctx.substitute_text(raw)
    # `~"..."` / `~'...'` escape strings need the structured eval path
    # so the wrapper is unwrapped at emit (`~'calc(-1 * @{x})'` →
    # `calc(-1 * 10px)`). `substitute_text` would keep the `~'...'`
    # spelling. Same for non-evaluable nodes — fall back to the cheap
    # text-substitution path.
    has_escape_string = '~"' in raw or "~'" in raw
    if not _value_has_evaluable_node(parsed) and not has_escape_string:
        # Pure literal text — substitute_text round-trips identically and
        # is cheaper. Avoids touching values that the structured emitter
        # would re-spell.
        return ctx.substitute_text(raw)
    try:
        evaled = eval_value(parsed, ctx)
    except LessError:
        return ctx.substitute_text(raw)
    return value_to_css(evaled)


def _has_non_less_value_syntax(text: str) -> bool:
    """True for values that contain syntax the structured parser would
    mangle: arrow functions (`=>`) and bare `=` (Fortran-style named
    args like `iostat=1`). Less values don't use `=` outside guard
    conditions; spotting one at value position is a reliable signal
    that the text should be kept verbatim.
    """
    if '=>' in text:
        return True
    # Scan for a `=` that isn't part of a comparison operator (`<=`,
    # `>=`, `==`, `!=`, `=<`, `=>`). Skip the contents of string
    # literals so `"key=val"` doesn't trip the heuristic.
    in_string: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string is not None:
            if ch == '\\' and i + 1 < len(text):
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_string = ch
            i += 1
            continue
        if ch == '=':
            prev = text[i - 1] if i > 0 else ''
            nxt = text[i + 1] if i + 1 < len(text) else ''
            if prev in ('<', '>', '=', '!') or nxt in ('<', '>', '='):
                i += 1
                continue
            return True
        i += 1
    return False


def _value_has_evaluable_node(value: Value) -> bool:
    """True if the parsed Value tree contains anything worth evaluating
    (Variable, PropertyAccess, Call, MixinCall, Operation, Negative, or
    a wrapped Expression containing one). Pure literal sequences (just
    Keywords / Dimensions / Anonymous text) don't need structured eval.
    """

    def walk(n: Node) -> bool:
        if isinstance(n, Variable | PropertyAccess | Call | MixinCall | Operation | Negative):
            return True
        for attr in ('expressions', 'values', 'args'):
            children = getattr(n, attr, None)
            if isinstance(children, list):
                for c in children:
                    if isinstance(c, Node) and walk(c):
                        return True
        inner = getattr(n, 'value', None)
        if isinstance(inner, Node) and walk(inner):
            return True
        return False

    return walk(value)


def _starts_with_newline(text: str) -> bool:
    """True iff the leading whitespace of `text` contains a `\\n`.
    `prop:\\n    value` triggers re-emit (collapse), while
    `prop: value\\n    cont` preserves the source layout.
    """
    for ch in text:
        if ch == '\n':
            return True
        if not ch.isspace():
            return False
    return False


def _has_redundant_decimal_zero(text: str) -> bool:
    """True iff `text` contains a numeric literal with trailing `.0`s
    that less.js would normalise away (`1.0` → `1`, `1.50` → `1.5`).
    Triggers a re-emit so the Dimension serialiser drops the zeros.
    """
    return _REDUNDANT_DECIMAL_ZERO_RE.search(text) is not None


def _has_high_precision_number(text: str) -> bool:
    """True iff `text` contains a numeric literal with more than 8
    fractional digits — less.js's `numPrecision=8` rounds it at emit.
    Triggers a re-emit so the Dimension serialiser applies fround.
    """
    return _HIGH_PRECISION_NUMBER_RE.search(text) is not None


def _has_missing_leading_zero(text: str) -> bool:
    """True iff `text` contains a numeric literal like `.5em` that's
    missing a leading zero — less.js normalises to `0.5em` at emit.
    Triggers a re-emit so the Dimension serialiser adds the zero.
    """
    return _MISSING_LEADING_ZERO_RE.search(text) is not None


def _has_comma_without_space(text: str) -> bool:
    """True iff `text` contains a top-level `,` that isn't followed by
    whitespace. Used to force re-emit through `value_to_css` so the
    comma-list separator normalises to `, ` (less.js convention).
    Skips commas inside strings and parens since those are function-arg
    territory, not value-level list separators.
    """
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            j = i + 1
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    j += 2
                    continue
                if text[j] == ch:
                    j += 1
                    break
                j += 1
            i = j
            continue
        if ch == '(':
            depth += 1
            i += 1
            continue
        if ch == ')':
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch == ',' and depth == 0:
            if i + 1 < n and text[i + 1] not in (' ', '\t', '\n'):
                return True
        i += 1
    return False


def _value_contains_url(value: Value) -> bool:
    """True iff the parsed Value tree contains a `url(...)` node. less.js
    folds multi-line `background: url(...) center;` onto a single line
    (the URL gets parsed structurally, and the structured emit drops
    inter-token newlines); plain keyword-only multi-line values like
    `red\\n    blue` are preserved by both. This check pairs with a
    `'\\n' in raw` guard to force re-emit only for the URL case.
    """

    def walk(n: Node) -> bool:
        if isinstance(n, Url):
            return True
        for attr in ('expressions', 'values', 'args'):
            children = getattr(n, attr, None)
            if isinstance(children, list):
                for c in children:
                    if isinstance(c, Node) and walk(c):
                        return True
        inner = getattr(n, 'value', None)
        if isinstance(inner, Node) and walk(inner):
            return True
        return False

    return walk(value)


def _has_unindented_continuation(text: str) -> bool:
    """True iff any continuation line (after a `\\n`) starts at column
    zero (no leading whitespace). less.js folds these into a single
    line; only properly-indented continuations preserve the layout.
    """
    if '\n' not in text:
        return False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\n' and i + 1 < n and not text[i + 1].isspace():
            return True
        i += 1
    return False


def _is_literal_only(value: Value) -> bool:
    """True when a parsed `Value` tree contains only literal nodes —
    Dimension, Color, Keyword, Url, Anonymous, and Quoted strings WITHOUT
    `@{var}` / `${prop}` interpolation. No Variables, Calls, Operations,
    or other dynamic nodes. Used by `_eval_declaration_inner` to
    round-trip static value text verbatim so multi-line /
    trailing-whitespace formatting survives.
    """

    def is_literal(node: Node) -> bool:
        if isinstance(node, (Dimension, Color, Keyword, Anonymous, Url)):
            # ie-filter Anonymous values carry a normalized text and
            # need the structured emit path (not raw source) so the
            # whitespace cleanup actually shows up.
            if isinstance(node, Anonymous) and node._ie_filter:
                return False
            if isinstance(node, Anonymous) and ('@{' in node.value or '${' in node.value):
                return False
            # `url(@var)` / `url(@{var})` needs eval to substitute the
            # variable; the verbatim round-trip path would emit `@var`
            # literally.
            if isinstance(node, Url) and ('@' in node.value or '${' in node.value):
                return False
            return True
        if isinstance(node, Quoted):
            # Strings with `@{name}` / `${name}` need eval to substitute.
            return '@{' not in node.value and '${' not in node.value
        if isinstance(node, Negative):
            return is_literal(node.value)
        return False

    for expr in value.expressions:
        for v in expr.values:
            if not is_literal(v):
                return False
    return True


def _can_emit_verbatim(parsed: Value, raw: str, *, leading_newline: bool) -> bool:
    """Decide whether a declaration's raw source text can be re-emitted
    byte-for-byte instead of round-tripping through the evaluator.

    The verbatim path preserves whitespace details (multi-line layouts,
    trailing spaces, exact comma spacing) that re-serialization would
    normalise. It is only safe when the value is a static literal whose
    source spelling already matches what less.js would emit. Each clause
    below rules out a case where less.js would rewrite the text:

    * `_is_literal_only` — no Variables / Calls / Operations to evaluate.
    * `leading_newline` — `prop:\n  value`; less.js folds the newline.
    * `_has_unindented_continuation` — `45\n-23`; less.js folds
      non-indented continuation lines (only indented ones survive).
    * `_has_comma_without_space` — `'a','b'`; less.js normalises to `, `.
    * `_has_redundant_decimal_zero` / `_has_high_precision_number` /
      `_has_missing_leading_zero` — number spellings less.js rewrites
      (`1.50`→`1.5`, precision clamp, `.5`→`0.5`).
    * `~"…"` / `~'…'` — escaped strings need eval to unwrap.
    * `_value_contains_url(...) and '\\n' in raw` — a multi-line `url(...)`
      is reflowed by less.js.

    Keep this as the single home for the verbatim-eligibility contract:
    new edge cases get one more clause here, not a scattered check.
    """
    return (
        _is_literal_only(parsed)
        and not leading_newline
        and not _has_unindented_continuation(raw)
        and not _has_comma_without_space(raw)
        and not _has_redundant_decimal_zero(raw)
        and not _has_high_precision_number(raw)
        and not _has_missing_leading_zero(raw)
        and '~"' not in raw
        and "~'" not in raw
        and not (_value_contains_url(parsed) and '\n' in raw)
    )


def _split_trailing_block_comment(text: str) -> tuple[str, str | None]:
    """If `text` ends with `... /* ... */` (optionally followed by
    whitespace), return (body_without_comment, comment_text). Otherwise
    returns (text, None). Strings are respected — a `*/` inside a quoted
    string doesn't close a comment.
    """
    stripped = text.rstrip()
    if not stripped.endswith('*/'):
        return text, None
    # Find the matching `/*` walking backwards; verify it isn't inside a
    # string by scanning forward from `/*` to `*/`.
    end_close = len(stripped) - 2
    open_at = stripped.rfind('/*', 0, end_close)
    if open_at == -1:
        return text, None
    # Confirm no quote opens between [0, open_at) without closing.
    inside_string = False
    quote = ''
    i = 0
    while i < open_at:
        ch = stripped[i]
        if inside_string:
            if ch == '\\' and i + 1 < open_at:
                i += 2
                continue
            if ch == quote:
                inside_string = False
            i += 1
            continue
        if ch in ('"', "'"):
            inside_string = True
            quote = ch
        i += 1
    if inside_string:
        return text, None
    return stripped[:open_at].rstrip(), stripped[open_at:]


def _value_carries_lookup_important(node: Node) -> bool:
    """Walk a Value tree; True if any node has the `_lookup_important`
    marker set by eval_lookup.
    """
    if node._lookup_important:
        return True
    if isinstance(node, Value):
        return any(_value_carries_lookup_important(e) for e in node.expressions)
    if isinstance(node, Expression):
        return any(_value_carries_lookup_important(v) for v in node.values)
    if isinstance(node, Operation):
        return _value_carries_lookup_important(node.lhs) or _value_carries_lookup_important(node.rhs)
    if isinstance(node, Negative):
        return _value_carries_lookup_important(node.value)
    if isinstance(node, Paren):
        return _value_carries_lookup_important(node.value)
    if isinstance(node, Call):
        return any(_value_carries_lookup_important(a) for a in node.args)
    return False
