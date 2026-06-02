"""Tests for the mixin-invocation budgets that catch silent hangs.

The depth limit traps a straight runaway recursion (`.x() { .x(); }`);
the total-invocation limit traps exponential expansion through
cross-invocation matching (the class of bug that produced the
Bootstrap grid hang).
"""

from __future__ import annotations

import unittest

from lessish import Lessish
from lessish.errors import EvalError

compile = Lessish().compile


class TestMixinBudgets(unittest.TestCase):
    def test_runaway_recursion_caught_by_depth_limit(self) -> None:
        """`.x() { .x(); }` — a mixin that calls itself with no
        termination must surface as a clear EvalError rather than a
        wall-clock hang.
        """
        src = '.x() { .x(); }\n.use { .x(); }'
        with self.assertRaises(EvalError) as ctx:
            compile(src)
        self.assertIn('Maximum mixin call depth', ctx.exception.message)

    def test_exponential_blowup_caught_by_total_limit(self) -> None:
        """Lower `MIXIN_TOTAL_LIMIT` and verify a deep but otherwise
        valid recursive expansion trips the budget. Real-world
        compiles stay well under 1M invocations; the budget is the
        backstop that prevents silent multi-hour hangs when a future
        regression re-introduces exponential expansion.
        """
        from lessish import evaluator

        original = evaluator.MIXIN_TOTAL_LIMIT
        evaluator.MIXIN_TOTAL_LIMIT = 100
        try:
            src = '.iter(@i) when (@i > 0) {\n  .x-@{i} { p: @i; }\n  .iter(@i - 1);\n}\n.iter(500);\n'
            with self.assertRaises(EvalError) as ctx:
                compile(src)
            self.assertIn('budget exhausted', ctx.exception.message)
        finally:
            evaluator.MIXIN_TOTAL_LIMIT = original

    def test_deep_but_bounded_recursion_succeeds(self) -> None:
        """500-iteration recursive mixin must still compile cleanly —
        well below the 1000-call depth limit. Confirms the limit is
        not so tight it rejects legitimate uses.
        """
        src = '.iter(@i) when (@i > 0) {\n  .x-@{i} { p: @i; }\n  .iter(@i - 1);\n}\n.iter(500);\n'
        out = compile(src)
        # 500 iterations × 3 lines per rule = 1500 lines
        self.assertEqual(len(out.splitlines()), 1500)

    def test_compile_does_not_mutate_recursion_limit(self) -> None:
        """`compile()` must not touch `sys.setrecursionlimit` — the
        mixin-invocation cycle runs under a trampoline (see
        `evaluator/_drive.py`) so default Python recursion is enough.
        """
        import sys

        baseline = sys.getrecursionlimit()
        compile('.x { color: red; }')
        self.assertEqual(sys.getrecursionlimit(), baseline)

        # Also via the error path (depth-limit raise).
        with self.assertRaises(EvalError):
            compile('.x() { .x(); }\n.use { .x(); }')
        self.assertEqual(sys.getrecursionlimit(), baseline)

    def test_compile_does_not_mutate_thread_stack_size(self) -> None:
        """Companion to the recursion-limit check: `compile()` must
        not touch `threading.stack_size()` — it stays at the
        process-wide default (0) across a compile.
        """
        import threading

        baseline = threading.stack_size()
        compile('.x { color: red; }')
        self.assertEqual(threading.stack_size(), baseline)

    def test_compile_runs_on_caller_thread(self) -> None:
        """`compile()` runs on the caller's thread — no per-call
        worker-thread spawn.
        """
        import threading
        from unittest.mock import patch

        from lessish import Lessish

        observed: dict[str, threading.Thread] = {}
        original = Lessish.parse

        def spy(self: Lessish, source: object, /, **kw: object) -> object:
            observed['thread'] = threading.current_thread()
            return original(self, source, **kw)  # type: ignore[arg-type]

        with patch.object(Lessish, 'parse', spy):
            compile('.x { color: red; }')
        self.assertIs(observed['thread'], threading.main_thread())

    def test_deep_mixin_chain_under_default_recursion_limit(self) -> None:
        """The iterative trampoline must not need the recursion-limit
        bump even for chains comfortably over CPython's default 1000.
        Set the limit lower than the default to prove the cycle is
        truly flat.
        """
        import sys

        from lessish import Lessish

        defs = '\n'.join(f'.m{i}() {{ .m{i + 1}(); }}' for i in range(800))
        src = f'{defs}\n.m800() {{ color: red; }}\n.x {{ .m0(); }}\n'

        saved = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(500)  # half the default — proves the trampoline is real
            out = Lessish().compile(src)
        finally:
            sys.setrecursionlimit(saved)
        self.assertIn('color: red', out)


