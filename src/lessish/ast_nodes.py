"""AST node definitions.

Covers the full Less surface lessish parses today: selectors, blocks,
declarations, at-rules, mixin definitions and calls, value-level
expressions (Dimensions, Colors, Calls, Operations, …), guards,
extends, and detached rulesets. The block-level parser produces
`Anonymous` declaration values verbatim; the value-position parser
re-tokenises them into the richer tree the evaluator consumes.

All nodes are keyword-only dataclasses so subclasses can add required
fields without inheriting defaults from the base.
"""

from __future__ import annotations

from copy import deepcopy as _deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from .source import Source

# Immutable field value types shared (not cloned) by `Node.__deepcopy__`.
_ATOMIC_FIELD_TYPES: frozenset[type] = frozenset({int, float, bool, str, bytes})

if TYPE_CHECKING:
    # Type-only — never imported at runtime, so no `ast_nodes -> mixins`
    # cycle. With `from __future__ import annotations` the field
    # annotations below stay strings and are never evaluated, so the
    # precise types cost nothing at import yet keep `--strict` honest.
    from .mixins.matching import _MixinIndex


@dataclass(kw_only=True, slots=True)
class Node:
    """Base for every AST node. `index` is the 0-based offset in the source.

    ``_source`` is set by the importer when the node originates in a
    sub-file (``@import`` traversal stamps every node recursively so
    error attribution can resolve the right ``<file>:line:column``
    band). Default ``None`` means "originates in the outermost source"
    — the caller passes that ``Source`` explicitly to the evaluator.
    The field is ``repr=False, compare=False`` so it has no observable
    effect on dataclass equality / debug-printing.
    """

    # Per-subclass tuple of structural-child attribute names that
    # tree-walking helpers (`_shift_indices`, etc.) should recurse
    # through. Populated once at module load by `_stamp_child_attrs`
    # (see bottom of this file) — each subclass gets exactly the
    # subset of `_NODE_CHILD_ATTRS` that maps to a real field on it.
    # Declared on `Node` here so mypy sees the attribute when readers
    # do `type(n).__lessish_child_attrs__`; `ClassVar` keeps it out
    # of the dataclass field set (and out of the slots).
    __lessish_child_attrs__: ClassVar[tuple[str, ...]] = ()

    # Every dataclass field name on the concrete subclass, stamped once
    # by `_stamp_child_attrs`. Drives the custom `__deepcopy__` below so
    # cloning a parse tree doesn't pay `copy.deepcopy`'s generic
    # `__reduce_ex__`/state-dict machinery per node.
    __lessish_all_attrs__: ClassVar[tuple[str, ...]] = ()

    index: int
    _source: Source | None = field(default=None, repr=False, compare=False)
    # Parser-emitted hint: this value-position node should render tight
    # to its preceding sibling (no whitespace). Set on entities that
    # had no source whitespace between them (`@{x}_bar`).
    _glue_to_prev: bool = field(default=False, repr=False, compare=False)
    # Evaluator-emitted marker: this value resolved via `@var[key]` or
    # `@var` lookup of an `!important` declaration. The consuming
    # declaration promotes itself via `_value_carries_lookup_important`.
    _lookup_important: bool = field(default=False, repr=False, compare=False)

    def __deepcopy__(self, memo: dict[int, object]) -> Node:
        # `Lessish.evaluate(copy_input=True)` clones the whole parse tree
        # on every call; generic `copy.deepcopy` is the slowest single
        # operation in the pipeline (it round-trips each slotted node
        # through `__reduce_ex__` + a state dict). Field-wise copy keyed
        # off the stamped `__lessish_all_attrs__` skips all of that.
        # `_source` is an immutable `Source` (large text + offsets) shared
        # by every node from the same file — share it, never clone it.
        cls = type(self)
        new = cls.__new__(cls)
        memo[id(self)] = new
        for name in cls.__lessish_all_attrs__:
            v = getattr(self, name)
            if name == '_source' or v is None or type(v) in _ATOMIC_FIELD_TYPES:
                setattr(new, name, v)
            else:
                setattr(new, name, _deepcopy(v, memo))
        return new


