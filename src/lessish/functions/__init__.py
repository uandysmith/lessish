"""Function registry — `function-name → callable(args, ctx) → Node`.

Each function module (`color`, `math`, `string`, `lists`, `type_check`)
registers its callables on import. The evaluator looks up a `Call.name`
here at eval time; if the name is unknown the call passes through
unchanged (emits as `name(arg1, ...)`), matching how a CSS-only function
like `linear-gradient(...)` survives a Less compile.

The callable convention:
  fn(args: list[Node], ctx: EvalContext) -> Node

`args` is the already-evaluated list of arguments — one Node per
positional, in source order. Functions raise `ArgumentError` (or any
`LessError` subtype) on bad input.
"""

from __future__ import annotations

from collections.abc import Callable

from ..ast_nodes import Node
from ..context import EvalContext

FunctionImpl = Callable[[list[Node], EvalContext], Node]

_REGISTRY: dict[str, FunctionImpl] = {}


def register(name: str) -> Callable[[FunctionImpl], FunctionImpl]:
    """Decorator: register `fn` under `name` (case-insensitive)."""

    def _decorate(fn: FunctionImpl) -> FunctionImpl:
        _REGISTRY[name.lower()] = fn
        return fn

    return _decorate


def lookup(name: str) -> FunctionImpl | None:
    """Find a registered function by name (case-insensitive)."""
    return _REGISTRY.get(name.lower())


# Special forms handled directly in `eval_call` rather than via the
# registry (they don't take pre-evaluated args). Disabling them is still
# meaningful, so they count as known names.
_SPECIAL_FORMS: frozenset[str] = frozenset({'if', 'boolean', 'isdefined'})


def known_function_names() -> frozenset[str]:
    """Every built-in name a `disabled_functions` entry may legally name
    (lowercased) — registry callables plus the `eval_call` special forms."""
    return frozenset(_REGISTRY) | _SPECIAL_FORMS


# Curated set of built-in functions whose DoS/security risk cannot be
# fully bounded by input validation. Pass to the `disabled_functions`
# compile option (`Lessish(disabled_functions=RESTRICTED_FUNCTIONS)`)
# when compiling untrusted Less. Disabling them is a deliberate
# degradation of Less support in exchange for a guarantee.
#
#   * `replace` — runs a user-supplied regex through Python's `re`,
#     whose catastrophic backtracking happens in C and can't be
#     interrupted or time-bounded in pure stdlib. Input-size and
#     nested-quantifier guards exist but leave a residual ReDoS class
#     (alternation-overlap, e.g. `(a|a)*`) that validation can't catch.
#   * `range` — generates one node per element; an attacker-chosen
#     count is a memory-amplification primitive. Bounded by
#     `range_max_elements` by default, but useless in untrusted UI CSS
#     and worth removing outright in a hardened deployment.
#
# File-reading functions (`data-uri`, `image-*`) are NOT here: they are
# governed by the `file_io` policy — use `file_io='deny'` to block them.
RESTRICTED_FUNCTIONS: frozenset[str] = frozenset({'replace', 'range'})


# Eager import so @register decorators populate _REGISTRY. Placed at the
# bottom of the file to avoid a circular-import — function modules import
# from `lessish.ast_nodes` and `lessish.errors`, not from this module's
# `lookup` (they call `register` only).
from . import color, data_uri, default_module, lists, math, string, svg, type_check  # noqa: F401, E402  # isort:skip
