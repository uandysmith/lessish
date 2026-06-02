"""Value-level evaluation: expressions, operations, calls, parens, etc.

This module owns the bulk of the math + value-tree machinery — the
public surface is `eval_value`, `eval_expression`, `eval_operation_inner`,
`eval_call`, `eval_paren`, `eval_negative`, `eval_quoted`, `eval_url`,
and the `if()` / `boolean()` / `isdefined()` special forms.

`_unwrap_single` (used everywhere as the canonical Value/Expression
flattener) lives here because it's a pure value-tree helper. Same for
the unit-arithmetic primitives (`_multiply_units`, `_divide_units`,
`_split_unit`, `_cancel`, `_format_unit`) and the `_fround` /
`_dim_backup` / `_set_backup_unit` Dimension support.

The mixin-invocation budget constants (`MIXIN_DEPTH_LIMIT`,
`MIXIN_TOTAL_LIMIT`) and the `_CALC_LIKE_FUNCTIONS` constant set live
here too — `_invoke_mixin_call` reads the limits via the package
module so tests that monkey-patch them see the change at runtime.
"""

from __future__ import annotations

from ..ast_nodes import (
    Anonymous,
    Call,
    Color,
    Condition,
    Dimension,
    Expression,
    Keyword,
    Negative,
    Node,
    Operation,
    Paren,
    Quoted,
    Url,
    Value,
    Variable,
)
from ..context import EvalContext
from ..errors import ArgumentError, EvalError, OperationError, UndefinedNameError, UnsupportedFeatureError
from ..functions import lookup
from ..units import convert, convertible
from .dispatch import eval_node
from .helpers import _dim_to_color, _locate, _maybe_promote_color

# `calc(...)`: operand math is preserved verbatim rather than folded.
# Variables inside still resolve. Mirrors less.js's `in_calc` flag.
_CALC_LIKE_FUNCTIONS = frozenset({'calc'})

# Hard caps on `_invoke_mixin_call` to prevent silent hangs. `DEPTH`
# catches straight runaway recursion (`.x() { .x(); } .x();`) — less.js
# has no numeric mixin limit and relies on the JS stack, so 1000 mirrors
# its effective ceiling. `TOTAL` catches exponential expansion via
# cross-invocation matching (each invocation multiplies matches → output
# grows multiplicatively even at shallow depth); it keeps ~10× headroom
# over the heaviest real-world corpus compile (Bootstrap v3 / UIkit issue
# a few thousand invocations). The exponential case can still burn a few
# seconds before the count trips, so an embedder compiling untrusted Less
# should pair it with the opt-in `max_eval_seconds` wall-clock budget,
# which interrupts the (Python-level) expansion regardless of count.
# Embedders override either via the `mixin_total_limit` /
# `mixin_depth_limit` compile options (threaded onto `EvalContext`);
# these module constants are the default and the monkeypatch point for
# tests.
MIXIN_DEPTH_LIMIT = 1000
MIXIN_TOTAL_LIMIT = 50_000


def eval_quoted(q: Quoted, ctx: EvalContext) -> Quoted:
    """Substitute `@{name}` references inside a string. less.js
    interpolates `@{name}` regardless of whether the string is escaped
    (`~"..."`), but does NOT substitute bare `@name` references inside
    a string — `~'@foo'` evaluates to the literal `@foo`, not the
    value of `@foo`.
    """
    return Quoted(
        index=q.index,
        quote=q.quote,
        value=ctx.substitute_text(q.value, bare_vars=False),
        escaped=q.escaped,
    )


def eval_url(u: Url, ctx: EvalContext) -> Url:
    """Resolve `@var` / `@{var}` references inside a `url(...)` literal.
    less.js evaluates the URL contents like any other value expression,
    so `url(@a)` where `@a: 'Trebuchet'` emits `url('Trebuchet')`.
    """
    return Url(index=u.index, value=ctx.substitute_text(u.value))