@dataclass(kw_only=True, slots=True)
class Anonymous(Node):
    """Raw text that the parser has not (yet) structured further.

    Used for declaration values until the expression layer arrives. The
    string preserves the original source span (whitespace-trimmed at the
    edges) so emit can round-trip and the evaluator has the bytes it needs.
    """

    value: str
    # IE filter syntax (`progid:DXImageTransform...`) — value contains
    # commas/colons that look structural but are part of the IE call
    # signature. Flag set by parser; evaluator preserves verbatim.
    _ie_filter: bool = field(default=False, repr=False, compare=False)
    # Per-compile cache fields. `parse_value_text(self.value)` is a hot
    # path under variable lookup and declaration eval; we memoize the
    # parse and eval results inline on the node rather than a side
    # object to avoid a second heap allocation per `Anonymous`
    # (the most heavily allocated node type). `compile()` builds a
    # fresh AST per call; the addressable `evaluate()` path clones the
    # caller's tree up front (`Lessish.evaluate(copy_input=True)`), so
    # in-place writes only touch a tree the single call owns.
    #
    # The raw text is kept alongside the parsed form on purpose: it is
    # the only faithful record of source formatting that re-serialization
    # would normalise away — inline mid-value `/* */` comments (parser
    # trivia), multi-line layouts and trailing whitespace, exact
    # `!important` spacing, number spellings less.js leaves untouched,
    # and CSS custom-property / ie-filter / permissive (`=>`) syntax
    # that does not round-trip through the parser at all. The verbatim
    # emit path (`_can_emit_verbatim`) needs BOTH the raw text and the
    # parsed tree. Error attribution also relies on the re-parse running
    # at eval time with the owning `Declaration` in hand — the
    # `_propagate` / `_base_index` column anchoring depends on it.
    #
    # `_evaled_node`: the structured Node `value` evaluated to; emit
    # prefers it over reparsing `value`.
    _evaled_node: Node | None = field(default=None, repr=False, compare=False)
    # `_parsed_ast` / `_parsed_ast_body`: cached `parse_value_text` results.
    _parsed_ast: Value | None = field(default=None, repr=False, compare=False)
    _parsed_ast_body: Value | None = field(default=None, repr=False, compare=False)
    # `_contains_dr(_parsed_ast)` precomputed once.
    _parsed_has_dr: bool | None = field(default=None, repr=False, compare=False)
    # `_is_static(_parsed_ast)` precomputed once — when true, the
    # cached AST IS the value (no eval needed).
    _parsed_is_static: bool | None = field(default=None, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Comment(Node):
    """A comment that survived as an AST node. `silent=True` for `//`
    lines (which never emit)."""

    text: str
    silent: bool = False
    # True for comments extracted from a statement-form `@import`'s
    # prelude (`@import "x" /* note */;`). less.js leaves these at
    # the source location; the hoister keeps them out of the hoisted
    # `@import` block.
    _from_prelude: bool = field(default=False, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Element(Node):
    """One textual piece of a selector with its leading combinator.

    `combinator` is the separator from the previous element:
      ''   — compound (no separator: `a.foo` is two elements)
      ' '  — descendant (implicit, derived from whitespace in the source)
      '>' '+' '~' '|' '^' '^^'  — explicit
    `value` keeps the original text (e.g. '.foo', '#bar', '&',
    ':hover(.x)', '[attr="val"]', '@{name}').
    """

    combinator: str
    value: str


@dataclass(kw_only=True, slots=True)
class Selector(Node):
    elements: list[Element]
    extend_list: list[Extend] = field(default_factory=list)
    # True when the selector text contains `@{var}` interpolation that
    # was resolved at eval time. Drives downstream emit decisions for
    # source-map attribution.
    _had_interpolation: bool = field(default=False, repr=False, compare=False)
    # `_selector_mixin_name(self)` cache. Two fields so we can
    # distinguish "not yet computed" from "computed and is None"
    # (a legitimate non-mixin selector).
    _mixin_name_cached: bool = field(default=False, repr=False, compare=False)
    _mixin_name: str | None = field(default=None, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Declaration(Node):
    """A property declaration: `name: value [!important];`.

    `variable=True` when `name` starts with `@` (Less variable). `merge` is
    '+' for `name+: value` (comma-merge) or '+_' for `name+_: value`
    (space-merge), otherwise empty.

    `important_origin` records where the `!important` flag came from so
    the emitter can pick spacing. less.js prints `red!important` (no
    space) for source-explicit declarations but `red !important` (with
    space) when `!important` was inherited via `$prop`/`@var` lookup.
    Values: 'source' (parsed directly), 'lookup' (promoted by
    `_value_carries_lookup_important`), or '' (default).
    """

    name: str
    # Typed as `Node` because the value can be `Anonymous` (raw text,
    # round-trip path), `Value` (the structured tree), `Expression`,
    # or any leaf — `_build_arg_value` in mixins.py can return any
    # of those when binding `@arguments`. Downstream callers narrow
    # with `isinstance`.
    value: Node
    important: bool = False
    variable: bool = False
    merge: str = ''
    important_origin: str = ''
    # Whitespace between the value and `!important` in source.
    # Round-tripped to match less.js's output spacing exactly.
    # Ignored when `important_origin == 'accessed'`.
    important_prefix_ws: str = ' '
    # True for variable declarations spliced out of a mixin invocation
    # (`@x: 1; .m(); .m()`'s body's `@y: 2` reaches the caller as a
    # mixin-call decl). The scope-lookup pass deprioritises these so
    # a local decl with the same name wins regardless of source order.
    _from_mixin_call: bool = field(default=False, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Ruleset(Node):
    """A block: selectors + nested rules. The root is a special Ruleset with
    `selectors=[]` and `root=True`.

    `paths` is populated by the JoinSelector visitor after evaluation: each
    entry is a fully-resolved selector string (ancestors and `&` already
    substituted in), ready to be emitted.

    `condition` is a CSS-guard: a `Condition` evaluated at eval time. When
    it is set and resolves to false, the ruleset (and any children it
    would have produced) emits nothing. `None` means unguarded.

    `reference=True` marks rulesets imported under `@import (reference)`.
    They participate in mixin/extend resolution but never emit CSS on
    their own — only rules that *use* them via `:extend(...)` or mixin
    calls produce output. less.js calls this `isReferenced`.
    """

    selectors: list[Selector]
    rules: list[Node]
    root: bool = False
    paths: list[str] = field(default_factory=list)
    condition: Condition | None = None
    reference: bool = False
    # Selector paths added by the extend pass (`:extend(.x all)` etc.).
    # Empty list when the ruleset wasn't touched by extend resolution.
    _extend_added_paths: list[str] = field(default_factory=list, repr=False, compare=False)
    # Variable-lookup index cache: (rules-list length, name -> (last
    # local decl, last decl from a mixin) index). Invalidated by length
    # growth (only-append-during-eval invariant). `Declaration` is
    # defined in this module, so the precise type needs no import.
    _var_index_cache: tuple[int, dict[str, tuple[Declaration | None, Declaration | None]]] | None = field(
        default=None, repr=False, compare=False
    )
    # Mixin-lookup index cache. `_MixinIndex` is TYPE_CHECKING-imported
    # above, so this stays precise without a runtime cycle.
    _mixin_index_cache: _MixinIndex | None = field(default=None, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class AtRule(Node):
    """Generic at-rule: `@name <prelude> { body }` or `@name <prelude>;`.

    `prelude` is the raw text between the name and the brace/semicolon
    (whitespace-trimmed). `body=None` denotes the statement form.

    `trailing_comments` carries `Comment` nodes that were extracted from
    the prelude of a body-less at-rule (e.g. `@import "x" /* c */;`).
    less.js leaves them next to the at-rule at the source location;
    when the at-rule is hoisted (as happens to `@import`), the
    comments stay behind.
    """

    name: str
    prelude: str
    body: list[Node] | None = None
    trailing_comments: list[Comment] = field(default_factory=list)
    # Importer-attached metadata. `repr=False, compare=False` keeps
    # these fields invisible to dataclass equality and debug output.
    # True for `@import` directives whose path contains `@{var}` —
    # resolved deferred (after eval), not by the pre-eval importer.
    _deferred_import: bool = field(default=False, repr=False, compare=False)
    # Base directory captured when an `@import` is deferred, so the
    # later eval-time resolver searches relative to the originating
    # file rather than wherever eval happens.
    _import_base_dir: Path | None = field(default=None, repr=False, compare=False)
    # True for `@import` directives the BFS pre-pass marked as
    # duplicates of a `(once)` file already claimed elsewhere.
    _import_bfs_duplicate: bool = field(default=False, repr=False, compare=False)
    # BFS pre-parse result: the parsed sub-file's root, plus its
    # Source — captured once during the BFS pass so the splice phase
    # in `_handle_import` skips reparsing. Both None for non-bfs
    # `@import`s.
    _import_bfs_parsed: Ruleset | None = field(default=None, repr=False, compare=False)
    _import_bfs_source: Source | None = field(default=None, repr=False, compare=False)
    # `@import (reference)` propagation marker: the at-rule and its
    # contents participate in mixin / extend resolution but never emit
    # CSS on their own.
    _reference: bool = field(default=False, repr=False, compare=False)
    # `@__inline__` sentinel marker (block-form at-rule used only as
    # an emit-time grouping; never appears in source).
    _inline_no_blank: bool = field(default=False, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class MixinCallStatement(Node):
    """Raw-text placeholder for `.mixin(args)!important;` and friends.

    `mixins.transform_mixins` reparses this into a structured
    `MixinCall` once mixin lookup runs.
    """

    text: str


# Value-level nodes populate declarations after `parse_value_text`
# decomposes the raw text. `Declaration.value` stays typed as `Anonymous`
# from the block-level parser; the structured `Value` is built (and
# re-serialised) at eval time.


@dataclass(kw_only=True, slots=True)
class Dimension(Node):
    """A numeric literal with an optional unit: `10px`, `50%`, `1.5em`, `200`."""

    value: float
    unit: str = ''
    # When the dimension was produced by arithmetic that mixed units
    # (`1px + 1em`), this records the non-emitted unit so a later
    # operation can preserve it through a follow-up `*` / `/`.
    _backup_unit: str = field(default='', repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Color(Node):
    """A color value.

    `value` preserves the original textual form (`#fff`, `red`, or a CSS
    `rgba(...)` literal) so unmodified colors round-trip verbatim. `rgb`
    holds the channels as floats (0..255) so arithmetic produces a
    fractional intermediate without losing precision; `alpha` is 0..1.

    `color_function` controls how the value re-renders to CSS: empty
    means hex/keyword form, otherwise one of 'rgb', 'rgba', 'hsl', 'hsla'.
    Functions like `rgba(...)` / `hsl(...)` set this; hex literals leave
    it blank, falling back to `value` if no arithmetic happened.
    """

    value: str
    rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)
    alpha: float = 1.0
    color_function: str = ''


@dataclass(kw_only=True, slots=True)
class Quoted(Node):
    """A string literal: `"..."`, `'...'`, or escaped `~"..."` / `~'...'`."""

    quote: str  # '"' or "'"
    value: str  # contents between quotes (escapes preserved)
    escaped: bool = False


@dataclass(kw_only=True, slots=True)
class Keyword(Node):
    """An identifier-position value: `solid`, `auto`, `red`, `inherit`, ..."""

    value: str


@dataclass(kw_only=True, slots=True)
class Variable(Node):
    """A `@name` reference at value position. Distinct from a variable
    *definition* (`Declaration` with `variable=True`)."""

    name: str  # includes the leading '@'


@dataclass(kw_only=True, slots=True)
class PropertyAccess(Node):
    """A `$prop` accessor: look up the value of the property named
    `prop` declared in the current ruleset (or its ancestors). Like
    a variable reference but searching declaration *names* rather than
    `@name` definitions. Last-definition wins, mirroring less.js.
    """

    name: str  # without the leading '$'


@dataclass(kw_only=True, slots=True)
class Url(Node):
    """A `url(...)` value. Contents are kept verbatim — Less doesn't try
    to parse inside `url(...)` as an expression."""

    value: str


@dataclass(kw_only=True, slots=True)
class Call(Node):
    """A function call: `rgba(255, 0, 0, 0.5)`, `lighten(@c, 10%)`, etc."""

    name: str
    args: list[Expression]


@dataclass(kw_only=True, slots=True)
class Lookup(Node):
    """A `[key]` postfix lookup against a target that resolves to a property
    map: `@p[name]`, `#ns[name]`, `@p[@var]`, chained `@a[@b][c]`.

    `target` is any value node (typically a `Variable`, value-position
    `MixinCall`, `DetachedRuleset`, or another `Lookup` for chaining).

    `key_kind` discriminates the spelling:
      'name'           — bare IDENT (`[text]`) or `$text` (same lookup)
      'var'            — `@name` — look up a variable inside the target
      'var-indirect'   — `@@name` — resolve @name first, then var-lookup
      'prop-indirect'  — `$@name` — resolve @name first, then name-lookup
      'last'           — empty `[]` — return the target's last declaration
    """

    target: Node
    key_kind: str
    key: str


@dataclass(kw_only=True, slots=True)
class Operation(Node):
    """Binary arithmetic between two value nodes: `a + b`, `a - b`, etc.

    `is_spaced` records whether the operator carried surrounding
    whitespace in the source (`a / b` vs `a/b`); the emitter reads
    this to round-trip the original spelling when the operation
    didn't fold (e.g. `font: 12px/14px` shorthand under math modes
    that leave `/` literal).
    """

    op: str
    lhs: Node
    rhs: Node
    is_spaced: bool = True


@dataclass(kw_only=True, slots=True)
class Negative(Node):
    """Unary minus: `-@x`, `-10px`."""

    value: Node


@dataclass(kw_only=True, slots=True)
class Paren(Node):
    """A parenthesized sub-expression: `(a + b)`. Used by the evaluator
    to decide whether to evaluate `/` (PARENS_DIVISION semantics)."""

    value: Node
    # `~(...)` tilde-paren list literal: the value is a comma list that
    # should be treated as a single composite when used as a mixin arg
    # (`.m(~(a, b))` passes `(a, b)` as one positional, not two).
    _tilde_list: bool = field(default=False, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Expression(Node):
    """A whitespace-separated list of entities: `1px solid #000`. May
    have length 1 (in which case eval may unwrap)."""

    values: list[Node]


@dataclass(kw_only=True, slots=True)
class Value(Node):
    """A comma-separated list of Expressions: `red, blue, green`."""

    expressions: list[Expression]


@dataclass(kw_only=True, slots=True)
class Extend(Node):
    """One `:extend(target)` clause (one target, one option).

    A surface clause like `:extend(.a, .b all)` parses into two Extend
    nodes — easier to apply uniformly. `target` is the raw selector
    text (`.error`, `.foo .bar`); `option` is `'all'` (component-
    anywhere match) or `''` (full-path match).

    `extender_paths` is populated by the extend visitor: the resolved
    paths of the rule that carries this extend. The chain pass updates
    it as the extender itself gets extended.
    """

    target: str
    option: str = ''
    extender_paths: list[str] = field(default_factory=list)
    # `id()` of the enclosing `@media`-like AtRule (or None at root):
    # extends only match other rules in the same media scope. Set by
    # the join-selectors visitor.
    _media_scope: int | None = field(default=None, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class Condition(Node):
    """A guard expression node.

    Three shapes share this class so the parser builds a single tree:
      * **comparison** — `op` is one of `<`, `<=`, `>`, `>=`, `=`, `<>`.
        `lhs`/`rhs` are value-level nodes (Dimension, Color, Variable, ...).
        The evaluator runs both through `compare(...)` to get -1|0|1|None
        and maps that to true/false.
      * **logical** — `op` is `'and'` or `'or'`. Children are themselves
        Conditions; short-circuit evaluation.
      * **unary** — `op` is `'not'`. `lhs` is the inverted condition,
        `rhs` is None.

    `negate` flips the polarity once more after evaluation. less.js sets
    this from the `not` keyword too — we keep both forms because the
    parser can produce either depending on grouping.
    """

    op: str
    lhs: Node
    rhs: Node | None = None
    negate: bool = False


# Mixin nodes are created by `mixins.transform_mixins` as a post-parse
# pass: Rulesets whose selector ends with `(...)` become MixinDefinitions
# and MixinCallStatement placeholders become MixinCalls with structured
# args.


@dataclass(kw_only=True, slots=True)
class MixinParam(Node):
    """One parameter in a mixin definition.

    Three flavours:
      * variable param (`@width`)         — name is `@width`, pattern=None
      * default-valued (`@c: red`)        — name + default
      * variadic tail (`@rest...`)        — variadic=True
      * pattern (`red`, `5px`, `solid`)   — name='', pattern holds a Value

    Pattern params don't bind a variable; they're used purely for
    mixin-overload selection. A call matches only when the supplied arg
    equals the pattern value (Keyword/Dimension/Color etc.).
    """

    name: str
    default: Value | None = None
    variadic: bool = False
    pattern: Value | None = None


@dataclass(kw_only=True, slots=True)
class MixinArg(Node):
    """One argument in a mixin call. `name=None` for positional;
    non-None for named (`@name: value`) arguments."""

    value: Value
    name: str | None = None


@dataclass(kw_only=True, slots=True)
class DetachedRuleset(Node):
    """`{ ... }` block stored as a Less variable value, invokable later
    via `@name();`. Captured rules evaluate at invocation time in the
    call site's scope.
    """

    rules: list[Node]
    # Lexical-scope frame stack captured the first time this DR is
    # resolved as a value. Subsequent invocations look up variables
    # against this snapshot (closure semantics for `@x: { … }`).
    _captured_frames: list[Ruleset] = field(default_factory=list, repr=False, compare=False)
    # Anonymous-mixin lambda parameters when this DR is the body of a
    # `((@a, @b) { … })` form. None / empty list means a plain DR.
    _lambda_params: list[MixinParam] = field(default_factory=list, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class VariableCall(Node):
    """`@name();` — invocation of a detached-ruleset variable."""

    name: str


@dataclass(kw_only=True, slots=True)
class MixinDefinition(Node):
    """A `.name(params) { rules }` (or `#name(...) { ... }`) definition.

    `name` is the full mixin selector path (no params; preserves `>` if
    present). `rules` is the body. Mixin definitions are kept in their
    parent ruleset's rule list so name lookup at call sites works the
    same way variable lookup does — but they never emit CSS themselves.

    `guard` is the optional `when (...)` clause: when present, the
    mixin matches only when the condition (evaluated with the call
    site's bound args) is true.

    `is_ruleset_wrapper` distinguishes a real mixin definition from a
    view manufactured by `_ruleset_as_mixin` (CSS rulesets are callable
    as 0-arg mixins). The flag controls error policy: when every
    candidate is a wrapper and all guards fail, the call resolves to
    "no output" instead of raising — matches less.js's silent-skip
    behavior for CSS-guarded rules used as mixins.
    """

    name: str
    params: list[MixinParam]
    rules: list[Node]
    guard: Condition | None = None
    is_ruleset_wrapper: bool = False
    # Mixin-resolution metadata. All optional.
    # Set by importer / mixin transformer when the definition lives in
    # an `@import (reference)` subtree; mixin invocation uses this to
    # decide whether the body is allowed to emit.
    _was_referenced: bool = field(default=False, repr=False, compare=False)
    # Lookup-time closure: in-tree parent chain that the matcher walked
    # through to find this definition (descent through namespaces /
    # nested rulesets). Combined with `_splice_closure_frames` at
    # invocation time to build the effective lexical scope.
    _closure_frames: list[Ruleset] = field(default_factory=list, repr=False, compare=False)
    # Splice-time closure: arg/local frames of the surrounding mixin
    # invocation when this definition was emitted out of another
    # mixin's body. The "innermost" scope; takes precedence over
    # `_closure_frames`.
    _splice_closure_frames: list[Ruleset] = field(default_factory=list, repr=False, compare=False)
    # Frame index at which this candidate was found during name
    # resolution. Used by the shadowing pass to keep only candidates
    # from the innermost frame that produced any match.
    _match_frame_index: int = field(default=0, repr=False, compare=False)
    # When this MixinDefinition is a synthetic wrapper around a
    # user-written Ruleset (`_ruleset_as_mixin`), this is the
    # `id()` of the source Ruleset. Used to break self-recursion
    # for "Ruleset called as 0-arg mixin" cases.
    _source_ruleset_id: int | None = field(default=None, repr=False, compare=False)


@dataclass(kw_only=True, slots=True)
class MixinCall(Node):
    """A `.name(args)` invocation. `args` is a mix of positional and
    named (`MixinArg`) arguments."""

    name: str
    args: list[MixinArg]
    important: bool = False
    # Namespace-alias call shape (`#lib.colors`) with no `()` in source:
    # emit-time stringification should not append empty parens.
    _no_parens: bool = field(default=False, repr=False, compare=False)


# Attribute names that participate in structural recursion through an
# AST tree (e.g. `_shift_indices` walking a value-level tree to
# re-anchor `index` fields, or visitors enumerating children). Most
# Node subclasses only have 1-2 of these as real fields; the rest are
# absent and would waste a getattr per probe.
_NODE_CHILD_ATTRS: tuple[str, ...] = (
    'value',
    'lhs',
    'rhs',
    'expressions',
    'values',
    'args',
    'rules',
    'selectors',
)


def _stamp_child_attrs() -> None:
    """Compute, once at module load, the structural-child attr tuple
    for every `Node` subclass and stash it on the class as
    `__lessish_child_attrs__`. Lets hot-path walkers like
    `_shift_indices` substitute a single class-attribute lookup for
    per-call introspection — on real-world inputs the saving compounds
    across tens of thousands of visits per compile.

    Walks Node and every descendant exactly once. Subclasses defined
    later (`_ExtendStatement` in `mixins/transform.py`) re-invoke this
    at their definition site to pick up the same stamp.
    """
    from dataclasses import fields as _fields

    def all_descendants(cls: type[Node]) -> set[type[Node]]:
        seen: set[type[Node]] = {cls}
        stack: list[type[Node]] = [cls]
        while stack:
            top = stack.pop()
            for sub in top.__subclasses__():
                if sub not in seen:
                    seen.add(sub)
                    stack.append(sub)
        return seen

    for cls in all_descendants(Node):
        try:
            field_names = tuple(f.name for f in _fields(cls))
        except TypeError:
            # Not a dataclass — skip; only AST nodes get stamped.
            continue
        names = set(field_names)
        cls.__lessish_child_attrs__ = tuple(a for a in _NODE_CHILD_ATTRS if a in names)
        cls.__lessish_all_attrs__ = field_names


_stamp_child_attrs()