class TestInterpolationBudget(unittest.TestCase):
    """`@{var}` interpolation has its own expansion budget (the mixin
    budget does not cover this path). Chained self-doubling string
    variables grow multiplicatively and must trip a clear EvalError
    rather than exhausting memory (billion-laughs).
    """

    @staticmethod
    def _bomb(depth: int) -> str:
        lines = ['@a0: "xxxxxxxxxx";']
        lines += [f'@a{i}: "@{{a{i - 1}}}@{{a{i - 1}}}";' for i in range(1, depth + 1)]
        lines.append(f'.out {{ content: "@{{a{depth}}}"; }}')
        return '\n'.join(lines)

    def test_billion_laughs_interpolation_caught(self) -> None:
        with self.assertRaises(EvalError) as ctx:
            compile(self._bomb(30))
        self.assertIn('runaway', ctx.exception.message.lower())

    def test_small_interpolation_chain_still_compiles(self) -> None:
        # A shallow chain stays well under the limit and compiles.
        out = compile(self._bomb(4))
        self.assertIn('content:', out)

    def test_interp_expansion_limit_is_configurable(self) -> None:
        # A tiny per-compile limit trips even a shallow chain; a generous
        # one lets it through. Proves the option threads to EvalContext.
        with self.assertRaises(EvalError):
            compile(self._bomb(6), interp_expansion_limit=200)
        self.assertIn('content:', compile(self._bomb(6), interp_expansion_limit=10_000_000))


class TestMixinBudgetConfigurable(unittest.TestCase):
    """The mixin budget is overridable per-compile via the
    `mixin_total_limit` / `mixin_depth_limit` options (defaulting to the
    `evaluator.MIXIN_*_LIMIT` module constants)."""

    def test_mixin_total_limit_option_overrides(self) -> None:
        src = '.iter(@i) when (@i > 0) {\n  .x-@{i} { p: @i; }\n  .iter(@i - 1);\n}\n.iter(500);\n'
        with self.assertRaises(EvalError) as ctx:
            compile(src, mixin_total_limit=100)
        self.assertIn('budget exhausted', ctx.exception.message)

    def test_mixin_depth_limit_option_overrides(self) -> None:
        with self.assertRaises(EvalError) as ctx:
            compile('.x() { .x(); }\n.use { .x(); }', mixin_depth_limit=50)
        self.assertIn('Maximum mixin call depth', ctx.exception.message)


class TestReplaceReDoSGuard(unittest.TestCase):
    """`replace()` rejects catastrophic-backtracking patterns and bounds
    input size, since Python's `re` has no step/time budget."""

    def _replace(self, subject: str, pattern: str, repl: str = 'z') -> object:
        from lessish.ast_nodes import Quoted
        from lessish.context import EvalContext
        from lessish.functions.string import fn_replace

        args = [
            Quoted(index=0, quote='"', value=subject, escaped=False),
            Quoted(index=0, quote='"', value=pattern, escaped=False),
            Quoted(index=0, quote='"', value=repl, escaped=False),
        ]
        return fn_replace(args, EvalContext())

    def test_nested_quantifier_pattern_rejected(self) -> None:
        from lessish.errors import ArgumentError

        for pat in ['(a+)+$', '(a*)*', '(.+)*', r'(\d+)+']:
            with self.assertRaises(ArgumentError, msg=pat):
                self._replace('a' * 40 + '!', pat)

    def test_ordinary_patterns_still_work(self) -> None:
        from lessish.ast_nodes import Quoted

        # Single-quantifier groups are NOT flagged.
        out = self._replace('hello world', r'(\w+)', 'x-$1')
        self.assertIsInstance(out, Quoted)
        self.assertEqual(out.value, 'x-hello world')  # type: ignore[union-attr]

    def test_oversize_input_rejected(self) -> None:
        from lessish.errors import ArgumentError

        with self.assertRaises(ArgumentError):
            self._replace('a' * 200_000, 'a', 'b')