def eval_value(v: Value, ctx: EvalContext) -> Value:
    return Value(
        index=v.index,
        expressions=[eval_expression(e, ctx) for e in v.expressions],
    )


def eval_expression(e: Expression, ctx: EvalContext) -> Expression:
    return Expression(
        index=e.index,
        values=[eval_node(v, ctx) for v in e.values],
    )


def eval_operation_inner(op: Operation, ctx: EvalContext) -> Node:
    """Evaluate operands and, when both sides reduce to Dimensions,
    fold the operation. Division (`/`) only folds inside parentheses
    (PARENS_DIVISION default); outside it stays as Operation so
    `font: 12px/14px` round-trips.

    Mixed Color/Dimension promotes the Dimension to a grey Color via
    less.js's `Dimension.toColor()` convention (value * 3). Color +
    Color operates per-channel with alpha blending.
    """
    lhs = _unwrap_single(eval_node(op.lhs, ctx))
    rhs = _unwrap_single(eval_node(op.rhs, ctx))
    if ctx.in_calc:
        # Inside `calc(...)`: substitute variables but never fold ops.
        # less.js calls this PARENS_DIVISION but it applies to every
        # operator when in_calc — `calc(100px * 2)` must stay literal.
        return Operation(index=op.index, op=op.op, lhs=lhs, rhs=rhs, is_spaced=op.is_spaced)
    # Honour the `math` option:
    #   `parens-division` (default) — `/` only folds inside `(...)`.
    #   `parens`                    — every operator only inside `(...)`.
    #   `always`                    — fold everything everywhere.
    # `./` (less.js v3-era explicit division) always folds regardless
    # of math mode. Normalise to `/` so downstream Dimension/Color
    # arithmetic treats it like a regular division.
    if op.op == './':
        op = Operation(index=op.index, op='/', lhs=lhs, rhs=rhs, is_spaced=op.is_spaced)
    elif ctx.math == 'parens' and not ctx.in_parens:
        return Operation(index=op.index, op=op.op, lhs=lhs, rhs=rhs, is_spaced=op.is_spaced)
    elif ctx.math == 'parens-division' and op.op == '/' and not ctx.in_parens:
        return Operation(index=op.index, op=op.op, lhs=lhs, rhs=rhs, is_spaced=op.is_spaced)
    # Promote color keywords like `red` to Color so `red - #070707` works.
    lhs = _maybe_promote_color(lhs)
    rhs = _maybe_promote_color(rhs)
    if isinstance(lhs, Dimension) and isinstance(rhs, Color):
        lhs = _dim_to_color(lhs)
    if isinstance(rhs, Dimension) and isinstance(lhs, Color):
        rhs = _dim_to_color(rhs)
    if isinstance(lhs, Color) and isinstance(rhs, Color):
        return _operate_colors(op.op, lhs, rhs, op.index)
    if isinstance(lhs, Dimension) and isinstance(rhs, Dimension):
        return _operate_dimensions(op.op, lhs, rhs, op.index, strict=ctx.strict_units)
    return Operation(index=op.index, op=op.op, lhs=lhs, rhs=rhs, is_spaced=op.is_spaced)


def _eval_isdefined(c: Call, ctx: EvalContext) -> Node:
    """`isdefined(@x)` returns `true` iff `@x` resolves without raising
    a `NameError`. less.js evaluates the arg lazily so an unknown
    variable doesn't propagate; we mirror by catching
    `UndefinedNameError` around the lookup. D3.
    """
    if len(c.args) != 1:
        return Keyword(index=c.index, value='false')
    try:
        eval_expression(c.args[0], ctx)
    except UndefinedNameError:
        return Keyword(index=c.index, value='false')
    return Keyword(index=c.index, value='true')


_CMP_OP_TEXTS = frozenset({'<', '>', '<=', '>=', '=', '<>'})


