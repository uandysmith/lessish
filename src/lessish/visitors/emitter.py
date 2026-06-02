"""CSS value-level stringification.

The `Emitter` class round-trips value-level AST nodes back to CSS
text. Two flavours coexist: the shared `_NEUTRAL_EMITTER` (no
`fround`, no strict-units checks) wraps the module-level
`value_to_css` / `_emit_decl_value` / `_format_dimension` helpers used
during eval; `emit_css` builds a per-compile `Emitter(num_precision=8,
strict_units=...)` so the final output rounds Dimensions and raises on
compound units.

`_important_suffix` lives here too — it's a single-declaration
stringification helper that the structure passes (dedup) and the emit
pass both call.
"""

from __future__ import annotations

from ..ast_nodes import (
    Anonymous,
    Call,
    Color,
    Declaration,
    DetachedRuleset,
    Dimension,
    Expression,
    Keyword,
    Lookup,
    MixinCall,
    Negative,
    Node,
    Operation,
    Paren,
    Quoted,
    Url,
    Value,
    Variable,
)
from ..colors import format_color
from ..errors import OperationError


def neutralize_style_breakout(s: str) -> str:
    """CSS-escape any `</` so untrusted Less can't terminate an inlined
    `<style>` element.

    `</style` (the only sequence that ends a `<style>` rawtext element)
    requires the literal `</`. Escaping just the `<` to its six-digit CSS
    escape leaves the byte stream as `\\00003c/style`, which no HTML
    pre-scan treats as an end tag, while a CSS parser decodes `\\00003c`
    back to `<` inside strings/identifiers — so `content: "</p>"`
    round-trips losslessly. Media-query range operators (`width < 600px`),
    `<!--`, and lone `<`/`>` never form `</`, so they are untouched. The
    six-digit form is self-delimiting (no trailing space), so it is also
    safe inside unquoted `url(...)`.
    """
    return s.replace('</', '\\00003c/')


