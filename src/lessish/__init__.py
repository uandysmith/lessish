"""lessish — a pure-Python Less → CSS translator.

Programmatic API
----------------

The package is designed to be embedded; CLI is incidental. The public
entry point is the `Lessish` class — instantiate once and call methods:

>>> from lessish import Lessish
>>> ls = Lessish()
>>> ls.compile('@c: red; .a { color: @c; }')
'.a {\\n  color: red;\\n}\\n'

Per-instance options:

>>> Lessish(compress=True).compile('@c: red; .a { color: @c; }')
'.a{color:red}'

Per-call overrides shadow constructor defaults:

>>> Lessish(compress=True).compile('@c: red; .a { color: @c; }', compress=False)
'.a {\\n  color: red;\\n}\\n'

Pipeline phases are public — `tokenize`, `parse`, `evaluate`, `emit` —
so a tool or language server can stop at any stage. `compile` is just
an orchestrator that calls them in order, and subclasses overriding
any phase will see their override actually used.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from difflib import get_close_matches
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any, TypedDict, cast

try:
    __version__ = _pkg_version('lessish')
except PackageNotFoundError:
    __version__ = '0.0.0+dev'

from .ast_nodes import (
    Anonymous,
    AtRule,
    Comment,
    Declaration,
    Element,
    Extend,
    MixinCallStatement,
    Node,
    Ruleset,
    Selector,
)
from .context import EvalContext
from .errors import (
    ArgumentError,
    EvalError,
    FileError,
    LessError,
    LessishSecurityWarning,
    OperationError,
    ParseError,
    SecurityError,
    SourceLocation,
    TypeMismatchError,
    UndefinedNameError,
    UnsupportedFeatureError,
)
from .evaluator import eval_node
from .functions import RESTRICTED_FUNCTIONS, known_function_names
from .importer import (
    Importer,
    _apply_url_args_in_rules,
    apply_rootpath_to_imports,
    apply_rootpath_to_urls,
)
from .lexer import Token, TokenStream
from .lexer import tokenize as _tokenize
from .mixins import transform_mixins
from .parser import parse as _parse_fn
from .source import Source
from .source_map import SourceMapResult, emit_with_map
from .source_map import SourceMapResult as _SourceMapResult
from .visitors import (
    apply_extends,
    bubble_atrules,
    check_no_root_properties,
    dedup_charset,
    dedup_declarations,
    emit_css,
    join_selectors,
    merge_rules,
    merge_same_path_nested,
)


class _Options(TypedDict, total=False):
    """The full set of compile options, in one typed place.

    Mirrors `Lessish._PIPELINE_DEFAULTS` and the per-stage kwargs.
    `total=False` because any individual call/instance may set only a
    subset — the pipeline always fills the rest from the defaults before
    the dict is treated as `_Options`. Centralising the option surface
    here is what lets `_run_pipeline_body` index and splat `opts`
    without per-line `# type: ignore[arg-type]`.
    """

    filename: str
    paths: tuple[str, ...] | list[str]
    process_imports: bool
    global_vars: dict[str, str] | None
    modify_vars: dict[str, str] | None
    banner: str
    compress: bool
    rewrite_urls: str
    rootpath: str
    url_args: str
    strict_units: bool
    math: str | int
    source_map: bool | dict[str, Any] | None
    file_io: str
    mixin_depth_limit: int | None
    mixin_total_limit: int | None
    interp_expansion_limit: int
    replace_input_limit: int
    range_max_elements: int
    max_eval_seconds: float | None
    max_output_size: int | None
    max_input_size: int | None
    disabled_functions: Iterable[str] | None
    neutralize_escape: bool


# The fixed order of the post-eval structural passes. Ordering is
# constrained by what each pass reads and writes:
#   * `join_selectors` writes `Ruleset.paths`; `apply_extends`,
#     `merge_same_path_nested`, and `merge_rules` all read it, so it must
#     run first.
#   * `bubble_atrules` produces the final at-rule layout; `dedup_charset`
#     and `hoist_imports` read that layout, so they must run after it.
#   * `dedup_declarations` is order-independent of the others.
# `_run_post_eval_visitors` maps each name to its callable and runs them
# in this order.
_POST_EVAL_PASS_ORDER: tuple[str, ...] = (
    'join_selectors',
    'apply_extends',
    'merge_same_path_nested',
    'merge_rules',
    'bubble_atrules',
    'dedup_charset',
    'dedup_declarations',
    'hoist_imports',
)


class Lessish:
    """Owns the Less → CSS compile pipeline.

    Public stages, in order of execution:

    1. `tokenize(source)`  — lexer.
    2. `parse(source)`     — lexer + parser + mixin extraction.
    3. `evaluate(root)`    — `@import` resolution + variable / mixin
                             evaluation + structural post-passes.
    4. `emit(root)`        — CSS serialization (with optional source
                             map).

    `compile(source)` glues 2 → 3 → 4 together; `compile_with_source_map`
    is the same with `source_map=True` and a `SourceMapResult` return
    type. Each stage is a method on this class — subclasses can
    override exactly one (e.g. wrap `emit` with timing, or swap
    `evaluate` for a sandboxed-imports variant) and `compile` will
    use the override automatically.

    Constructor options are baked-in defaults for every call:

    >>> Lessish(compress=True).compile('...')         # compressed
    >>> Lessish(compress=True).compile('...', compress=False)  # not

    Per-compile state (`EvalContext`, `Emitter`, `DefaultGuardState`)
    is built fresh inside each call, never held on the instance. The
    only thing carried by `self` is the immutable `_defaults` dict, so
    independent `compile()` calls — on two instances or in parallel on
    one instance from different threads — never race: each parses its
    own tree and owns it end to end.

    One caveat for the addressable pipeline: `evaluate()` annotates the
    AST it processes (memoised value parses, spliced imports, captured
    closures). So if you `parse()` once and feed the *same* tree to
    several `evaluate()` calls — sequentially or across threads — keep
    the default `copy_input=True`, which clones the tree per call and
    keeps your parsed AST pristine. Only set `copy_input=False` when the
    tree is single-use (as `compile()` does internally).
    """

    # Baseline option values used when neither the constructor's
    # `**defaults` nor a per-call override sets a key. Semantics
    # mirror less.js:
    #   filename         — logical path; base for `@import` resolution
    #                      and source-map `sources[]`.
    #   paths            — extra `@import` search dirs.
    #   process_imports  — splice imported rules before eval.
    #   global_vars      — `{name: text}` prepended as synthetic
    #                      `@name: text;` (user code can override).
    #   modify_vars      — `{name: text}` appended (overrides user code).
    #   banner           — verbatim string prefix on the emitted CSS.
    #   compress         — single-line, no-whitespace output.
    #   rewrite_urls     — `'all' | 'local' | 'off'` for url(…)
    #                      rewriting in imported files.
    #   rootpath         — prefix prepended to every url(…) at emit.
    #   url_args         — appended to every url(…) as a query string;
    #                      `data:` URIs exempt, `#fragment` preserved.
    #   strict_units     — make compound units raise at emit time.
    #   math             — `'always' | 'parens-division' | 'parens'`,
    #                      plus less.js's numeric aliases (0/1/2/3/4).
    #   source_map       — None = no map; True / dict = Source Maps v3.
    #   file_io          — `'allow' | 'jail' | 'deny'`. Governs every
    #                      filesystem read triggered by the Less source
    #                      (`@import`, `@import (inline)`, `data-uri()`,
    #                      `image-size()`/`-width`/`-height`). Default
    #                      `'jail'` — reads are confined to `base_dir` /
    #                      `paths` (absolute paths and `..`-escapes
    #                      raise `SecurityError`) — secure by default for
    #                      embedders compiling Less they do not control.
    #                      `'allow'` mirrors less.js (Less source can read
    #                      any file the process can) and emits a
    #                      `LessishSecurityWarning` every time it is
    #                      selected. `'deny'` blocks every file-touching
    #                      feature with `SecurityError`.
    #   mixin_depth_limit / mixin_total_limit
    #                    — DoS backstops on mixin invocation; `None`
    #                      uses the `evaluator.MIXIN_*_LIMIT` defaults.
    #   interp_expansion_limit
    #                    — max bytes a single `@{var}` interpolation may
    #                      expand to (billion-laughs guard).
    #   replace_input_limit
    #                    — max pattern/subject length for the `replace()`
    #                      Less function (ReDoS input bound).
    #   range_max_elements
    #                    — max element count `range()` may generate
    #                      (memory-blowup guard).
    #   disabled_functions
    #                    — built-in function names to refuse at call
    #                      time (`UnsupportedFeatureError`). `None` =
    #                      none disabled. See `RESTRICTED_FUNCTIONS`.
    _PIPELINE_DEFAULTS: _Options = {
        'filename': '<input>',
        'paths': (),
        'process_imports': True,
        'global_vars': None,
        'modify_vars': None,
        'banner': '',
        'compress': False,
        'rewrite_urls': 'off',
        'rootpath': '',
        'url_args': '',
        'strict_units': False,
        'math': 'parens-division',
        'source_map': None,
        'file_io': 'jail',
        'mixin_depth_limit': None,
        'mixin_total_limit': None,
        'interp_expansion_limit': 1_000_000,
        'replace_input_limit': 100_000,
        'range_max_elements': 1_000_000,
        'max_eval_seconds': None,
        'max_output_size': None,
        'max_input_size': None,
        'disabled_functions': None,
        'neutralize_escape': False,
    }

    # less.js's `math` option accepts a name OR a numeric enum. Both
    # spellings map onto the three canonical gates used by the evaluator;
    # unknown values fall back to `parens-division`.
    _MATH_ALIASES: dict[Any, str] = {
        0: 'always',
        1: 'parens-division',
        2: 'parens',
        3: 'parens',  # less.js 4+: `strict` is an alias for `parens`.
        4: 'parens',  # less.js: `strict-legacy` collapses onto `parens`.
        'always': 'always',
        'parens-division': 'parens-division',
        'parens': 'parens',
        'strict': 'parens',
        'strict-legacy': 'parens',
    }

    # The complete set of option names a caller may legitimately set —
    # the pipeline defaults plus `strict_imports` (accepted-but-no-op,
    # mirroring less.js's own deprecation). Derived from
    # `_PIPELINE_DEFAULTS` so the two can never drift. Anything outside
    # this set passed to the constructor or a per-call override is a
    # typo (`compres=True`) or a non-existent option, and is rejected
    # loudly rather than silently ignored. The internal driver kwargs
    # (`src`, `want_source_map_result`) are deliberately NOT here — they
    # are not user-settable and are stripped by `_INTERNAL_PIPELINE_KWARGS`.
    _VALID_OPTION_KEYS: frozenset[str] = frozenset(_PIPELINE_DEFAULTS) | {'strict_imports'}

    # Options a `hardened()` instance freezes: a per-call override that
    # *weakens* any of these is refused (see `_enforce_security_lock`).
    _SECURITY_OPTION_KEYS: frozenset[str] = frozenset(
        {'file_io', 'disabled_functions', 'neutralize_escape', 'max_eval_seconds', 'max_output_size', 'max_input_size'}
    )
    # `file_io` policies from most to least restrictive. A per-call
    # override may move up this scale or hold; never down.
    _FILE_IO_RANK: dict[str, int] = {'deny': 2, 'jail': 1, 'allow': 0}

    __slots__ = ('_defaults', '_locked_security')

    def __init__(self, **defaults: object) -> None:
        """Store baseline options. Accepts every keyword `compile()`
        takes (`filename`, `paths`, `compress`, `math`, …); see
        `_PIPELINE_DEFAULTS` above for the full list.

        Unknown keywords raise `TypeError` (with a did-you-mean hint)
        rather than being silently dropped — a misspelled option such as
        `compress` → `compres` would otherwise compile with the default
        silently in force.
        """
        self._reject_unknown_options(defaults.keys(), where='Lessish()')
        self._defaults = defaults
        # Non-None only for instances built via `hardened()` — holds the
        # frozen security baseline that per-call overrides may not weaken.
        self._locked_security: dict[str, object] | None = None

    @classmethod
    def hardened(
        cls,
        *,
        max_eval_seconds: float = 10.0,
        max_output_size: int = 50_000_000,
        max_input_size: int = 10_000_000,
        **overrides: object,
    ) -> Lessish:
        """A `Lessish` preset for compiling **untrusted** Less, bundling
        every guard that has to be opted into individually:

        * `file_io='deny'` — no `@import` or `data-uri()` disk reads (the
          only way to block the file-reading functions; `disabled_functions`
          alone does not cover them).
        * `disabled_functions=RESTRICTED_FUNCTIONS` — drops `replace`
          (residual ReDoS in C-level regex) and `range` (output
          amplification).
        * `neutralize_escape=True` — untrusted Less can't smuggle
          `</style>` into an inlined `<style>` block through any output
          path (values, selectors, property names, comments, preludes).
        * `max_eval_seconds` / `max_output_size` — a wall-clock deadline
          (checked throughout evaluation — the cycle trampoline, the
          per-declaration path, and mixin invocation) and an output
          ceiling that catch exponential expansion *and* a large flat
          workload (the per-invocation `mixin_total_limit` is always on
          regardless). Neither interrupts a single uninterruptible
          C-level call; ReDoS in `replace` is handled by dropping it
          (above), not here.
        * `max_input_size` — a cumulative character budget on the parser
          input (entry source + every imported file), checked before
          lexing. Bounds the one phase the wall-clock deadline does not:
          parsing is O(n) and runs ahead of evaluation.

        A keyword passed *here* overrides a preset default —
        `Lessish.hardened(compress=True)`. But the security-critical
        options above are then **locked**: a later
        `compile(**overrides)` may make them *stricter* (tighter budget,
        more disabled functions, `deny` over `jail`) but a *weakening*
        override raises `SecurityError` instead of silently widening the
        sandbox. Build a separate instance if you need a looser policy.
        """
        opts: dict[str, object] = {
            'file_io': 'deny',
            'disabled_functions': RESTRICTED_FUNCTIONS,
            'neutralize_escape': True,
            'max_eval_seconds': max_eval_seconds,
            'max_output_size': max_output_size,
            'max_input_size': max_input_size,
        }
        opts.update(overrides)
        inst = cls(**opts)
        inst._locked_security = {k: opts[k] for k in cls._SECURITY_OPTION_KEYS}
        return inst

    @classmethod
    def _reject_unknown_options(cls, keys: Iterable[str], *, where: str) -> None:
        """Raise `TypeError` for any option name not in
        `_VALID_OPTION_KEYS`, appending a `difflib` did-you-mean
        suggestion when a close match exists. `where` names the call
        site (e.g. `'Lessish()'` or `'compile()'`) for the message.
        """
        unknown = [k for k in keys if k not in cls._VALID_OPTION_KEYS]
        if not unknown:
            return
        bits: list[str] = []
        for k in sorted(unknown):
            close = get_close_matches(k, cls._VALID_OPTION_KEYS, n=1)
            hint = f' (did you mean {close[0]!r}?)' if close else ''
            bits.append(f'{k!r}{hint}')
        raise TypeError(f'{where} got unknown option(s): {", ".join(bits)}')

    def _enforce_security_lock(self, overrides: dict[str, object]) -> None:
        """Reject a per-call override that weakens a `hardened()` instance's
        frozen security baseline. A no-op for a plain `Lessish()`.

        Strengthening is allowed — a tighter `max_eval_seconds`, more
        `disabled_functions`, or `deny` over `jail` all pass. Only a
        weakening override raises `SecurityError`, so splatting a
        config-derived options dict into `compile()` can't silently undo
        the sandbox `hardened()` set up.
        """
        locked = self._locked_security
        if locked is None:
            return
        for key in self._SECURITY_OPTION_KEYS:
            if key in overrides and self._weakens_security(key, overrides[key], locked[key]):
                raise SecurityError(
                    f'compile() option {key!r}={overrides[key]!r} would weaken the hardened() '
                    f'sandbox (locked at {locked[key]!r}). Build a separate Lessish instance '
                    f'if you need a looser policy.'
                )

    @classmethod
    def _weakens_security(cls, key: str, new: object, locked: object) -> bool:
        """True if per-call value `new` is *less* restrictive than the
        frozen `locked` baseline for security option `key`."""
        if key == 'file_io':
            rank = cls._FILE_IO_RANK
            return rank.get(str(new), -1) < rank.get(str(locked), -1)
        if key == 'neutralize_escape':
            return bool(locked) and not bool(new)
        if key == 'disabled_functions':
            # Locked set must stay a subset of whatever is disabled now;
            # dropping any locked-disabled name re-enables it. Case-fold
            # to match the engine's own lowercasing of function names.
            def _fold(names: object) -> set[str]:
                # A bare string is one name, not a set of characters.
                if names is None:
                    return set()
                if isinstance(names, str) or not isinstance(names, Iterable):
                    items: Iterable[object] = [names]
                else:
                    items = names
                return {str(n).lower() for n in items}

            return not _fold(locked) <= _fold(new)
        if key in ('max_eval_seconds', 'max_output_size', 'max_input_size'):
            # A concrete numeric cap under hardened(); `None` removes the
            # bound and a larger number loosens it.
            if locked is None:
                return False
            if new is None:
                return True
            return float(new) > float(locked)  # type: ignore[arg-type]
        return False  # pragma: no cover - keys enumerated by _SECURITY_OPTION_KEYS

    @staticmethod
    def _check_input_size(src: Source, limit: int | None) -> None:
        """Reject input larger than `limit` characters *before* lexing.

        Parsing is O(n) and runs ahead of the eval-time wall-clock
        budget, so an oversized top-level source would otherwise burn
        CPU and memory unbounded. Imported files are bounded separately
        by the same budget inside the `Importer`. A no-op when `limit`
        is None.
        """
        if limit is None:
            return
        n = len(src.text)
        if n > limit:
            err = ParseError(f'input exceeds max_input_size ({n} > {limit} characters)')
            err.location = src.location_at(limit)
            err.snippet = src.snippet_around(limit)
            raise err

    def tokenize(
        self,
        source: str | Source,
        /,
        *,
        filename: str = '<input>',
        max_input_size: int | None = None,
    ) -> TokenStream:
        """Lex Less source into a `TokenStream`. Iterates, indexes, and
        `len()`s as a flat sequence of tokens, so callers that want
        `list[Token]` semantics (`for tok in tokens:`, `tokens[0]`,
        `list(tokens)`) work as before. The stream also carries the
        originating `Source` and a cursor, which `parse()` consumes
        directly when handed the stream — skipping a re-lex pass.

        `max_input_size` bounds the source length (characters) before
        lexing; `None` (default) is off. The full `compile()` pipeline
        applies the same budget — this kwarg is for tooling that lexes
        untrusted input through `tokenize()`/`parse()` directly.

        Raises
        ------
        ParseError
            If the source contains a character the lexer can't
            classify (e.g. an unclosed string literal), or the source
            exceeds `max_input_size`.
        """
        src = source if isinstance(source, Source) else Source(text=source, filename=filename)
        self._check_input_size(src, max_input_size)
        return _tokenize(src)

    def parse(
        self,
        source: str | Source | TokenStream,
        /,
        *,
        filename: str = '<input>',
        max_input_size: int | None = None,
    ) -> Ruleset:
        """Parse Less source (or a pre-tokenised `TokenStream`) into a
        root `Ruleset` AST.

        Passing a `TokenStream` (typically the return value of an
        earlier `tokenize()` call on the same input) skips the lex
        pass. The stream's cursor is reset to 0 on entry; the stream
        is consumed in place, so re-passing it after `parse()` returns
        will produce an empty parse.

        The returned tree has selectors, blocks, declarations,
        at-rules, and `MixinDefinition` nodes already extracted from
        their source-form Rulesets. Declaration values are still raw
        text — those get re-parsed lazily inside `evaluate()`.

        `max_input_size` bounds the source length (characters) before
        lexing; `None` (default) is off. It is ignored when a
        pre-tokenised `TokenStream` is passed (the budget applies at
        `tokenize()` time). See `tokenize()` for the rationale.

        Raises
        ------
        ParseError
            If tokenization or parsing fails, or the source exceeds
            `max_input_size`.
        """
        if isinstance(source, TokenStream):
            src = source.source
            parser_input: Source | TokenStream = source
        else:
            src = source if isinstance(source, Source) else Source(text=source, filename=filename)
            self._check_input_size(src, max_input_size)
            parser_input = src
        try:
            root = _parse_fn(parser_input)
        except ParseError as e:
            self._attach_base_index_location(e, src)
            raise
        except RecursionError:
            raise self._recursion_error(ParseError, src) from None
        try:
            root_node = transform_mixins(root)
        except LessError as e:
            # `transform_mixins` is structurally part of parsing, so
            # its semantic rejections (e.g. multi-selector guard)
            # anchor at the same source position as a parse error.
            self._attach_base_index_location(e, src)
            raise
        if not isinstance(root_node, Ruleset):  # pragma: no cover - internal invariant
            raise TypeError('lessish internal error: parser did not yield a Ruleset root')
        return root_node

    def evaluate(
        self,
        root: Ruleset,
        /,
        *,
        src: Source | None = None,
        copy_input: bool = True,
        process_imports: bool = True,
        paths: list[str] | tuple[str, ...] = (),
        rewrite_urls: str = 'off',
        rootpath: str = '',
        url_args: str = '',
        strict_units: bool = False,
        math: str | int = 'parens-division',
        file_io: str = 'jail',
        mixin_depth_limit: int | None = None,
        mixin_total_limit: int | None = None,
        interp_expansion_limit: int = 1_000_000,
        replace_input_limit: int = 100_000,
        range_max_elements: int = 1_000_000,
        max_eval_seconds: float | None = None,
        max_input_size: int | None = None,
        disabled_functions: Iterable[str] | None = None,
        **_extra: object,
    ) -> Ruleset:
        """Run the evaluator on a parsed Ruleset.

        `src` is used for error attribution and the importer's
        base-path. When called standalone (without a Source — e.g.
        from tests that built a Ruleset by hand), errors won't carry
        a `<file>:line:column` band but evaluation still works.

        `copy_input` (default True): deep-copy `root` before evaluating
        so that eval's per-compile annotations — memoised value parses,
        `@import` splicing, detached-ruleset closure capture — land on a
        private clone and never mutate the AST the caller passed in. This
        is what makes a `parse()` tree safe to **reuse** across multiple
        `evaluate()` calls or **share** between threads (each compile gets
        its own copy). Pass `copy_input=False` when you own the tree and
        will not reuse it (e.g. you just parsed it for a single compile);
        the internal `compile()` pipeline does exactly that to avoid the
        clone on its hot path.

        Extra kwargs are accepted and ignored so callers can splat a
        full options dict (compile passes the whole `opts` through).
        """
        if src is None:
            src = Source(text='', filename='<input>')

        if copy_input:
            # Belt-and-braces isolation: evaluation reads variable/mixin
            # definitions out of the input frames and memoises parsed
            # values, splices imports, and stamps closures onto nodes. On
            # a shared/reused tree those writes would be visible to the
            # caller (and, under a free-threaded interpreter, a genuine
            # data race). Cloning up front confines every mutation to a
            # tree only this call can see.
            root = deepcopy(root)

        # `processImports: False` does not bypass the Importer — it
        # still routes per-spec (drop less-shaped, pass-through
        # css-shaped) to match less.js's option semantics. The instance
        # is wired into `EvalContext` so deferred
        # `@import "@{var}.less"` path-substitution at eval time gets
        # the same routing.
        importer = Importer(
            source_path=src.filename,
            paths=list(paths),
            rewrite_urls=rewrite_urls,
            rootpath=rootpath,
            url_args=url_args,
            process_imports=process_imports,
            file_io=file_io,
            max_input_size=max_input_size,
            # The entry source is pre-charged so the budget bounds the
            # *cumulative* input (entry + every imported file), not each
            # file independently.
            entry_input_chars=len(src.text),
        )
        try:
            root = importer.resolve(root)
        except LessError as e:
            self._attach_base_index_location(e, src)
            raise

        ctx = EvalContext(
            source=src,
            strict_units=strict_units,
            math=self._normalize_math(math),
            importer=importer,
            mixin_depth_limit=mixin_depth_limit,
            mixin_total_limit=mixin_total_limit,
            interp_expansion_limit=interp_expansion_limit,
            replace_input_limit=replace_input_limit,
            range_max_elements=range_max_elements,
            max_eval_seconds=max_eval_seconds,
            disabled_functions=self._normalize_disabled_functions(disabled_functions),
        )
        try:
            evaled = eval_node(root, ctx)
            if not isinstance(evaled, Ruleset):  # pragma: no cover - internal invariant
                raise TypeError('lessish internal error: evaluator did not yield a Ruleset')

            self._run_post_eval_visitors(
                evaled,
                rewrite_urls=rewrite_urls,
                rootpath=rootpath,
                url_args=url_args,
            )
        except RecursionError:
            raise self._recursion_error(EvalError, src) from None
        return evaled

    def emit(
        self,
        root: Ruleset,
        /,
        *,
        src: Source | None = None,
        compress: bool = False,
        strict_units: bool = False,
        banner: str = '',
        source_map: bool | dict[str, Any] | None = None,
        filename: str = '<input>',
        want_source_map_result: bool = False,
        max_output_size: int | None = None,
        neutralize_escape: bool = False,
        **_extra: object,
    ) -> str | SourceMapResult:
        """Serialize an evaluated Ruleset to CSS.

        Returns a plain CSS string by default. If `source_map` is
        truthy AND `want_source_map_result` is True, returns a
        `SourceMapResult` carrying `.css`, `.map_json`, and
        `.annotation_url`. Otherwise (source_map truthy but
        `want_source_map_result` False), the CSS string already
        contains the trailing `/*# sourceMappingURL=… */` annotation
        but the map JSON is discarded.

        Extra kwargs are accepted and ignored.
        """
        if src is None:
            src = Source(text='', filename=filename)

        try:
            check_no_root_properties(root)
            if source_map:
                result = emit_with_map(
                    root,
                    src,
                    source_map,
                    filename=filename,
                    compress=compress,
                    strict_units=strict_units,
                    neutralize_escape=neutralize_escape,
                    max_output_size=max_output_size,
                )
                css = (banner + result.css) if banner else result.css
                if want_source_map_result:
                    return SourceMapResult(
                        css=css,
                        map_json=result.map_json,
                        annotation_url=result.annotation_url,
                    )
                return css
            css = emit_css(
                root,
                compress=compress,
                strict_units=strict_units,
                max_output_size=max_output_size,
                neutralize_escape=neutralize_escape,
            )
        except LessError as e:
            self._attach_base_index_location(e, src)
            raise
        except RecursionError:
            raise self._recursion_error(EvalError, src) from None
        return (banner + css) if banner else css

    def compile(self, source: str, /, **overrides: object) -> str:
        """Translate Less source text to CSS.

        Per-call `**overrides` are merged on top of `self._defaults`
        (the constructor's baseline). Hard-coded defaults
        (`compress=False`, …) apply only when neither layer provides
        a value. Overrides win even when set to a falsy value —
        `Lessish(compress=True).compile(src, compress=False)`
        produces pretty-printed CSS.

        See `_PIPELINE_DEFAULTS` for the full set of accepted keys;
        an unknown key raises `TypeError` (with a did-you-mean hint)
        so a misspelled option can't silently fall back to its default.
        """
        result = self._run_pipeline(source, overrides, want_source_map_result=False)
        if not isinstance(result, str):  # pragma: no cover - internal invariant
            raise TypeError('lessish internal error: compile() did not yield a str')
        return result

    def compile_with_source_map(self, source: str, /, **overrides: object) -> SourceMapResult:
        """Compile and return a `SourceMapResult` carrying the rendered
        CSS, the Source Maps v3 JSON, and the annotation URL. Override
        semantics match `compile()` — see that method's docstring.
        """
        if 'source_map' not in overrides:
            overrides = {**overrides, 'source_map': True}
        result = self._run_pipeline(source, overrides, want_source_map_result=True)
        if not isinstance(result, _SourceMapResult):  # pragma: no cover - internal invariant
            raise TypeError('lessish internal error: compile_with_source_map() did not yield a SourceMapResult')
        return result

    def _run_pipeline(
        self,
        source: str,
        overrides: dict[str, object],
        *,
        want_source_map_result: bool,
    ) -> Any:
        # Reject typo'd / non-existent per-call options before they get
        # merged away into the defaults and silently ignored.
        self._reject_unknown_options(overrides.keys(), where='compile()')
        # A `hardened()` instance refuses a per-call override that would
        # loosen its security baseline (no-op for a plain instance).
        self._enforce_security_lock(overrides)
        # Precedence: per-call overrides > constructor defaults >
        # pipeline defaults.
        opts = {**self._PIPELINE_DEFAULTS, **self._defaults, **overrides}

        # `strict_imports` is documented as accepted-but-no-op (mirroring
        # less.js's own deprecation). Silently swallowing a
        # security-relevant flag is a footgun — warn explicitly so
        # embedders learn that toggling it does nothing. Triggered only
        # when the caller explicitly passes the option (truthy or falsy);
        # the implicit pipeline default never warns.
        self._maybe_warn_strict_imports(overrides)

        # `file_io='allow'` lets Less source read any file the process
        # can (the less.js-compatible mode). The secure-by-default value
        # is `'jail'`; `'allow'` is opt-in. Whenever a compile actually
        # runs under `'allow'`, warn — every time, not once — so the
        # risk is visible at each call site that selected it. Callers
        # that want `'allow'` silently (e.g. the CLI, which is an
        # explicit interactive invocation) suppress the category via
        # `warnings.filterwarnings('ignore', category=LessishSecurityWarning)`.
        self._maybe_warn_allow_file_io(opts)

        return self._run_pipeline_body(source, opts, want_source_map_result)

    # Kwarg names the pipeline driver passes through to stage methods
    # itself. If a caller smuggles one of these into `compile(**overrides)`
    # (or the constructor's defaults), splatting `**opts` on top of the
    # explicit kwargs raises `TypeError: got multiple values for keyword
    # argument 'src'`. Stripped silently — they are not options users
    # can meaningfully set from outside the pipeline.
    _INTERNAL_PIPELINE_KWARGS: frozenset[str] = frozenset({'src', 'want_source_map_result'})

    def _run_pipeline_body(
        self,
        source: str,
        opts: dict[str, object],
        want_source_map_result: bool,
    ) -> Any:
        # Strip the internal driver kwargs (`src`, `want_source_map_result`)
        # if a caller smuggled them in — splatting them again below would
        # collide with the explicit kwargs. Done on the plain dict before
        # the `_Options` view so the non-option keys don't fight the
        # TypedDict.
        for k in self._INTERNAL_PIPELINE_KWARGS:
            opts.pop(k, None)
        # Every key is now one of the typed compile options (the merge in
        # `_run_pipeline` always layers `_PIPELINE_DEFAULTS` underneath),
        # so view it as `_Options` — this is what makes the indexing and
        # the `**opts` splats below type-check without per-line ignores.
        typed = cast('_Options', opts)

        injected = self._inject_vars(source, typed['global_vars'], typed['modify_vars'])
        src = Source(text=injected, filename=typed['filename'])

        # Bound the parser's input *before* lexing. Done here rather than
        # via `self.parse(...)` so a subclass override of `parse` (with the
        # public two-arg signature) isn't handed an unexpected kwarg.
        self._check_input_size(src, typed['max_input_size'])

        # Each stage picks out its own kwargs and ignores the rest via
        # `**_extra`. Subclass overrides of `parse`, `evaluate`, or
        # `emit` are honored here.
        root = self.parse(src)
        # `copy_input=False`: this tree was just parsed here and is never
        # handed back to the caller, so the defensive clone in evaluate()
        # would be pure overhead on the compile hot path.
        evaluated = self.evaluate(root, src=src, copy_input=False, **typed)
        return self.emit(
            evaluated,
            src=src,
            want_source_map_result=want_source_map_result,
            **typed,
        )

    def _maybe_warn_allow_file_io(self, opts: dict[str, object]) -> None:
        """Emit `LessishSecurityWarning` whenever a compile runs under
        the effective `file_io='allow'` policy.

        The default is the secure `'jail'`, so this fires only when a
        caller explicitly opted into `'allow'` (via the constructor's
        `_defaults` or a per-call override) — and it fires on *every*
        such compile, not once per process, so the risk stays visible
        at each call site. Silence it deliberately with
        `warnings.filterwarnings('ignore', category=LessishSecurityWarning)`
        (the CLI does exactly this — `file_io='allow'` there is an
        explicit, interactive choice).
        """
        import warnings

        if opts.get('file_io') != 'allow':
            return
        warnings.warn(
            "lessish: file_io='allow' lets Less source read any file the "
            'process can. This is the less.js-compatible mode, not the '
            "default — prefer file_io='jail' (or 'deny') for input you do "
            "not control. Silence with warnings.filterwarnings('ignore', "
            'category=lessish.LessishSecurityWarning).',
            LessishSecurityWarning,
            stacklevel=4,
        )

    def _maybe_warn_strict_imports(self, overrides: dict[str, object]) -> None:
        """Emit a `DeprecationWarning` when the caller explicitly passes
        `strict_imports` (in either the constructor's `_defaults` or the
        per-call `overrides`). The option is accepted but does nothing —
        less.js itself deprecated it and our lack of behaviour mirrors
        that. We warn rather than silently swallow so embedders learn
        that flipping the flag does not actually tighten anything.

        `stacklevel=4` so the warning's "from" frame points at the
        embedder's `Lessish(...).compile(...)` call site:
        embedder → compile → _run_pipeline → _maybe_warn_strict_imports.
        """
        import warnings

        if 'strict_imports' in overrides or 'strict_imports' in self._defaults:
            warnings.warn(
                "lessish: 'strict_imports' is accepted but has no effect "
                "(matches less.js's own deprecation). The option will be "
                'removed in a future release; drop it from your call.',
                DeprecationWarning,
                stacklevel=4,
            )

    def _inject_vars(
        self,
        source: str,
        global_vars: dict[str, str] | None,
        modify_vars: dict[str, str] | None,
    ) -> str:
        """Synthesize `@name: value;` declarations from the
        `globalVars` / `modifyVars` options. Mirrors less.js:
        `globalVars` go at the START (user code can override them);
        `modifyVars` go at the END (they override user code).
        """
        if global_vars:
            prefix = ''.join(f'@{name}: {value};\n' for name, value in global_vars.items())
            source = prefix + source
        if modify_vars:
            suffix = ''.join(f'\n@{name}: {value};' for name, value in modify_vars.items())
            source = source + suffix
        return source

    def _run_post_eval_visitors(
        self,
        evaled: Ruleset,
        *,
        rewrite_urls: str,
        rootpath: str,
        url_args: str,
    ) -> None:
        """Apply the structural visitors (`join_selectors`,
        `apply_extends`, `merge_rules`, …) followed by the URL
        post-rewrites gated by `rootpath` / `url_args`. All passes
        mutate `evaled` in place.

        Ordering matters; the sequence and the constraints behind it live
        in `_POST_EVAL_PASS_ORDER`. This method maps each pass name to its
        callable and runs them in that order.
        """
        runners = {
            'join_selectors': join_selectors,
            'apply_extends': apply_extends,
            'merge_same_path_nested': merge_same_path_nested,
            'merge_rules': merge_rules,
            'bubble_atrules': bubble_atrules,
            'dedup_charset': dedup_charset,
            'dedup_declarations': dedup_declarations,
            # Dispatched via `self` so a subclass override is honored.
            'hoist_imports': self._hoist_imports,
        }
        for pass_name in _POST_EVAL_PASS_ORDER:
            runners[pass_name](evaled)

        # less.js: `rootpath` is independent of `rewriteUrls` — it
        # applies to every relative `url(...)` and every surviving
        # top-level `@import` path. The importer's
        # `_rewrite_urls_in_rules` already handled urls in imported
        # files when `rewrite_urls != 'off'`; this post-pass covers
        # urls in the outer file plus the `rewrite_urls='off'` case.
        if rootpath:
            if rewrite_urls == 'off':
                evaled.rules = apply_rootpath_to_urls(evaled.rules, rootpath)
            evaled.rules = apply_rootpath_to_imports(evaled.rules, rootpath)
        # `urlArgs` runs AFTER eval so it sees the final url() paths
        # (variable substitutions, mixin output, function returns all
        # already collapsed into the declaration text).
        if url_args:
            evaled.rules = _apply_url_args_in_rules(evaled.rules, url_args)

    @classmethod
    def _normalize_math(cls, math: str | int) -> str:
        """Translate a less.js-shaped `math` option (string or numeric
        enum) into one of `'always'`, `'parens-division'`, `'parens'`.
        Unknown values fall back to the `parens-division` default.
        """
        return cls._MATH_ALIASES.get(math, 'parens-division')

    @staticmethod
    def _normalize_disabled_functions(names: Iterable[str] | None) -> frozenset[str]:
        """Lowercase and validate the `disabled_functions` names. An entry
        that names no real built-in is almost always a typo (`'replace '`
        with a stray space, `'darkenn'`) that would silently provide zero
        protection, so reject it with a did-you-mean hint rather than
        accept a no-op."""
        if not names:
            return frozenset()
        known = known_function_names()
        wanted = {name.lower() for name in names}
        unknown = sorted(wanted - known)
        if unknown:
            hints = []
            for name in unknown:
                close = get_close_matches(name, known, n=1)
                hints.append(f'{name!r}' + (f' (did you mean {close[0]!r}?)' if close else ''))
            raise ValueError('disabled_functions names no such built-in function: ' + ', '.join(hints))
        return frozenset(wanted)

    @staticmethod
    def _hoist_imports(root: Ruleset) -> None:
        """less.js: every surviving `@import` directive at root level
        emits BEFORE other rules — except `@charset`, which must come
        first. Trailing comments (extracted from the import's
        prelude) stay at the source location; only the import
        statement itself moves.

        Leading comments (those appearing before any `@import` in
        source order) stick with the hoisted block so they render
        between `@charset` and the first `@import`, matching less.js.
        """
        # Comments before the first surviving `@import` are "leading"
        # and ride with the hoisted block.
        first_import_index: int | None = None
        for i, r in enumerate(root.rules):
            if isinstance(r, AtRule) and r.name == '@import' and r.body is None:
                first_import_index = i
                break

        charsets: list[Node] = []
        imports: list[Node] = []
        leading_comments: list[Node] = []
        rest: list[Node] = []
        for i, r in enumerate(root.rules):
            if isinstance(r, AtRule) and r.name == '@charset':
                charsets.append(r)
                continue
            if isinstance(r, AtRule) and r.name == '@import' and r.body is None:
                hoisted = AtRule(
                    index=r.index,
                    name=r.name,
                    prelude=r.prelude,
                    body=None,
                )
                imports.append(hoisted)
                rest.extend(r.trailing_comments)
                continue
            if first_import_index is not None and i < first_import_index and isinstance(r, Comment):
                leading_comments.append(r)
                continue
            rest.append(r)
        root.rules = charsets + leading_comments + imports + rest

    @staticmethod
    def _recursion_error(exc: type[LessError], src: Source) -> LessError:
        """Build a `LessError` standing in for a `RecursionError`.

        The parser/evaluator bound their own nesting (`MAX_PARSE_DEPTH`,
        the mixin budget), so this is a backstop for any recursion path
        those explicit guards don't gate. It keeps the public contract
        that everything thrown out of the pipeline is a `LessError` —
        a raw `RecursionError` (a `RuntimeError`) would escape that.
        """
        err = exc('maximum nesting depth exceeded')
        err.location = src.location_at(0)
        err.snippet = src.snippet_around(0)
        return err

    @staticmethod
    def _attach_base_index_location(err: LessError, src: Source) -> None:
        """If a strict-mode error carries a `_base_index` attribute
        (set by detection points outside the parser proper), use it
        to populate `err.location` so the error message renders the
        right `line N, column M` band.
        """
        if err.location is not None:
            return
        base = err._base_index
        if base is None:
            return
        err.location = src.location_at(int(base))
        err.snippet = src.snippet_around(int(base))


__all__ = [
    '__version__',
    'Lessish',
    'Source',
    'SourceLocation',
    'SourceMapResult',
    'Token',
    'TokenStream',
    'Node',
    'Anonymous',
    'AtRule',
    'Comment',
    'Declaration',
    'Element',
    'Extend',
    'MixinCallStatement',
    'Ruleset',
    'Selector',
    'LessError',
    'ParseError',
    'EvalError',
    'UndefinedNameError',
    'OperationError',
    'TypeMismatchError',
    'ArgumentError',
    'FileError',
    'UnsupportedFeatureError',
    'SecurityError',
    'LessishSecurityWarning',
    'RESTRICTED_FUNCTIONS',
]