def _arg_to_condition(arg: Node, default_index: int) -> Condition:
    """Interpret a value-level `if()` / `boolean()` argument as a guard
    Condition. The parser produces a flat Expression of values (with
    `and`/`or` as Keywords and `<`/`=`/... as Anonymous separators);
    this walker rebuilds the precedence: `or` lowest, then `and`, then
    a comparison or a single truthy unit. Nested `not(...)` /
    `boolean(...)` calls and `(...)` wrappers are unwrapped recursively.
    """
    if isinstance(arg, Value):
        if len(arg.expressions) == 1:
            return _arg_to_condition(arg.expressions[0], default_index)
        return Condition(index=default_index, op='truthy', lhs=arg)
    if isinstance(arg, Expression):
        return _values_to_condition(arg.values, arg.index or default_index)
    return _single_to_condition(arg, default_index)


def _values_to_condition(values: list[Node], idx: int) -> Condition:
    or_groups = _split_by_keyword(values, 'or')
    if len(or_groups) > 1:
        result = _and_chain_to_condition(or_groups[0], idx)
        for grp in or_groups[1:]:
            result = Condition(index=idx, op='or', lhs=result, rhs=_and_chain_to_condition(grp, idx))
        return result
    return _and_chain_to_condition(values, idx)


def _and_chain_to_condition(values: list[Node], idx: int) -> Condition:
    and_groups = _split_by_keyword(values, 'and')
    if len(and_groups) > 1:
        result = _unit_to_condition(and_groups[0], idx)
        for grp in and_groups[1:]:
            result = Condition(index=idx, op='and', lhs=result, rhs=_unit_to_condition(grp, idx))
        return result
    return _unit_to_condition(values, idx)


def _split_by_keyword(values: list[Node], kw: str) -> list[list[Node]]:
    groups: list[list[Node]] = []
    current: list[Node] = []
    for v in values:
        if isinstance(v, Keyword) and v.value == kw:
            groups.append(current)
            current = []
        else:
            current.append(v)
    groups.append(current)
    return groups


def _unit_to_condition(values: list[Node], idx: int) -> Condition:
    cmp_pos: int | None = None
    for i, v in enumerate(values):
        if isinstance(v, Anonymous) and v.value in _CMP_OP_TEXTS:
            cmp_pos = i
            break
    if cmp_pos is not None:
        cmp_node = values[cmp_pos]
        assert isinstance(cmp_node, Anonymous)
        op_text = cmp_node.value
        lhs = _wrap_values(values[:cmp_pos], idx)
        rhs = _wrap_values(values[cmp_pos + 1 :], idx)
        return Condition(index=idx, op=op_text, lhs=lhs, rhs=rhs)
    if len(values) == 1:
        return _single_to_condition(values[0], idx)
    if not values:
        return Condition(index=idx, op='truthy', lhs=Keyword(index=idx, value='false'))
    return Condition(index=idx, op='truthy', lhs=_wrap_values(values, idx))


def _wrap_values(values: list[Node], idx: int) -> Node:
    if len(values) == 1:
        return values[0]
    return Expression(index=idx, values=values)


def _single_to_condition(node: Node, idx: int) -> Condition:
    if isinstance(node, Paren):
        return _arg_to_condition(node.value, idx)
    if isinstance(node, Expression):
        return _arg_to_condition(node, idx)
    if isinstance(node, Call):
        nm = node.name.lower()
        if nm == 'not':
            if node.args:
                inner = _arg_to_condition(node.args[0], idx)
            else:
                inner = Condition(index=idx, op='truthy', lhs=Keyword(index=idx, value='false'))
            return Condition(index=idx, op='not', lhs=inner)
        if nm == 'boolean':
            if node.args:
                return _arg_to_condition(node.args[0], idx)
            return Condition(index=idx, op='truthy', lhs=Keyword(index=idx, value='false'))
    return Condition(index=idx, op='truthy', lhs=node)


