"""Per-rule watchdog.

Guards the engine against runaway rules (e.g. an infinite `while` loop
in newly-added code). When a rule's `check()` exceeds the configured
budget, the watchdog raises `RuleTimeoutError`; the engine catches it,
records a `watchdog-timeout` finding, and continues with the next rule.

Implementation: a single shared daemon thread (`_WatchdogScheduler`)
that wakes on the nearest pending deadline and async-raises
`RuleTimeoutError` into the target thread via
`PyThreadState_SetAsyncExc`. This works on Windows, on non-main
threads, and does not mutate any process-wide signal handler. The
shared-thread design also keeps the per-`watchdog(...)` cost sub-µs
(heap push + event set), which matters because the engine wraps every
rule's `check()` call — a 45-rule × N-file run otherwise pays
thread-spawn overhead 45·N times.

Caveat: async exceptions fire at Python bytecode boundaries, so a
rule stuck inside a C extension (a long regex backtrack, a `time.sleep`)
won't unblock until control returns. That matches the older SIGALRM
behaviour too — signals only fire between bytecodes anyway.

Use as a context manager:

    with watchdog(seconds=30, rule_id='some-rule'):
        do_work()
"""

from __future__ import annotations

import ctypes
import heapq
import threading
import time as _time
from collections.abc import Iterator
from contextlib import contextmanager


class RuleTimeoutError(Exception):
    """Raised when a rule exceeds its watchdog budget.

    Two construction paths: callers that already know the rule
    pass `(rule_id, seconds)` explicitly; the watchdog scheduler
    delivers the exception async via `PyThreadState_SetAsyncExc`,
    which constructs the class with no arguments — in that case
    the active `(rule_id, seconds)` is recovered from the per-thread
    context stack populated by the surrounding `watchdog(...)`
    block (see `_active`).
    """

    def __init__(
        self,
        rule_id: str | None = None,
        seconds: float | None = None,
    ) -> None:
        if rule_id is None or seconds is None:
            stack = getattr(_active, 'stack', None)
            if stack:
                rule_id, seconds = stack[-1]
            else:
                rule_id = rule_id or '?'
                seconds = seconds if seconds is not None else 0.0
        super().__init__(f'rule {rule_id!r} timed out after {seconds}s')
        self.rule_id: str = rule_id
        self.seconds: float = seconds


# Per-thread stack of `(rule_id, seconds)` for active watchdog blocks.
# Populated by `watchdog(...)` on entry, popped on exit. The
# scheduler thread delivers `RuleTimeoutError` to a target thread; the
# exception's no-arg constructor reads the top of *that* thread's
# stack — same thread that's about to raise.
_active = threading.local()


def _has_set_async_exc() -> bool:
    """True iff `ctypes.pythonapi.PyThreadState_SetAsyncExc` is callable
    on this interpreter. Standard on CPython; PyPy / non-standard
    interpreters may lack it, in which case the watchdog degrades to a
    no-op rather than crashing.
    """
    return hasattr(ctypes.pythonapi, 'PyThreadState_SetAsyncExc')


