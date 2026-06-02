from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeclAnchor(Enum):
    """Where a deferred `ParseError` should re-anchor on the enclosing
    declaration (the evaluator injects the source offset from this).

    Replaces a bare `'value_end'` / `'decl_start'` string signal so the
    propagation mode is type-checked rather than stringly-typed.
    """

    #: First source offset past the declaration's value text — the
    #: column less.js uses for an invalid hex color followed by a
    #: trailing comment / `;`.
    VALUE_END = 'value_end'
    #: Start of the property name (`d.index`) — the column less.js uses
    #: for `Invalid % without number`.
    DECL_START = 'decl_start'


@dataclass(frozen=True, slots=True)
class SourceLocation:
    filename: str
    line: int
    column: int
    index: int

    def __str__(self) -> str:
        return f'{self.filename}:{self.line}:{self.column}'


class LessError(Exception):
    """Base error class.

    `_less_js_name` is the prefix used when formatting `__str__` —
    matches less.js's error-type naming. Override in subclasses
    where less.js uses a different label (e.g. NameError for
    undefined names, ArgumentError for bad function args).

    Instance-level error metadata (all optional):

    * ``_base_index`` — source-text offset (`int`) the error should
      anchor at. Set by detection sites that know the precise column
      but lack a `Source`; the outer error-formatter uses it to fill
      in `location` / `snippet`.
    * ``_propagate`` — for `ParseError`, a `DeclAnchor` telling the
      evaluator which side of a declaration to re-anchor at.
    * ``_fatal`` — for `ArgumentError`, set by argument validators
      that want the call to abort even in contexts that would
      otherwise swallow the error (e.g. function-as-statement).

    These were `__dict__`-stashed historically; they are now declared
    fields so mypy can see them and direct attribute access works.
    """

    _less_js_name: str = 'Error'

    def __init__(
        self,
        message: str,
        *,
        location: SourceLocation | None = None,
        snippet: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.location = location
        self.snippet = snippet
        self._base_index: int | None = None
        self._propagate: DeclAnchor | None = None
        self._fatal: bool = False

    def __str__(self) -> str:
        # Format mirrors less.js's stderr layout:
        #
        #   <Type>: <msg> in <path> on line <L>, column <C>:
        #   <snippet>
        #
        # Instance-level `_less_js_name` shadows the class default
        # via normal attribute lookup, so the same Python class can
        # surface as `NameError` vs `RuntimeError` depending on
        # context. `_less_js_name` is intentionally NOT set in
        # `__init__` — leaving it unbound at the instance level
        # means `self._less_js_name` falls through to the class
        # attribute (`'Error'` / `'ParseError'` / etc.) unless an
        # explicit override has been assigned.
        name = self._less_js_name
        head = f'{name}: {self.message}'
        if self.location is not None:
            head = f'{head} in {self.location.filename} on line {self.location.line}, column {self.location.column}:'
        out = head
        if self.snippet:
            out = f'{out}\n{self.snippet}'
        return out


class ParseError(LessError):
    _less_js_name = 'ParseError'


class EvalError(LessError):
    _less_js_name = 'SyntaxError'


class UndefinedNameError(EvalError):
    _less_js_name = 'NameError'


class OperationError(EvalError):
    _less_js_name = 'SyntaxError'


class TypeMismatchError(EvalError):
    _less_js_name = 'SyntaxError'


class ArgumentError(EvalError):
    _less_js_name = 'ArgumentError'


class FileError(LessError):
    """Raised when an `@import` target can't be resolved on disk.

    less.js's wording is `FileError: '<path>' wasn't found. Tried -
    <candidate-list>`. The candidate list is paths the resolver tried
    in order: current-file-relative, paths-option, `npm://<spec>`, and
    the raw `<spec>` itself.
    """

    _less_js_name = 'FileError'


class UnsupportedFeatureError(LessError):
    """Raised when a Less feature that `lessish` deliberately doesn't
    implement is encountered in user input — JS evaluation
    (`@plugin "…"`, `` `…` `` backticks) and other RCE-shaped vectors
    aren't silently swallowed, they raise so the user gets an explicit
    signal.
    """

    _less_js_name = 'UnsupportedFeature'


class SecurityError(LessError):
    """Raised when source code attempts file I/O that the active
    `file_io` policy forbids — `'jail'` blocks paths that escape the
    base / search dirs, `'deny'` blocks every file read. Carries the
    rejected path and the policy that made the call so embedders can
    surface a useful diagnostic.
    """

    _less_js_name = 'SecurityError'


class LessishSecurityWarning(UserWarning):
    """Emitted at runtime when an embedder selects a lessish option
    whose security posture they may not have considered. Currently
    used for the opt-in `file_io='allow'` mode (Less source can read
    any file the process can — same as less.js's CLI behaviour, but
    riskier in server-side embeddings of untrusted Less). Note the
    default is the secure `file_io='jail'`; `'allow'` is never implicit.

    Subclass of `UserWarning` so the standard `warnings.filterwarnings`
    machinery silences it cleanly:

        import warnings
        from lessish.errors import LessishSecurityWarning
        warnings.filterwarnings('ignore', category=LessishSecurityWarning)
    """