class Emitter:
    """Stringifies value-level AST nodes back to CSS text.

    State (`num_precision`, `strict_units`, `decl_index`) lives on the
    instance so two compiles can run concurrently without racing. The
    module-level `value_to_css(node)` wraps a single immutable
    `_NEUTRAL_EMITTER` (precision=None) for eval-time stringification
    (declaration round-trip, error messages, …); `emit_css` builds a
    configured `Emitter(num_precision=8, strict_units=...)` so
    Dimensions round through `fround` and `strict_units` raises on
    compound units.

    Round-trips literals verbatim where possible; numerical Dimensions
    drop trailing zeros (`1.5px` not `1.500000px`, `10` not `10.0`).
    """

    __slots__ = ('num_precision', 'strict_units', 'decl_index', '_dispatch')

    def __init__(
        self,
        *,
        num_precision: int | None = None,
        strict_units: bool = False,
    ) -> None:
        # `num_precision=None` means "no fround": Dimensions keep full
        # IEEE-754 precision. less.js sets it to 8 only during the CSS
        # emit pass, not during eval-time interpolation.
        self.num_precision = num_precision
        self.strict_units = strict_units
        # Source index of the declaration currently being formatted;
        # used as the anchor when an emit-time error fires inside a
        # Dimension value so the error band points at the property
        # name rather than the inner literal's position.
        self.decl_index: int | None = None
        # Per-instance type → method dispatch; binding methods keeps
        # the lookup one-deep on the hot path.
        self._dispatch: dict[type, object] = {
            Value: self._v_value,
            Expression: self._v_expression,
            Dimension: self._format_dimension,
            Color: self._v_color,
            Keyword: self._v_keyword,
            Variable: self._v_variable,
            Quoted: self._v_quoted,
            Url: self._v_url,
            Call: self._v_call,
            Operation: self._v_operation,
            Negative: self._v_negative,
            Paren: self._v_paren,
            Anonymous: self._v_anonymous,
            DetachedRuleset: self._v_detached_ruleset,
            MixinCall: self._v_mixin_call,
            Lookup: self._v_lookup,
        }

    def to_css(self, node: Node) -> str:
        handler = self._dispatch.get(type(node))
        if handler is not None:
            return handler(node)  # type: ignore[operator,no-any-return]
        return ''

    def emit_decl_value(self, d: Declaration) -> str:
        """Stringify `d.value` with `decl_index` temporarily anchored
        at the declaration. Mirrors less.js's `currentDeclaration`
        attribution: strict-units errors inside the value report
        against the property-name column, not the inner Dimension.
        """
        saved = self.decl_index
        self.decl_index = d.index
        try:
            return self.to_css(d.value)
        finally:
            self.decl_index = saved

    def _v_value(self, node: Value) -> str:
        return ', '.join(self.to_css(e) for e in node.expressions)

    def _v_expression(self, node: Expression) -> str:
        # Default join with space. Suppress only the specific case
        # `Keyword(ident)` followed by `Anonymous([…])` — this is
        # `input[type=text]` parsed as two tokens with no source
        # whitespace, and the attribute bracket must stay tight to the
        # ident. Other adjacencies (Dimension before `[grid-name]`)
        # keep their space because they're CSS-grid line names where
        # the space IS significant.
        values = node.values
        if len(values) == 1:
            return self.to_css(values[0])
        parts: list[str] = []
        prev_v: Node | None = None
        for v in values:
            piece = self.to_css(v)
            if prev_v is not None:
                tight = type(prev_v) is Keyword and type(v) is Anonymous and piece.startswith('[')
                # `@{x}_bar` and similar source forms with no whitespace
                # between adjacent value entities should round-trip
                # tight too; the parser tags such entities with
                # `_glue_to_prev` so we can preserve that here.
                if not tight and v._glue_to_prev:
                    tight = True
                if not tight:
                    parts.append(' ')
            parts.append(piece)
            prev_v = v
        return ''.join(parts)

    def _v_color(self, node: Color) -> str:
        return format_color(node.rgb, node.alpha, node.color_function, node.value)

    def _v_keyword(self, node: Keyword) -> str:
        return node.value

    def _v_variable(self, node: Variable) -> str:
        # Unresolved variable — emit the reference verbatim. Reachable
        # only when the evaluator decided not to resolve (e.g., text
        # contexts already substituted).
        return node.name

    def _v_quoted(self, node: Quoted) -> str:
        # `</style>` neutralisation is applied at the emit sink (see
        # `neutralize_style_breakout`), not here — the sink covers
        # selectors, property names, comments, and at-rule preludes too,
        # not just escaped string values.
        if node.escaped:
            return node.value
        return f'{node.quote}{node.value}{node.quote}'

    def _v_url(self, node: Url) -> str:
        return f'url({node.value})'

    def _v_call(self, node: Call) -> str:
        return f'{node.name}({", ".join(self.to_css(a) for a in node.args)})'

    def _v_operation(self, node: Operation) -> str:
        sep = ' ' if node.is_spaced else ''
        return f'{self.to_css(node.lhs)}{sep}{node.op}{sep}{self.to_css(node.rhs)}'

    def _v_negative(self, node: Negative) -> str:
        return f'-{self.to_css(node.value)}'

    def _v_paren(self, node: Paren) -> str:
        # `~(...)`-marked Parens are structural list constructors, not
        # CSS parens — emit just the inner list text without wrapping.
        if node._tilde_list:
            return self.to_css(node.value)
        return f'({self.to_css(node.value)})'

    def _v_anonymous(self, node: Anonymous) -> str:
        # During emit (`num_precision` active), prefer the structured
        # evaluated value stashed by `eval_declaration` so Dimensions
        # round through `_format_dimension`'s fround branch. The raw
        # `node.value` text was generated at eval time without fround
        # so interpolation contexts see full precision.
        if self.num_precision is not None:
            structured = node._evaled_node
            if structured is not None:
                return self.to_css(structured)
        return node.value

    def _v_detached_ruleset(self, node: DetachedRuleset) -> str:
        # Inline serialisation — used for round-tripping when a detached
        # ruleset stays in a declaration value. Invocation goes through
        # `_invoke_variable_call`, not this path.
        dr_parts: list[str] = []
        for r in node.rules:
            if isinstance(r, Declaration) and not r.variable:
                dr_parts.append(f'{r.name}: {self.to_css(r.value)};')
        return '{ ' + ' '.join(dr_parts) + ' }'

    def _v_mixin_call(self, node: MixinCall) -> str:
        # Value-position captured mixin call that wasn't resolved by a
        # Lookup. Round-trip as source text — `name` uses `>` separators
        # internally for parser convenience; flatten back to its readable
        # form (`#a>b` → `#a.b` for path segments).
        path = _format_namespace_path(node.name)
        # Bare `#lib.colors` (no parens in source) is a namespace alias,
        # not an invocation.
        if node._no_parens and not node.args:
            return path
        if node.args:
            return f'{path}({", ".join(self._format_mixin_arg(a) for a in node.args)})'
        return f'{path}()'

    def _v_lookup(self, node: Lookup) -> str:
        # Unresolved Lookup — only reachable in defensive paths. Emit
        # textually as `<target>[<key>]`.
        return f'{self.to_css(node.target)}[{node.key}]'

    def _format_mixin_arg(self, arg: Node) -> str:
        """Best-effort textual rendering of a MixinArg for value emit."""
        # Use duck-typing to avoid circular import; MixinArg has `name`/`value`.
        name = getattr(arg, 'name', None)
        value = getattr(arg, 'value', None)
        if value is None:
            return ''
        rendered = self.to_css(value)
        if name:
            return f'{name}: {rendered}'
        return rendered

    def _format_dimension(self, d: Dimension) -> str:
        return _format_dimension_impl(d, self)


# Shared immutable emitter for module-level `value_to_css` /
# `_emit_decl_value`. Callers never mutate per-instance state, so
# sharing is safe across threads.
_NEUTRAL_EMITTER: Emitter  # forward declaration — instantiated below.


def value_to_css(node: Node) -> str:
    """Eval-time stringification of a value node. Uses a neutral
    Emitter (no fround, no strict-units, no decl-index attribution) —
    appropriate for declaration round-trip text, error messages,
    interpolation, etc. The emit pass uses its own configured Emitter
    instance directly.
    """
    return _NEUTRAL_EMITTER.to_css(node)