def _eval_if(c: Call, ctx: EvalContext) -> Node:
    """`if(cond, A, B)` — eager-eval `cond`, lazy-eval the matching
    branch only. Two-arg form (`if(cond, A)`) returns an empty Anonymous
    when `cond` is false; mirrors less.js's `new Anonymous`.
    """
    from .control_flow import eval_condition

    if not c.args:
        return Anonymous(index=c.index, value='')
    cond = _arg_to_condition(c.args[0], c.index)
    take_true = eval_condition(cond, ctx)
    if take_true:
        if len(c.args) >= 2:
            return _unwrap_single(eval_expression(c.args[1], ctx))
        return Anonymous(index=c.index, value='')
    if len(c.args) >= 3:
        return _unwrap_single(eval_expression(c.args[2], ctx))
    return Anonymous(index=c.index, value='')


def _eval_boolean(c: Call, ctx: EvalContext) -> Node:
    """`boolean(cond)` — return `Keyword('true'|'false')` from the
    Condition arg. Used so the boolean result can be stored in a
    variable and revisited later (`@flag: boolean(@x > 0); ... when (@flag)`).
    """
    from .control_flow import eval_condition

    if not c.args:
        return Keyword(index=c.index, value='false')
    cond = _arg_to_condition(c.args[0], c.index)
    return Keyword(index=c.index, value='true' if eval_condition(cond, ctx) else 'false')


def _operate_colors(op: str, a: Color, b: Color, index: int) -> Color:
    """Per-channel arithmetic with alpha blending. Mirrors
    `Color.operate` in less.js — alpha uses Porter-Duff `a*(1-b)+b`.
    """
    new_rgb: list[float] = []
    for i in range(3):
        new_rgb.append(_apply(op, a.rgb[i], b.rgb[i]))
    alpha = a.alpha * (1 - b.alpha) + b.alpha
    return Color(
        index=index,
        value='',
        rgb=(new_rgb[0], new_rgb[1], new_rgb[2]),
        alpha=alpha,
        color_function='',
    )


def _apply(op: str, x: float, y: float) -> float:
    if op == '+':
        return x + y
    if op == '-':
        return x - y
    if op == '*':
        return x * y
    if op == '/':
        if y == 0:
            raise OperationError('division by zero')
        return x / y
    raise OperationError(f'unknown operator: {op!r}')


def eval_negative(n: Negative, ctx: EvalContext) -> Node:
    inner = _unwrap_single(eval_node(n.value, ctx))
    if isinstance(inner, Dimension):
        return Dimension(index=n.index, value=-inner.value, unit=inner.unit)
    return Negative(index=n.index, value=inner)


def eval_paren(p: Paren, ctx: EvalContext) -> Node:
    # `~(...)` list constructors carry no paren semantics — emit the
    # inner Value/Expression directly so downstream consumers (`length`,
    # `extract`, `each`, value-list iteration) see the list shape.
    if p._tilde_list:
        return eval_node(p.value, ctx)
    with ctx.entering_parens():
        inner = eval_node(p.value, ctx)
        # Media-query feature shape `(min-width: @size)` is captured as
        # `Paren(Anonymous(text))` (parser bails on `:` inside parens to
        # keep the prelude verbatim). Resolve `@var` / `@{var}` inside
        # the captured text here so substitution into an at-rule prelude
        # produces `(min-width: 640px)`, not `(min-width: @size)`.
        if isinstance(inner, Anonymous):
            new_text = ctx.substitute_text(inner.value, strip_quotes=False)
            if new_text != inner.value:
                inner = Anonymous(index=inner.index, value=new_text)
    inner = _unwrap_single(inner)
    # Drop redundant nested parens: `((expr))` collapses to `(expr)`.
    # less.js folds these greedily so triple+ parens around the same
    # expression always emit as a single pair.
    while isinstance(inner, Paren):
        inner = inner.value
    # If the parens enclose a pure literal we can drop the wrapper.
    # less.js does the same: `(@x)` where `@x = 10px` emits `10px`, not
    # `(10px)`. Anything still compound stays wrapped.
    if isinstance(inner, Dimension | Color | Keyword | Quoted | Variable):
        return inner
    return Paren(index=p.index, value=inner)


