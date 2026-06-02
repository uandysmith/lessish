"""The `default()` guard helper.

`default()` returns a boolean keyword whose value is set by the mixin-
matcher right before evaluating a guard. The matcher does two passes —
one with `default()` returning false, one true — and uses the results
to break ties between explicit and fallback mixin branches.

State lives on a `DefaultGuardState` instance attached to each
`EvalContext` (no module-level globals): the matcher saves/restores
it around `eval_condition` calls. Plain calls to `default()` outside
guard evaluation default to `True` (any standalone use means "the
matcher hasn't told me otherwise" — less.js does the same).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ast_nodes import Keyword, Node
from ..context import EvalContext
from ..errors import ArgumentError, EvalError
from . import register


@dataclass
class DefaultGuardState:
    """Per-compile mutable state for `default()`.

    Attached to `EvalContext.default_state` so two concurrent compiles
    (or two `Lessish` instances) don't see each other's matcher state.

    `in_guard` and `in_css_guard` are nesting depth counters, not
    bools — nested guards need balanced push/pop.
    """

    value: bool = True
    in_guard: int = 0
    in_css_guard: int = 0

    def set_default_value(self, new: bool) -> bool:
        """Update the active value and return the previous one. The
        matcher uses this to push/pop around guard evaluation.
        """
        prev = self.value
        self.value = new
        return prev

    def set_in_guard(self, active: bool) -> None:
        """Toggle whether we're currently evaluating a parametric-mixin
        guard. `default()` resolves to the matcher's per-candidate
        flag only inside a guard.
        """
        if active:
            self.in_guard += 1
        else:
            self.in_guard = max(0, self.in_guard - 1)

    def set_in_css_guard(self, active: bool) -> None:
        """Toggle whether we're currently evaluating a CSS-guard
        (`selector when (...)` on a non-parametric ruleset).
        `default()` is rejected outright in this context.
        """
        if active:
            self.in_css_guard += 1
        else:
            self.in_css_guard = max(0, self.in_css_guard - 1)


@register('default')
def fn_default(args: list[Node], ctx: EvalContext) -> Node:
    state = ctx.default_state
    if state.in_css_guard:
        # `default()` inside a CSS-guard is rejected by less.js with a
        # SyntaxError. Raise EvalError so the call propagates with the
        # right wording.
        raise EvalError('Error evaluating function `default`: it is currently only allowed in parametric mixin guards,')
    if not state.in_guard:
        # Outside any guard the call has no defined meaning — raise
        # ArgumentError so `eval_call` emits the call verbatim
        # (`case: default();` round-trips as raw text, mirroring less.js).
        raise ArgumentError('default() can only be evaluated in mixin guards')
    return Keyword(index=0, value='true' if state.value else 'false')
