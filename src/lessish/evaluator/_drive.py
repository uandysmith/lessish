"""Trampoline driver for the iterative evaluator.

The `eval_ruleset ↔ _invoke_mixin_call` cycle would grow the Python
stack proportionally to mixin-invocation depth if expressed
recursively. The two functions are instead **generators** that
`yield` a *child generator* wherever they would have recursed. This
driver pulls each yielded child onto an explicit stack and resumes
the parent with the child's return value when the child finishes.

Result: regardless of logical recursion depth, only a single Python
frame (the driver's `while` loop) sits between the inner-most active
generator and the caller of `_drive`. The mixin budget
(`MIXIN_DEPTH_LIMIT`) caps logical depth; Python's recursion limit
is not load-bearing for the cycle.

Generators participating in the protocol:

* `evaluator.rulesets._eval_ruleset_gen` — the generator body of
  `eval_ruleset`. `yield` produces a child generator for the next
  sub-Ruleset (mixin body, nested ruleset, amp-when block, deferred
  import expansion); `.send(value)` provides the evaluated `Ruleset`
  or `list[Node]` back.
* `evaluator.mixins._invoke_mixin_call_gen` — the generator body of
  `_invoke_mixin_call`. Yields a child generator for the
  synthetic-body eval; receives the evaluated `Ruleset` back.

Functions NOT in the cycle (eval_declaration, eval_atrule,
eval_value, etc.) stay as plain synchronous functions. Generators
call them directly — no `yield` needed.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import EvalContext


def drive(initial: Generator[Generator[Any, Any, Any], Any, Any], ctx: EvalContext) -> Any:
    """Trampoline an evaluator generator to completion and return
    its `StopIteration.value`.

    Protocol:

    * The generator yields ONLY other generators (or raises). The
      driver pushes the yielded child onto its stack, resumes it
      with `None`, and accumulates results.
    * When a generator returns (raises `StopIteration`), the driver
      `.send(return_value)` into its parent.
    * Exceptions from a child propagate via `.throw()` into the
      parent — so `try/except` inside a generator catches
      sub-evaluation errors normally.

    Maintains O(1) Python frames regardless of cycle depth: only
    this function's frame sits between any active generator and the
    original caller.
    """
    stack: list[Generator[Generator[Any, Any, Any], Any, Any]] = [initial]
    # `pending_send` is the value to feed into the topmost generator
    # on the next iteration. None for the very first `.send()`, then
    # whatever the child returned via StopIteration.
    pending_send: Any = None
    # `pending_throw` is set when the top of stack just popped after
    # raising; the driver should `.throw()` into the new top instead
    # of `.send()`-ing.
    pending_throw: BaseException | None = None
    while stack:
        # One throttled wall-clock check per cycle step. This bounds
        # deep/wide mixin (and each/if/detached-ruleset) expansion, whose
        # logical recursion is invisible to Python's stack — the budget
        # is the only thing standing between a malicious cross-matching
        # mixin and an unbounded run.
        if ctx._deadline is not None:
            ctx.check_deadline('evaluating')
        gen = stack[-1]
        try:
            if pending_throw is not None:
                exc = pending_throw
                pending_throw = None
                yielded = gen.throw(exc)
            else:
                yielded = gen.send(pending_send)
                pending_send = None
        except StopIteration as e:
            stack.pop()
            pending_send = e.value
            continue
        except BaseException as e:  # noqa: BLE001 — re-raise unconditionally
            # Propagate up to the parent generator (if any) so its
            # try/except can catch us, mirroring the recursive-call
            # behaviour.
            stack.pop()
            if not stack:
                raise
            pending_throw = e
            continue
        # `yielded` must be another generator we should step into.
        stack.append(yielded)
        pending_send = None
    return pending_send