def eval_call(c: Call, ctx: EvalContext) -> Node:
    """Evaluate the arguments, then dispatch through the function registry.

    Unknown function names pass through (re-emit as `name(arg, ...)`),
    which is how CSS-only functions like `linear-gradient(...)` and
    `var(...)` survive a Less compile. Registry-resolved calls return
    whatever node the implementation produces — Dimension for math,
    Color for color helpers, Keyword for predicates, etc.

    Argument-shape mismatches (e.g. CSS Color 4 `hsl(from #0000FF ...)`
    feeding our HSL builder) raise ArgumentError; we catch those and
    fall back to passthrough so modern-syntax CSS round-trips.
    """
    # `isdefined(@x)` is a special form: it catches the variable-lookup
    # failure rather than letting it bubble. Run before arg eval so an
    # undefined `@x` doesn't crash the lookup.
    if c.name.lower() == 'isdefined':
        return _eval_isdefined(c, ctx)
    # `if(cond, A, B)` and `boolean(cond)` interpret their first arg as
    # a Condition tree, not a value expression. The branch-lazy `if` only
    # evaluates the selected branch (so `darken(@some, 10%)` doesn't fire
    # when `@some` isn't a color and the false-branch is taken).
    name_lower = c.name.lower()
    if name_lower == 'if':
        return _eval_if(c, ctx)
    if name_lower == 'boolean':
        return _eval_boolean(c, ctx)
    # Hardened-mode policy: refuse functions the embedder disabled (e.g.
    # `replace`, whose residual ReDoS lives in uninterruptible C-level
    # regex). Checked before arg eval so a disabled call does no work.
    if name_lower in ctx.disabled_functions:
        err = UnsupportedFeatureError(f'function `{c.name}` is disabled by the `disabled_functions` policy')
        _locate(err, c.index, ctx)
        raise err
    # `calc(...)` and friends keep their math literal: variables resolve
    # but `+`/`-`/`*`/`/` stay as Operations in the tree. Mirrors less.js.
    # Non-calc-like functions called *inside* a `calc(...)` evaluate their
    # args in normal math context — so `calc(100% - min(10px + 10px))`
    # reduces the `min` args to a Dimension before composition.
    calc_like = c.name.lower() in _CALC_LIKE_FUNCTIONS
    with ctx.calc_mode(calc_like):
        try:
            new_args = [eval_expression(a, ctx) for a in c.args]
        except UndefinedNameError as e:
            # Less.js wraps property/variable recursion errors raised
            # inside a function-arg expression with an `Error evaluating
            # function 'name':` prefix and anchors at the function-call
            # position (not the arg).
            msg = e.message
            if msg.startswith('Recursive property reference') or msg.startswith('Recursive variable definition'):
                e.message = f'Error evaluating function `{c.name}`: {msg}'
                e.location = None
                _locate(e, c.index, ctx)
            raise
    impl = lookup(c.name)
    if impl is None:
        return Call(index=c.index, name=c.name, args=new_args)
    # Per-arg unwrap so the function bodies can pattern-match on
    # Dimension/Color/Quoted/Keyword without re-doing the dance every
    # time. The original wrapper structure is irrelevant once the args
    # are evaluated — registry callables build their own return types.
    unwrapped: list[Node] = [_unwrap_single(a) for a in new_args]
    try:
        return impl(unwrapped, ctx)
    except ArgumentError as e:
        # `_fatal=True` on the error means the function deliberately
        # rejected its input (e.g. `svg-gradient` with bad direction or
        # `unit()` with a non-Dimension). less.js wraps these with an
        # `Error evaluating function 'name':` prefix and propagates.
        if e._fatal:
            if not e.message.startswith('Error evaluating function'):
                e.message = f'Error evaluating function `{c.name}`: {e.message}'
            e.location = None
            _locate(e, c.index, ctx)
            raise
        # Soft ArgumentError — fall back to passthrough so modern-CSS
        # `hsl(from #fff ...)` survives parsing.
        return Call(index=c.index, name=c.name, args=new_args)
    except EvalError as e:
        # Hard EvalError (SyntaxError-class, e.g. `default()` used
        # outside a parametric mixin guard) propagates with the call's
        # source position attached. less.js anchors these at the
        # function-call site, not the surrounding declaration.
        if e.location is None:
            _locate(e, c.index, ctx)
        raise
    except (ArithmeticError, ValueError) as e:
        # A built-in leaked a raw Python numeric error — `sqrt(-1)` /
        # `acos(2)` (ValueError: math domain), `mod(_, 0)`
        # (ValueError from fmod), `pow` overflow (OverflowError). The
        # public contract is that every failure is a LessError, so wrap
        # it rather than letting `ValueError`/`OverflowError` escape.
        wrapped = OperationError(f'Error evaluating function `{c.name}`: {e}')
        _locate(wrapped, c.index, ctx)
        raise wrapped from e