def _format_namespace_path(name: str) -> str:
    return name


def _format_mixin_arg(arg: Node) -> str:
    return _NEUTRAL_EMITTER._format_mixin_arg(arg)


def _format_dimension(d: Dimension) -> str:
    return _format_dimension_impl(d, _NEUTRAL_EMITTER)


def _emit_decl_value(d: Declaration) -> str:
    """Used by `dedup_declarations` and emit paths that don't have
    a configured `Emitter` on hand. `decl_index` attribution is a
    no-op on the neutral emitter — these callers never raise
    strict-units errors mid-format.
    """
    return _NEUTRAL_EMITTER.emit_decl_value(d)


def _format_dimension_impl(d: Dimension, emitter: Emitter) -> str:
    """Format a Dimension as CSS text. Integers omit the fractional part;
    fractional values round to 6 decimal places and strip trailing
    zeros. Negative zero collapses to `0`.

    Compound units (`px*em`, `px/em`) have no CSS spelling — emitting
    one raises in strict-units mode. Mirrors less.js: `Multiple units in
    dimension. Correct the units or use the unit function. Bad unit:
    <unit>`. In non-strict mode less.js falls back to the unit's
    `backupUnit` (or first denominator), so we do the same.
    """
    # Mirror less.js `Unit#genCSS`:
    #   numerator.length == 1            → emit numerator[0]
    #   else, !strict and backupUnit     → emit backupUnit
    #   else, !strict and denominator    → emit denominator[0]
    #   else (strict and !singular)      → raise OperationError
    unit_text = d.unit
    if '/' in unit_text:
        num_part, den_part = unit_text.split('/', 1)
    else:
        num_part, den_part = unit_text, ''
    numer = num_part.split('*') if num_part else []
    denom = den_part.split('*') if den_part else []
    is_singular = len(numer) <= 1 and not denom
    if emitter.strict_units and not is_singular:
        err = OperationError(
            f'Multiple units in dimension. Correct the units or use the unit function. Bad unit: {d.unit}'
        )
        # Anchor at the enclosing declaration (matches less.js, which
        # reports the property-name column, not the inner Dimension's
        # source position). Fall back to the Dimension's own index if
        # the formatter ran outside a declaration context.
        err._base_index = emitter.decl_index if emitter.decl_index is not None else d.index
        raise err
    backup = d._backup_unit if d._backup_unit is not None else ''
    # less.js's `tree/unit.js#genCSS`:
    #   numerator.length === 1                 → emit numerator[0]
    #   else, !strictUnits and backupUnit      → emit backupUnit
    #   else, !strictUnits and denominator.len → emit denominator[0]
    #   else                                   → emit nothing (unitless)
    # In strict mode the backup/denom fallbacks are skipped — a
    # genuinely unitless Dim(1, '') emits `1`, not `1px` from a
    # carried backup.
    if len(numer) == 1:
        unit_text = numer[0]
    elif not emitter.strict_units and backup:
        unit_text = backup
    elif not emitter.strict_units and denom:
        unit_text = denom[0]
    else:
        unit_text = ''
    val = d.value
    # less.js `fround(context, value)` mirror — applied only when the
    # emit-time context has `numPrecision` set (true during emit_css,
    # not during eval-time interpolation). Mirrors less.js's
    # parse-tree.js fround context flag.
    num_precision = emitter.num_precision
    if num_precision is not None and isinstance(val, float):
        val = float(f'{val + 2e-16:.{num_precision}f}')
    # Clamp FP noise (`rotate(-0.0000000001deg)` source → `-1e-10deg`
    # without this; less.js's trig and toFixed(10) cleanup also collapse
    # sub-nano values to zero).
    if isinstance(val, float) and abs(val) < 1e-9:
        val = 0
    if val == 0:
        text = '0'
    elif val == int(val):
        text = str(int(val))
    else:
        # less.js prints numbers via JavaScript's `Number.toString()`,
        # which keeps full IEEE-754 precision (up to ~17 significant
        # digits) without trailing zeros. Match by using Python's
        # `repr(float)` and trimming the leading sign as needed.
        text = repr(val)
    return text + unit_text


_NEUTRAL_EMITTER = Emitter()


def _important_suffix(d: Declaration, compress: bool) -> str:
    """less.js's default emission inserts a space before `!important`
    (`red !important`). The no-space form (`red!important`) is reserved
    for declarations that have been `$prop`-accessed from a sibling —
    that quirk is tracked via `important_origin == 'accessed'`. The
    `lookup` origin (propagated from a `$prop`/`@var` value) keeps the
    default space.

    Source-parsed declarations carry the exact whitespace via
    `important_prefix_ws` so `position: relative  !important` (uikit
    style — two spaces) round-trips verbatim. Programmatically-built
    Declarations leave the default `' '` and get the standard spacing.

    `compress=True` always drops the space.
    """
    if compress:
        return '!important'
    if d.important_origin == 'accessed':
        return '!important'
    return d.important_prefix_ws + '!important'