class _WatchdogScheduler:
    """Single daemon thread that delivers `RuleTimeoutError` to the
    right target when each pending budget expires.

    `schedule(...)` is O(log n) heap push + event set; `cancel(...)`
    is dict-pop. The scheduler thread itself blocks on an Event with
    the nearest-deadline timeout, then drains every entry past `now`
    under the lock. SetAsyncExc fires inside the lock so a racing
    `cancel(cookie)` sees a consistent "fired or not" answer.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Event()
        # Heap of (deadline_monotonic, cookie). Cookies whose entry
        # has been removed from `_entries` are tombstones — drained
        # lazily from the heap top by the scheduler loop.
        self._heap: list[tuple[float, int]] = []
        self._entries: dict[int, tuple[int, type[BaseException]]] = {}
        self._counter = 0
        self._thread: threading.Thread | None = None

    def _ensure_started(self) -> None:
        """Lazy-spawn the scheduler thread on first use. A consumer
        that never calls `watchdog(...)` pays no thread cost.
        """
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is not None:
                return
            t = threading.Thread(
                target=self._run,
                daemon=True,
                name='lessish-watchdog',
            )
            t.start()
            self._thread = t

    def schedule(
        self,
        seconds: float,
        target_tid: int,
        exc_class: type[BaseException],
    ) -> int:
        self._ensure_started()
        deadline = _time.monotonic() + seconds
        with self._lock:
            self._counter += 1
            cookie = self._counter
            heapq.heappush(self._heap, (deadline, cookie))
            self._entries[cookie] = (target_tid, exc_class)
        self._wake.set()
        return cookie

    def cancel(self, cookie: int) -> bool:
        """Remove a pending entry. Returns True iff the scheduler had
        already fired the async exception — callers then need to
        `PyThreadState_SetAsyncExc(target, None)` to clear any still-
        pending dispatch on the caller thread.
        """
        with self._lock:
            return self._entries.pop(cookie, None) is None

    def _run(self) -> None:
        while True:
            self._wake.clear()
            with self._lock:
                # Drop tombstoned entries from the heap top so the
                # next-wakeup decision sees only live deadlines.
                while self._heap and self._heap[0][1] not in self._entries:
                    heapq.heappop(self._heap)
                timeout = self._heap[0][0] - _time.monotonic() if self._heap else None
            if timeout is None:
                self._wake.wait()
            elif timeout > 0:
                self._wake.wait(timeout)
            now = _time.monotonic()
            with self._lock:
                # Drain every expired entry under the lock. SetAsyncExc
                # runs here too so a racing `cancel(cookie)` either
                # (a) finds the entry present and removes it before we
                # get a chance to fire, or (b) finds it gone and knows
                # we fired — caller clears via SetAsyncExc(None).
                set_async_exc = _SET_ASYNC_EXC
                while self._heap and self._heap[0][0] <= now:
                    _, cookie = heapq.heappop(self._heap)
                    entry = self._entries.pop(cookie, None)
                    if entry is None:
                        continue
                    target_tid, exc_class = entry
                    if set_async_exc is not None:
                        set_async_exc(
                            ctypes.c_ulong(target_tid),
                            ctypes.py_object(exc_class),
                        )


_SCHEDULER = _WatchdogScheduler()
# Cache the C-level entry-point lookup so the hot path doesn't redo
# the `ctypes.pythonapi.<name>` attribute walk on every call.
_SET_ASYNC_EXC = ctypes.pythonapi.PyThreadState_SetAsyncExc if _has_set_async_exc() else None


@contextmanager
def watchdog(*, seconds: float, rule_id: str) -> Iterator[None]:
    """Abort the with-block if it runs longer than `seconds`.

    A `seconds <= 0` budget disables the watchdog (`yield` only). On
    interpreters that don't expose `PyThreadState_SetAsyncExc`, the
    watchdog is likewise a no-op so the linter still runs — bugs can
    still hang in that scenario, but the rest of the engine is
    unaffected.
    """
    if seconds <= 0 or _SET_ASYNC_EXC is None:
        yield
        return

    target_tid = threading.get_ident()

    # `PyThreadState_SetAsyncExc` calls the exception class with no
    # args, so we push the (rule_id, seconds) context onto a per-thread
    # stack that `RuleTimeoutError.__init__` reads. Cheaper than
    # synthesising a closure subclass per call (~5µs of class
    # allocation overhead on the linter's hot path).
    stack = getattr(_active, 'stack', None)
    if stack is None:
        stack = []
        _active.stack = stack
    stack.append((rule_id, seconds))

    cookie = _SCHEDULER.schedule(seconds, target_tid, RuleTimeoutError)
    try:
        yield
    finally:
        already_fired = _SCHEDULER.cancel(cookie)
        if already_fired:
            # Clear any still-pending async exc dispatch — the
            # scheduler injected it after the work was done.
            _SET_ASYNC_EXC(ctypes.c_ulong(target_tid), None)
        stack.pop()