def _operate_dimensions(op: str, a: Dimension, b: Dimension, index: int, strict: bool = False) -> Dimension:
    """Compute `a op b` for two Dimensions.

    Units follow less.js's compound-unit model in strict mode:
      `+`/`-`  require matching units (or one side unitless / convertible).
      `*`      multiplies units (`px * em` → `em*px`, `px * px` → `px*px`).
      `/`      divides units; matching units cancel.

    In non-strict mode (less.js default), multiply/divide collapse to
    a single unit — the side that has one — instead of building a
    compound. Strict mode keeps the compound form.

    The formatter (`_format_dimension`) raises `OperationError` when it
    tries to serialise a compound unit because such units have no
    CSS spelling.
    """
    if op in ('+', '-'):
        if a.unit == b.unit:
            unit = a.unit
            b_val = b.value
        elif not a.unit:
            unit = b.unit
            b_val = b.value
        elif not b.unit:
            unit = a.unit
            b_val = b.value
        elif convertible(a.unit, b.unit):
            unit = a.unit
            b_val = convert(b.value, b.unit, a.unit)
        elif strict:
            # Wording mirrors less.js exactly.
            raise OperationError(
                f"Incompatible units. Change the units or use the unit function. Bad units: '{a.unit}' and '{b.unit}'."
            )
        else:
            # Permissive default (less.js `strictUnits: false`): apply
            # the op on the raw magnitudes and keep the lhs unit, so
            # `1px + 1em` resolves to `2px` rather than raising. H2.
            unit = a.unit
            b_val = b.value
        result = a.value + b_val if op == '+' else a.value - b_val
        dim = Dimension(index=index, value=_fround(result), unit=unit)
        # Propagate `_backup_unit` so `(2em/1em) + 20` (= `Dim(2, '')`
        # with backup `em`) keeps the `em` suffix when emitted. Mirrors
        # less.js's add/sub keeping `unit.backupUnit` alive.
        _set_backup_unit(dim, a, b)
        return dim
    if op == '*':
        unit = _multiply_units(a.unit, b.unit)
        dim = Dimension(index=index, value=_fround(a.value * b.value), unit=unit)
        _set_backup_unit(dim, a, b)
        return dim
    if op == '/':
        if b.value == 0:
            raise OperationError('division by zero')
        unit = _divide_units(a.unit, b.unit)
        dim = Dimension(index=index, value=_fround(a.value / b.value), unit=unit)
        _set_backup_unit(dim, a, b)
        return dim
    raise OperationError(f'unknown operator: {op!r}')


def _fround(value: float) -> float:
    """Pass through — fround runs at *emit* time only (see
    `_emit_num_precision` in `visitors.py`). Applying it at each
    arithmetic step would erode precision through chained operations:
    `percentage((11/12))` would become `91.666667%` instead of
    `91.66666667%` because `11/12 = 0.91666...` gets clamped to
    `0.91666667` before the `*100`. less.js keeps full IEEE-754
    precision through operations and only rounds at toCSS.
    """
    return value


