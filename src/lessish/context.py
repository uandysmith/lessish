"""Evaluation context: frame stack, variable lookup, and text substitution.

Variable lookup returns a structured Node so operations like
`@a + 5px` are computed type-aware; a text bridge wraps it for the
remaining text-substitution contexts (selectors and at-rule preludes,
which still go through the regex path).

Lookups walk the frame stack innermost-first and within each frame
scan rules in REVERSE (less.js 'lazy / last-wins' semantics). A
`_resolving` set guards against `@a: @a;` cycles.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    # `functions.default_module` top-imports `context`, so a runtime
    # import here would cycle. The annotation is a string thanks to
    # `from __future__ import annotations`; `__post_init__` does the
    # real import lazily.
    from .functions.default_module import DefaultGuardState
    from .importer import Importer

from ._strutil import has_block_comment as _has_block_comment
from ._strutil import strip_quotes as _unquote
from .ast_nodes import (
    Anonymous,
    Color,
    Declaration,
    DetachedRuleset,
    Dimension,
    Expression,
    Keyword,
    Node,
    Quoted,
    Ruleset,
    Url,
    Value,
)
from .errors import EvalError, UndefinedNameError
from .parser import parse_value_text
from .source import Source
from .visitors import value_to_css


def _attach_dr_closure(node: Node, frames: list[Ruleset]) -> None:
    """Tag every DetachedRuleset inside `node` with the captured frame
    stack so a later invocation (`@x()` / DR-as-mixin-arg) resolves
    variables against the DR's lexical scope first. No-op if a closure
    is already attached (the first capture wins — re-evaluation must
    not clobber a deeper, more accurate snapshot). Callers that pass a
    DR which may be re-bound to a DIFFERENT scope on each invocation
    (mixin arg binding for a literal DR inside a mixin body) must
    `_fresh_dr_clones` the value first, so each capture lives on its
    own DR object.

    "Already attached" is detected by `dr._captured_frames` being a
    non-empty list.
    """
    inner = node
    if isinstance(inner, Value):
        for e in inner.expressions:
            _attach_dr_closure(e, frames)
        return
    if isinstance(inner, Expression):
        for v in inner.values:
            _attach_dr_closure(v, frames)
        return
    if isinstance(inner, DetachedRuleset) and not inner._captured_frames:
        inner._captured_frames = list(frames)


@overload
def _fresh_dr_clones(node: Value) -> Value: ...
@overload
def _fresh_dr_clones(node: Expression) -> Expression: ...
@overload
def _fresh_dr_clones(node: DetachedRuleset) -> DetachedRuleset: ...
@overload
def _fresh_dr_clones(node: Node) -> Node: ...
def _fresh_dr_clones(node: Node) -> Node:
    """Replace every DetachedRuleset inside `node` with a fresh copy so
    a subsequent `_attach_dr_closure` writes to a per-invocation object
    rather than scribbling on a literal AST node shared across mixin
    invocations.

    Without this, a mixin whose body builds DR literals from its own
    args — `.setColor(@var,@l,@d){._setColor({@{var}:@l;}, …);}` —
    captures the FIRST call's `@var` on the literal and reuses it for
    every subsequent invocation, since the `_attach_dr_closure` guard
    ("first capture wins") sees the slot already populated.

    Returns a node of the same outer shape; non-DR children are shared.
    """
    if isinstance(node, Value):
        return Value(
            index=node.index,
            expressions=[_fresh_dr_clones(e) for e in node.expressions],
        )
    if isinstance(node, Expression):
        return Expression(
            index=node.index,
            values=[_fresh_dr_clones(v) for v in node.values],
        )
    if isinstance(node, DetachedRuleset):
        # Clone with every other field carried over (including
        # `_lambda_params`, `_source`, etc.) but `_captured_frames`
        # reset to fresh empty so the next `_attach_dr_closure` is
        # the one that populates it. `dataclasses.replace` runs the
        # constructor so the kw-only typed fields are all populated;
        # passing `_captured_frames=[]` overrides the carried value.
        from dataclasses import replace as _dc_replace

        return _dc_replace(node, _captured_frames=[])
    return node


def _contains_dr(node: Node) -> bool:
    """True if `node` is or wraps a DetachedRuleset."""
    if isinstance(node, DetachedRuleset):
        return True
    if isinstance(node, Value):
        return any(_contains_dr(e) for e in node.expressions)
    if isinstance(node, Expression):
        return any(_contains_dr(v) for v in node.values)
    return False


_STATIC_LEAF_TYPES = frozenset({Dimension, Color, Keyword})


def _is_static(node: Node) -> bool:
    """True when `eval_value(node, ctx)` would yield a structurally
    equivalent tree regardless of the active context.

    A "static" parsed value contains only literal leaves and Value/
    Expression wrappers — no Variable / Call / Operation / Lookup /
    PropertyAccess / Paren / Negative / DetachedRuleset / MixinCall.
    Strings and URLs are static only when they don't carry interpolation
    markers; `Anonymous` is static unless tagged as an ie-filter (which
    re-substitutes its text on every eval).

    Used by `lookup_variable_node` to short-circuit literal variable
    resolution: a variable bound to e.g. `@grey: #333` resolves to the
    same Color node every time, so we can skip the parse → eval →
    closure-attach walk on every reference.
    """
    t = type(node)
    if t in _STATIC_LEAF_TYPES:
        return True
    if isinstance(node, Quoted):
        return '@{' not in node.value
    if isinstance(node, Url):
        return '@' not in node.value
    if isinstance(node, Anonymous):
        return not node._ie_filter
    if isinstance(node, Value):
        return all(_is_static(e) for e in node.expressions)
    if isinstance(node, Expression):
        return all(_is_static(v) for v in node.values)
    # Variable, Call, Operation, Negative, Paren, PropertyAccess,
    # Lookup, MixinCall, VariableCall, DetachedRuleset — always dynamic.
    return False


# `@{name}` interpolation (used in strings, selectors, property names).
# Resolved values are quote-stripped: `@s: "x"; "@{s}"` → `"x"`, not `""x""`.
_INTERP_RE = re.compile(r'@\{([\w-][\w-]*)\}')

# `${name}` property-value interpolation — looks up a SIBLING property
# `name` in the current ruleset (last-wins). Used for `${prop}` in
# property NAMES and string contexts.
_PROP_INTERP_RE = re.compile(r'\$\{([_a-zA-Z][\w-]*)\}')

# Bare `@name` value-position reference. Must NOT match digit-led names
# (`@1` is a special form we don't handle yet) or non-identifiers; word
# boundary on the trailing side prevents `@foo-bar` from matching as `@foo`.
# A leading `\` escapes the `@` (CSS escape — `.foo\@bar` is a class
# whose name happens to contain `@`; uikit uses this for `\@s`/`\@m`
# breakpoint suffixes).
_BARE_VAR_RE = re.compile(r'(?<!\\)@([_a-zA-Z][\w-]*)')


# Transient evaluation-state toggles, used as `with ctx.calc_mode(x):`.
# These are slotted `__enter__`/`__exit__` objects rather than
# `@contextmanager` generators: they sit on the value-eval hot path
# (tens of thousands of enters per compile) where the generator
# machinery is the dominant cost. A fresh instance per `with` keeps the
# saved value private, so nesting is safe; centralising the restore in
# `__exit__` keeps the set/restore pairing the call site can't forget.
class _EnteringParens:
    __slots__ = ('_ctx', '_prev')

    def __init__(self, ctx: EvalContext) -> None:
        self._ctx = ctx

    def __enter__(self) -> None:
        self._prev = self._ctx.in_parens
        self._ctx.in_parens = True

    def __exit__(self, *exc: object) -> None:
        self._ctx.in_parens = self._prev


class _CalcMode:
    __slots__ = ('_ctx', '_prev', '_enabled')

    def __init__(self, ctx: EvalContext, enabled: bool) -> None:
        self._ctx = ctx
        self._enabled = enabled

    def __enter__(self) -> None:
        self._prev = self._ctx.in_calc
        self._ctx.in_calc = self._enabled

    def __exit__(self, *exc: object) -> None:
        self._ctx.in_calc = self._prev


class _MathMode:
    __slots__ = ('_ctx', '_prev', '_mode')

    def __init__(self, ctx: EvalContext, mode: str) -> None:
        self._ctx = ctx
        self._mode = mode

    def __enter__(self) -> None:
        self._prev = self._ctx.math
        self._ctx.math = self._mode

    def __exit__(self, *exc: object) -> None:
        self._ctx.math = self._prev


@dataclass
class EvalContext:
    """The frames stack and recursion guard used during evaluation.

    `in_parens` tracks whether the evaluator is currently inside a
    parenthesised sub-expression. less.js default math mode is
    PARENS_DIVISION: `/` only collapses inside `(...)`. Other operators
    (`+`, `-`, `*`) evaluate unconditionally.
    """

    frames: list[Ruleset] = field(default_factory=list)
    in_parens: bool = False
    in_calc: bool = False
    strict_units: bool = False
    # less.js `math` option, normalised to one of:
    #   `'always'`           — fold every operator everywhere.
    #   `'parens-division'`  — default; `/` only inside parens, others everywhere.
    #   `'parens'`           — every operator only inside parens.
    # See `eval_operation_inner` for the per-op gates.
    math: str = 'parens-division'
    source: Source | None = None
    _resolving: set[str] = field(default_factory=set, repr=False, compare=False)
    # `$prop` skips Declarations whose values are currently being
    # evaluated (chain of nested resolutions). Holds Python id()s.
    _current_decl_ids: set[int] = field(default_factory=set, repr=False, compare=False)
    # Importer instance for resolving `@import "@{var}"` deferred at the
    # importer pass (path was unbound). Set by `compile()` when import
    # processing is enabled; `None` for stand-alone evaluator use. Typed
    # via a `TYPE_CHECKING` import (never executed at runtime, so no
    # `functions → context → importer` cycle) so file-I/O consumers get
    # checked attribute access instead of stringly-typed `getattr`.
    importer: Importer | None = None
    # Mixin invocation budget — catches pathological inputs that
    # produce exponential expansion through cross-invocation matching
    # without going deeply recursive. Counters are bumped on every
    # `_invoke_mixin_call`; once `_mixin_call_depth` exceeds
    # `MIXIN_DEPTH_LIMIT` or `_mixin_call_total` exceeds
    # `MIXIN_TOTAL_LIMIT`, an EvalError is raised so the failure
    # surfaces as a clear diagnostic instead of a wall-clock hang.
    _mixin_call_depth: int = 0
    _mixin_call_total: int = 0
    # Per-compile DoS limits, threaded from `compile()`/`evaluate()`.
    # `None` means "use the module-level default" (the `MIXIN_*_LIMIT`
    # constants in `evaluator/values.py`); a concrete int overrides it
    # for this compile. Embedders compiling untrusted Less can tighten
    # these. See `evaluator/mixins.py` (mixin budget) and
    # `substitute_text` / `functions/string.py` (the other two).
    mixin_depth_limit: int | None = None
    mixin_total_limit: int | None = None
    # Max bytes a single `@{name}` interpolation may expand to before we
    # treat it as a runaway (billion-laughs-style) expansion and raise.
    # Bounds memory/CPU for chained self-referential string variables
    # (`@a: "@{b}@{b}"; ...`) which the mixin budget does not cover.
    interp_expansion_limit: int = 1_000_000
    # Max length (chars) of the pattern and haystack accepted by the
    # `replace()` Less function. A coarse bound on regex input size;
    # paired with a catastrophic-backtracking pattern check in
    # `functions/string.py` (Python's `re` has no step/time budget).
    replace_input_limit: int = 100_000
    # Max element count `range()` may generate before raising — bounds a
    # `range(1e9)` memory blow-up (and a non-terminating `step <= 0`).
    range_max_elements: int = 1_000_000
    # Wall-clock evaluation budget (seconds). `None` disables it. When
    # set, `__post_init__` stamps `_deadline`; `check_deadline` fires
    # from the eval-cycle trampoline, the per-declaration hot path, and
    # mixin invocation, so the budget bounds a large flat (mixin-free)
    # workload as well as exponential mixin expansion. The work between
    # checks is Python-level and so genuinely interruptible (unlike a
    # C-level regex). Opt-in: the per-invocation `mixin_total_limit` is
    # the always-on guard.
    max_eval_seconds: float | None = None
    _deadline: float | None = field(default=None, repr=False, compare=False)
    # Throttles the clock read in `check_deadline`. Its call sites are
    # hot (per declaration, per cycle step), so the wall-clock is only
    # sampled once per 1024 calls — bounding overshoot to ~1ms of work
    # while keeping the per-call cost a single increment-and-mask.
    _deadline_tick: int = field(default=0, repr=False, compare=False)
    # Built-in function names (lowercased) refused at call time with
    # `UnsupportedFeatureError`. Opt-in hardening for untrusted input:
    # disables functions whose risk can't be fully bounded by input
    # validation (e.g. `replace`'s residual ReDoS lives in C-level regex
    # backtracking). Empty by default — every built-in is available.
    disabled_functions: frozenset[str] = field(default_factory=frozenset)
    # `_invoking_ruleset_ids` holds Python id()s of Rulesets currently
    # being evaluated as 0-arg mixins (cycle detector for
    # Ruleset-as-mixin invocations).
    _invoking_ruleset_ids: set[int] = field(default_factory=set, repr=False, compare=False)
    # Source-text indexes of Declarations that have been observed via
    # `$prop` property-access; an outer declaration consumer can use
    # this to decide whether to elide its own value when the accessed
    # decl already provided it.
    _property_accessed_indexes: set[int] = field(default_factory=set, repr=False, compare=False)
    # `default()` / mixin-guard nesting state. Lives on the ctx so two
    # concurrent compiles don't race on a module global. Imported lazily
    # in `__post_init__` to avoid the `functions → context → ast_nodes`
    # circular import; typed via the TYPE_CHECKING string form above.
    default_state: DefaultGuardState = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_state is None:
            from .functions.default_module import DefaultGuardState

            self.default_state = DefaultGuardState()
        if self.max_eval_seconds is not None and self._deadline is None:
            self._deadline = time.monotonic() + self.max_eval_seconds

    def check_deadline(self, where: str) -> None:
        """Abort if the wall-clock evaluation budget has been exceeded.

        Samples `time.monotonic()` only once per 1024 calls, so it is
        cheap enough to call from the per-declaration / per-cycle-step
        hot paths. A no-op when no budget is set.
        """
        if self._deadline is None:
            return
        self._deadline_tick += 1
        if self._deadline_tick & 0x3FF:
            return
        if time.monotonic() > self._deadline:
            from .errors import EvalError

            raise EvalError(
                f'Evaluation time budget exceeded ({self.max_eval_seconds}s) while {where} — probable runaway expansion'
            )

    def push_frame(self, frame: Ruleset) -> None:
        self.frames.insert(0, frame)

    def pop_frame(self) -> None:
        if self.frames:
            self.frames.pop(0)

    # Transient evaluation-state toggles, returned as one-shot context
    # managers (see `_EnteringParens` / `_CalcMode` / `_MathMode` above).
    def entering_parens(self) -> _EnteringParens:
        """`with ctx.entering_parens():` evaluates the body with
        `in_parens=True` (parenthesised sub-expression), restoring the
        previous value on exit."""
        return _EnteringParens(self)

    def calc_mode(self, enabled: bool) -> _CalcMode:
        """`with ctx.calc_mode(enabled):` sets `in_calc` for the body and
        restores the previous value on exit."""
        return _CalcMode(self, enabled)

    def math_mode(self, mode: str) -> _MathMode:
        """`with ctx.math_mode(mode):` evaluates the body under a
        temporary `math` mode (e.g. the `font` shorthand's
        `always`→`parens-division` swap), restoring it on exit."""
        return _MathMode(self, mode)

    def lookup_variable_node(self, name: str) -> Node:
        """Find `@name` in the frame stack, parse its declared value, and
        evaluate it. Returns the resulting Node (Dimension, Color,
        Expression, ...). Raises UndefinedNameError if undefined or
        recursive.

        If the source declaration was `!important`, sets
        `_lookup_important = True` on the result so a consuming
        declaration can promote itself via
        `_value_carries_lookup_important` (mirrors the namespace-lookup
        bridge in `eval_lookup`).

        DetachedRuleset values get a `_captured_frames` closure attached
        the first time they're resolved, so `@x: { one: @a }` looks up
        `@a` in the scope where `@x` was *declared*, not where it's
        later invoked. For `@x: {…}` text-stored variables the frames
        snapshot starts at the declaration's enclosing frame; for DR
        args bound by `build_arg_frame` the closure is already attached
        (call-site scope captured before frame push).
        """
        if name in self._resolving:
            raise UndefinedNameError(f'Recursive variable definition for {name}')
        self._resolving.add(name)
        try:
            decl_idx = self._find_variable_with_frame(name)
            if decl_idx is None:
                raise UndefinedNameError(f'variable {name} is undefined')
            decl, frame_idx = decl_idx
            from .evaluator import _split_trailing_block_comment, eval_value

            # `build_arg_frame` stores DR args with their closure already
            # attached on the inner DR, so a pre-structured value is
            # returned as-is.
            if not isinstance(decl.value, Anonymous):
                return decl.value
            text = decl.value.value
            # Variable values live in their own expression context;
            # `in_calc=False` so `@c: 10px + 20px;` folds to `30px`
            # regardless of where `@c` is referenced. `in_parens` is
            # inherited from the call site (less.js's `parens-division`
            # math mode): `@v: 10px / 2;` keeps the `/` literal outside
            # parens, while `@v: (10px / 2);` still folds.
            base = decl.value.index
            anon = decl.value
            with self.calc_mode(False):
                body, trailing_comment = _split_trailing_block_comment(text)
                if trailing_comment is not None and '/*' not in body:
                    try:
                        parsed_body = anon._parsed_ast_body
                        if parsed_body is None:
                            parsed_body = parse_value_text(body, base_offset=base)
                            anon._parsed_ast_body = parsed_body
                        evaled = eval_value(parsed_body, self)
                        text_with_comment = value_to_css(evaled) + ' ' + trailing_comment
                        result: Node = Anonymous(index=decl.value.index, value=text_with_comment)
                    except Exception:
                        result = Anonymous(index=decl.value.index, value=text)
                elif _has_block_comment(text) or _has_permissive_syntax(text):
                    # Block comments and arrow-function literals don't
                    # usually survive the structured parse, so the safe
                    # default is to keep raw text. But a detached-ruleset
                    # literal (`@x: { … }`) does parse, and downstream
                    # code (each(), value-splice eval) needs the DR
                    # object — so try the structured route and accept
                    # it only when it yields a DR.
                    text_stripped = text.lstrip()
                    if text_stripped.startswith('{'):
                        try:
                            parsed = anon._parsed_ast
                            if parsed is None:
                                parsed = parse_value_text(text, base_offset=base)
                                anon._parsed_ast = parsed
                            evaled = eval_value(parsed, self)
                        except Exception:
                            evaled = None
                        if evaled is not None and _contains_dr(evaled):
                            _attach_dr_closure(evaled, self.frames[frame_idx:])
                            result = evaled
                        else:
                            result = Anonymous(index=decl.value.index, value=text)
                    else:
                        result = Anonymous(index=decl.value.index, value=text)
                else:
                    parsed = anon._parsed_ast
                    if parsed is None:
                        parsed = parse_value_text(text, base_offset=base)
                        anon._parsed_ast = parsed
                        anon._parsed_has_dr = _contains_dr(parsed)
                        anon._parsed_is_static = _is_static(parsed)
                    if anon._parsed_is_static:
                        # Pure literal — eval would yield the same tree.
                        # Return the cached AST directly.
                        result = parsed
                    else:
                        result = eval_value(parsed, self)
                        # A parsed AST with no DetachedRuleset can't host
                        # one after eval either, so skip the closure walk
                        # in that case (the common one on real stylesheets).
                        # `_parsed_has_dr` is None when uncached; treat
                        # None as "unknown, do the walk".
                        if anon._parsed_has_dr is not False:
                            _attach_dr_closure(result, self.frames[frame_idx:])
            if decl.important:
                # Clone first: the parsed AST is shared across lookups
                # via the `_parsed_ast` cache, so an in-place mark would
                # leak `!important` into unrelated consumers. The mark
                # lands on both the wrapper and the innermost single
                # node so `_value_carries_lookup_important`'s walk
                # tolerates `_unwrap_single` flattening.
                result = _clone_for_marking(result)
                _mark_important(result)
            return result
        finally:
            self._resolving.discard(name)

    def lookup_variable_text(self, name: str) -> str:
        """Stringified variable lookup, used by `substitute_text` for
        contexts that aren't structurally re-parsed (selectors,
        at-rule preludes).
        """
        return value_to_css(self.lookup_variable_node(name))

    def substitute_text(
        self,
        text: str,
        *,
        strip_quotes: bool = True,
        bare_vars: bool = True,
    ) -> str:
        """Replace `@name` and `@{name}` references in `text`.

        `@{name}` (interpolation form) strips surrounding quotes from the
        resolved value (so a string-valued variable embeds cleanly).
        Bare `@name` substitutes the value verbatim — but only outside
        quoted string literals; `@import` inside `"…@import…"` stays
        literal.

        `${name}` is the property-accessor counterpart of `@{name}` —
        looks up the sibling declaration `name` and embeds its value
        text. Used for `${prop}` in property names.

        `strip_quotes=False` keeps quotes from the resolved value
        unchanged. less.js uses this in selector contexts: `[data=@{x}]`
        with `@x: "test"` interpolates as `[data="test"]`, not
        `[data=test]`. Value contexts pass `True` (the default).
        """

        def maybe_strip(s: str) -> str:
            return _strip_quotes(s) if strip_quotes else s

        def repl_interp(m: re.Match[str]) -> str:
            return maybe_strip(self.lookup_variable_text('@' + m.group(1)))

        def repl_prop_interp(m: re.Match[str]) -> str:
            return maybe_strip(self.lookup_property_text(m.group(1)))

        def repl_bare(m: re.Match[str]) -> str:
            return self.lookup_variable_text('@' + m.group(1))

        # Fast path: the vast majority of declaration names and at-rule
        # preludes contain no interpolation/variable markers at all. The
        # regex passes below allocate match state per call, which adds
        # up across thousands of declarations. A single byte scan via
        # the `in` operator is cheap and lets us skip the regex work
        # when there's nothing to substitute.
        has_interp = '@{' in text or '${' in text
        has_bare_var = bare_vars and '@' in text
        if not has_interp and not has_bare_var:
            return text
        # `@{…}` and `${…}` interpolation is intentional inside string
        # bodies (that's the use case), so run them over the whole text.
        # Loop to handle nested interpolation like `@{box-@{suffix}}` —
        # `re.sub` makes one pass and won't re-scan an inner result that
        # creates a new outer match.
        if has_interp:
            limit = self.interp_expansion_limit
            for _ in range(16):
                new_text = _INTERP_RE.sub(repl_interp, text)
                new_text = _PROP_INTERP_RE.sub(repl_prop_interp, new_text)
                # Guard against runaway expansion: chained self-doubling
                # string variables (`@a: "@{b}@{b}"; @b: "@{c}@{c}"; …`)
                # grow multiplicatively with no fixpoint, exhausting
                # memory. The mixin budget does not see this path, so we
                # bound the expanded size directly. The recursive lookup
                # in `repl_interp` re-enters `substitute_text`, so the
                # innermost expansion to cross the limit trips first —
                # keeping peak memory near `limit`, not the final size.
                if len(new_text) > limit:
                    raise EvalError(
                        f'Interpolation expanded past {limit} bytes — probable runaway '
                        'variable expansion (billion-laughs). Tighten with the '
                        '`interp_expansion_limit` compile option if this is legitimate.'
                    )
                if new_text == text:
                    break
                text = new_text
        # Bare `@name` substitution must respect string-literal boundaries.
        # When called from inside a quoted-string eval (`~"@not-variable"`),
        # the caller passes `bare_vars=False` to suppress this — less.js
        # only does `@{name}` interpolation inside strings, never bare.
        if not bare_vars:
            return text
        if '@' not in text:
            return text
        out: list[str] = []
        for is_string, chunk in _split_string_runs(text):
            if is_string:
                out.append(chunk)
            else:
                out.append(_BARE_VAR_RE.sub(repl_bare, chunk))
        return ''.join(out)

    def lookup_property_text(self, name: str) -> str:
        """Stringified property lookup, mirroring `lookup_variable_text`
        but for non-variable declarations. Used by `${name}` interpolation.
        """
        for frame in self.frames:
            for rule in reversed(frame.rules):
                if isinstance(rule, Declaration) and not rule.variable and rule.name == name:
                    raw = rule.value.value if isinstance(rule.value, Anonymous) else ''
                    return raw
        raise UndefinedNameError(f'property {name} is undefined')

    def _find_variable(self, name: str) -> Declaration | None:
        result = self._find_variable_with_frame(name)
        return result[0] if result is not None else None

    def _find_variable_with_frame(self, name: str) -> tuple[Declaration, int] | None:
        # less.js scope rule: variable decls produced by a mixin call
        # are lower-priority than the call-site's own decls. Within a
        # frame, prefer the local decl (last wins) and fall back to the
        # latest mixin-produced decl. Across frames, walk innermost-first.
        for i, frame in enumerate(self.frames):
            local, from_mixin = _variable_index_for(frame).get(name, (None, None))
            chosen = local if local is not None else from_mixin
            if chosen is not None:
                return (chosen, i)
        return None


def _variable_index_for(
    frame: Ruleset,
) -> dict[str, tuple[Declaration | None, Declaration | None]]:
    """Lazy-build a `var_name -> (last_local, last_from_mixin)` index
    for `frame.rules`. Cached on `frame._var_index_cache` as
    `(len_when_built, dict)`; the eval pre-pass only appends to a
    frame's rules during invocation splicing, so a length-only key is
    sufficient — and when it grows we just walk the new tail instead
    of rebuilding the whole thing.

    Without this index every `@var` lookup would walk `frame.rules` in
    reverse — on real stylesheets that's 500-1500 rules per lookup and
    tens of thousands of lookups per compile. Incremental extension is
    the same algorithmic win as `_mixin_index_for` got: an N-rule
    frame that grows N times via splice is O(N) total, not O(N^2).
    """
    rules = frame.rules
    n = len(rules)
    cache = frame._var_index_cache
    if cache is not None:
        cached_len, cached_idx = cache
        if cached_len == n:
            return cached_idx
        if cached_len < n:
            _extend_variable_index(cached_idx, rules, cached_len, n)
            frame._var_index_cache = (n, cached_idx)
            return cached_idx
    idx: dict[str, tuple[Declaration | None, Declaration | None]] = {}
    _extend_variable_index(idx, rules, 0, n)
    frame._var_index_cache = (n, idx)
    return idx


def _extend_variable_index(
    idx: dict[str, tuple[Declaration | None, Declaration | None]],
    rules: list[Node],
    start: int,
    end: int,
) -> None:
    """Walk `rules[start:end]` and merge variable-declaration entries
    into `idx`. Used both for the cold path (full rebuild, `start=0`)
    and the warm append path. Maintains the
    `(last_local, last_from_mixin)` shape `_find_variable_with_frame`
    consumes — within a frame, the spec prefers the local decl (last
    wins) and falls back to the latest mixin-produced one.
    """
    # `type(...) is Declaration` instead of `isinstance(rule, Declaration)`:
    # `Declaration` has no subclasses (all AST classes are
    # `@dataclass(slots=True)`), so the exact-type check is correct
    # and ~3-5x cheaper per call. This loop fires once per rule on
    # every cache extension; on a Bootstrap-shaped compile that's
    # tens of thousands of iterations, so the constant factor matters.
    for i in range(start, end):
        rule = rules[i]
        if type(rule) is not Declaration or not rule.variable:
            continue
        local, from_mixin = idx.get(rule.name, (None, None))
        if rule._from_mixin_call:
            from_mixin = rule
        else:
            local = rule
        idx[rule.name] = (local, from_mixin)


def _strip_quotes(s: str) -> str:
    # Interpolation context: trim surrounding whitespace first, then
    # delegate to the shared quote-stripping core in `_strutil`.
    return _unquote(s.strip())


def _clone_for_marking(node: Node) -> Node:
    """Deep-clone a Value/Expression hierarchy so a subsequent
    `_mark_important` mutation doesn't pollute the original AST.

    The `_parsed_ast` cache on `Anonymous` shares every leaf node
    across lookups; marking `_lookup_important` in place would leak
    `!important` into every other consumer of the same leaf — e.g.
    turning `multi: ..., @a;` into `multi: ..., @a !important;` after
    a sibling `!important`-marked lookup transiently visited `@a`.
    `dataclasses.replace(node)` produces fresh instances with the
    same field values but a fresh `__dict__`.
    """
    from dataclasses import replace

    if isinstance(node, Value):
        return Value(
            index=node.index,
            expressions=[_clone_for_marking(e) for e in node.expressions],  # type: ignore[misc]
        )
    if isinstance(node, Expression):
        return Expression(
            index=node.index,
            values=[_clone_for_marking(v) for v in node.values],
        )
    return replace(node)


def _mark_important(node: Node) -> None:
    """Set ``_lookup_important = True`` on `node` and on the innermost
    node reached by flattening trivial Value/Expression wrappers.
    `_value_carries_lookup_important` walks the tree, so marking both
    placements is safe and lets callers' `_unwrap_single` flattening
    preserve the signal.

    Callers MUST pass a fresh-cloned tree (see `_clone_for_marking`)
    — this routine mutates `node._lookup_important` (and its single-
    child descendants') in place.
    """
    node._lookup_important = True
    cur: Node = node
    while True:
        if isinstance(cur, Value) and len(cur.expressions) == 1:
            cur = cur.expressions[0]
        elif isinstance(cur, Expression) and len(cur.values) == 1:
            cur = cur.values[0]
        else:
            break
        cur._lookup_important = True


_URL_FUNC_RE = re.compile(r'url\(', re.IGNORECASE)
_BARE_VAR_ONLY_RE = re.compile(r'^\s*@[_a-zA-Z][\w-]*\s*$')


def _split_string_runs(text: str) -> list[tuple[bool, str]]:
    """Yield ``(is_string, chunk)`` pairs for ``text``.

    Quoted runs (`"…"` or `'…'`, with `\\` escapes) flag True. The body
    of a bare ``url(...)`` function flags True too — `@less` inside
    `url(https://.../@less/...)` must stay literal. Exception: when the
    URL body is *only* a bare `@name` (e.g. `url(@base)`), the body is
    marked substitutable (False), so the @-prefixed string variable
    resolves.

    Everything else flags False. Used to keep bare-`@name` substitution
    outside string literals and inside URL paths.
    """
    parts: list[tuple[bool, str]] = []
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
            parts.append((True, text[i:j]))
            i = j
            continue
        url_match = _URL_FUNC_RE.match(text, i)
        if url_match:
            head = text[i : url_match.end()]
            depth = 1
            j = url_match.end()
            while j < n and depth > 0:
                c = text[j]
                if c in ('"', "'"):
                    j += 1
                    while j < n:
                        if text[j] == '\\' and j + 1 < n:
                            j += 2
                            continue
                        if text[j] == c:
                            j += 1
                            break
                        j += 1
                    continue
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = text[url_match.end() : j]
            parts.append((False, head))
            if _BARE_VAR_ONLY_RE.match(inner):
                parts.append((False, inner))
            else:
                parts.append((True, inner))
            if j < n and text[j] == ')':
                parts.append((False, ')'))
                j += 1
            i = j
            continue
        j = i
        while j < n and text[j] not in ('"', "'"):
            if _URL_FUNC_RE.match(text, j):
                break
            j += 1
        parts.append((False, text[i:j]))
        i = j
    return parts


def _has_permissive_syntax(text: str) -> bool:
    """True when `text` contains constructs the structured value parser
    can't faithfully round-trip — arrow-function literals (`=>`) and
    similar JS-ish forms used inside `@var: () => { … };` declarations.
    Treated as opaque text so substitution into `--custom-property`
    values preserves the source verbatim.
    """
    return '=>' in text