class TestRangeBudget(unittest.TestCase):
    """`range()` generates one node per element; an attacker-chosen
    count would otherwise exhaust memory. Bounded by `range_max_elements`."""

    def test_huge_range_caught(self) -> None:
        with self.assertRaises(EvalError) as ctx:
            compile('.x { y: range(5000000); }')
        self.assertIn('range()', ctx.exception.message)

    def test_nonterminating_step_caught(self) -> None:
        # `step <= 0` with `to >= from` would loop forever; the element
        # cap converts it into a clean error.
        with self.assertRaises(EvalError):
            compile('.x { y: range(1, 10, 0); }')

    def test_ordinary_range_still_works(self) -> None:
        self.assertIn('1 2 3', compile('.x { y: range(3); }'))

    def test_range_max_elements_option(self) -> None:
        with self.assertRaises(EvalError):
            compile('.x { y: range(50); }', range_max_elements=10)
        self.assertIn('1 2 3', compile('.x { y: range(3); }', range_max_elements=10))


class TestDisabledFunctions(unittest.TestCase):
    """Opt-in hardening: `disabled_functions` refuses named built-ins at
    call time with `UnsupportedFeatureError`. Off by default."""

    def test_default_keeps_all_functions(self) -> None:
        import warnings

        from lessish import Lessish

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            out = Lessish().compile(r'.a { c: replace("hello", "l", "x"); }', file_io='allow')
        self.assertIn('hexlo', out)

    def test_restricted_preset_disables_replace_and_range(self) -> None:
        import warnings

        from lessish import RESTRICTED_FUNCTIONS, Lessish
        from lessish.errors import UnsupportedFeatureError

        self.assertEqual(RESTRICTED_FUNCTIONS, frozenset({'replace', 'range'}))
        ls = Lessish(disabled_functions=RESTRICTED_FUNCTIONS)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for src in (r'.a { c: replace("x", "y", "z"); }', '.a { c: range(3); }'):
                with self.assertRaises(UnsupportedFeatureError):
                    ls.compile(src, file_io='allow')

    def test_disabled_is_case_insensitive(self) -> None:
        from lessish import Lessish
        from lessish.errors import UnsupportedFeatureError

        ls = Lessish(disabled_functions={'RePlAcE'})
        with self.assertRaises(UnsupportedFeatureError):
            ls.compile(r'.a { c: replace("x", "y", "z"); }')

    def test_disabling_one_leaves_others(self) -> None:
        from lessish import Lessish

        ls = Lessish(disabled_functions={'replace'})
        self.assertIn('1 2', ls.compile('.a { c: range(2); }'))

    def test_per_call_override(self) -> None:
        from lessish import Lessish
        from lessish.errors import UnsupportedFeatureError

        ls = Lessish()
        with self.assertRaises(UnsupportedFeatureError):
            ls.compile('.a { c: range(3); }', disabled_functions={'range'})


class TestMixinShadowing(unittest.TestCase):
    """`find_mixin_matches` shadowing rule.

    A `.col()` call inside `.mk(b)`'s body must NOT match `.col()`
    definitions left in the caller's scope by a sibling `.mk(a)`
    invocation; without the shadow rule, matches would multiply
    exponentially.
    """

    def test_sibling_mixin_invocations_dont_cross_match(self) -> None:
        """`.mk(a)` and `.mk(b)` each call their OWN local `.col()`;
        neither must invoke the other's `.col()` definition. Output
        order: all of mk(a)'s output first, then all of mk(b)'s.
        """
        src = (
            '.mk(@k) {\n'
            '  .col(@i) when (@i > 0) {\n'
            '    .x-@{k}-@{i} { width: @i*1px; }\n'
            '    .col(@i - 1);\n'
            '  }\n'
            '  .col(3);\n'
            '}\n'
            '.mk(a);\n'
            '.mk(b);\n'
        )
        out = compile(src)
        # Expected: x-a-3, x-a-2, x-a-1, x-b-3, x-b-2, x-b-1
        names = [line.strip() for line in out.splitlines() if line.strip().startswith('.x-')]
        self.assertEqual(
            names,
            ['.x-a-3 {', '.x-a-2 {', '.x-a-1 {', '.x-b-3 {', '.x-b-2 {', '.x-b-1 {'],
        )

    def test_inner_mixin_def_shadows_outer(self) -> None:
        """A `.x()` defined inside a Ruleset shadows a `.x()` at the
        enclosing scope. Calling `.x();` inside picks only the inner
        one (less.js semantics: first-frame-with-matches wins).
        """
        src = '.x() { p: outer; }\n.use {\n  .x() { p: inner; }\n  .x();\n}\n'
        out = compile(src)
        self.assertIn('p: inner', out)
        self.assertNotIn('p: outer', out)


if __name__ == '__main__':
    unittest.main()