def _dim_backup(d: Dimension) -> str:
    """Pick the unit string less.js would store as `unit.backupUnit` for
    this dimension: the left-most unit symbol — either the first
    numerator atom, an inherited backup from an earlier operation, or
    the first denominator atom as a last resort.
    """
    if d.unit:
        if '/' in d.unit:
            num, _, _ = d.unit.partition('/')
        else:
            num = d.unit
        if num:
            return num.split('*')[0]
    backup = d._backup_unit
    if isinstance(backup, str) and backup:
        return backup
    if d.unit and '/' in d.unit:
        _, _, den = d.unit.partition('/')
        if den:
            return den.split('*')[0]
    return ''


def _set_backup_unit(result: Dimension, a: Dimension, b: Dimension) -> None:
    """Mirror less.js's `unit.backupUnit` (set in `clone()`): the result
    inherits the left operand's backup so chained `*`/`/` ops emit
    `1px` for `1px / 1px` and pick the left-hand unit for `14px *
    1.4em → 19.6px` (when the cancelled numerator has more than one
    distinct atom, less.js falls back to the backup rather than
    arbitrarily picking the alphabetically-first one).
    """
    backup = _dim_backup(a) or _dim_backup(b)
    if backup:
        result._backup_unit = backup


def _multiply_units(a: str, b: str) -> str:
    """Compose two unit strings under multiplication. Same-unit-list
    elements are accumulated; mixed elements appear sorted in the
    numerator. Denominators (the `/X` tail) are concatenated.

    Examples:
      ('', '')              → ''
      ('px', '')            → 'px'
      ('px', 'px')          → 'px*px'
      ('px', 'em')          → 'em*px'
      ('px/em', 'em')       → 'px'           (em cancels)
      ('px*em', 'px')       → 'em*px*px'
    """
    num_a, den_a = _split_unit(a)
    num_b, den_b = _split_unit(b)
    num = num_a + num_b
    den = den_a + den_b
    return _format_unit(_cancel(num, den))


def _divide_units(a: str, b: str) -> str:
    """Compose two unit strings under division — flip b's numerator and
    denominator before composing.
    """
    num_a, den_a = _split_unit(a)
    num_b, den_b = _split_unit(b)
    num = num_a + den_b
    den = den_a + num_b
    return _format_unit(_cancel(num, den))


def _split_unit(unit: str) -> tuple[list[str], list[str]]:
    """Parse a unit string back into (numerator, denominator) lists."""
    if not unit:
        return [], []
    if '/' in unit:
        n, d = unit.split('/', 1)
        return n.split('*') if n else [], d.split('*') if d else []
    return unit.split('*'), []


def _cancel(num: list[str], den: list[str]) -> tuple[list[str], list[str]]:
    """Cancel each element that appears in both numerator and
    denominator. Returns sorted lists (canonical form).
    """
    n = list(num)
    d = list(den)
    for u in list(n):
        if u in d:
            n.remove(u)
            d.remove(u)
    return sorted(n), sorted(d)


def _format_unit(parts: tuple[list[str], list[str]]) -> str:
    """Serialise (numerator, denominator) back to canonical string.

    Empty numerator stays empty (`/em`, not `1/em`) so a downstream
    `_split_unit` round-trips cleanly — inserting a synthetic `1`
    here makes `_multiply_units('1/em', 'em')` produce `'1'` as a
    spurious numerator atom, which then leaks into later `*`
    operations as `'1*px'`. The CSS-facing `1/em` spelling, when
    relevant, is the formatter's job (`_format_dimension`).
    """
    num, den = parts
    n_str = '*'.join(num)
    if not den:
        return n_str
    d_str = '*'.join(den)
    return f'{n_str}/{d_str}'


def _unwrap_single(node: Node) -> Node:
    """Flatten trivial Value/Expression wrappers so a single literal can
    be operated on without manual destructuring."""
    while True:
        if isinstance(node, Value) and len(node.expressions) == 1:
            node = node.expressions[0]
        elif isinstance(node, Expression) and len(node.values) == 1:
            node = node.values[0]
        else:
            return node
