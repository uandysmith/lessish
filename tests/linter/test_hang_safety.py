"""Defense against runaway rules.

Two layers:

1. **Adversarial-input wall-clock tests** — pathological sources must
   complete under a budget per input.

2. **Watchdog mechanism tests** — a synthetic rule with an infinite
   loop. The engine's `signal.SIGALRM`-based watchdog must catch it,
   yield a `watchdog-timeout` finding, and continue with the rest.
"""

from __future__ import annotations

import time
import unittest
from collections.abc import Iterable

from lessish.linter import LessLinter, LinterConfig
from lessish.linter._findings import Finding
from lessish.linter._watchdog import RuleTimeoutError, watchdog
from lessish.linter.rules._base import LintContext, Rule

ADVERSARIAL_INPUTS = [
    '',
    '.a { color: red; }',
    '.a {' * 50 + ' color: red; ' + '}' * 50,
    '.a {\n' + ' ' * 4000 + 'color: red;\n}\n',
    '.a {\n' + ''.join(f'  prop{i}: {i};\n' for i in range(500)) + '}\n',
    '@x: 5;\n.a { width: @{x};\n  height: @{x};\n}\n',
    ''.join(f'/* comment {i} */\n' for i in range(200)) + '/* lessish-disable */\n.a { color: red; }\n',
    '.a { color: red',
    '\n   \t  \n',
    ''.join(f'@import "f{i}.less";\n' for i in range(300)),
]

BUDGET_SECONDS = 5.0


class TestAdversarialInputsTerminate(unittest.TestCase):
    def test_all_adversarial_inputs_complete_quickly(self) -> None:
        linter = LessLinter()
        for src in ADVERSARIAL_INPUTS:
            start = time.monotonic()
            try:
                linter.check(src, filename='<adversarial>')
            except Exception as e:  # noqa: BLE001
                self.fail(f'linter crashed on adversarial input: {type(e).__name__}: {e}')
            elapsed = time.monotonic() - start
            self.assertLess(
                elapsed,
                BUDGET_SECONDS,
                msg=f'check() took {elapsed:.2f}s (budget {BUDGET_SECONDS}s) on input {src!r:.60}…',
            )

    def test_fix_idempotent_under_budget(self) -> None:
        linter = LessLinter()
        for src in ADVERSARIAL_INPUTS:
            start = time.monotonic()
            try:
                once = linter.fix(src)
                twice = linter.fix(once)
            except Exception:  # noqa: BLE001
                continue
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, BUDGET_SECONDS, msg=f'fix() exceeded budget on {src!r:.60}')
            self.assertEqual(once, twice, msg=f'fix not idempotent: {src!r:.60}')


class _InfiniteLoopRule(Rule):
    """A rule that spins forever — exercises the watchdog."""

    id = 'test-hang'
    severity = 'warning'
    fix_tier = 'none'
    description = 'test fixture'

    def check(self, ctx: LintContext) -> Iterable[Finding]:  # noqa: ARG002
        while True:
            pass
        yield  # noqa: F821  # unreachable but keeps method a generator


class TestWatchdogCatchesHang(unittest.TestCase):
    def test_watchdog_aborts_runaway_rule(self) -> None:
        linter = LessLinter(rule_timeout_seconds=0.2)
        linter._rules = (*linter._rules, _InfiniteLoopRule())
        linter.config.enabled = None
        start = time.monotonic()
        findings = linter.check('.a { color: red; }\n')
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 2.0, msg=f'watchdog took {elapsed:.2f}s')
        timeout_findings = [f for f in findings if f.rule_id == 'watchdog-timeout']
        self.assertEqual(len(timeout_findings), 1)
        self.assertIn('test-hang', timeout_findings[0].message)

    def test_other_rules_still_run_after_timeout(self) -> None:
        linter = LessLinter(
            rule_timeout_seconds=0.2,
            config=LinterConfig(enabled={'hex-short', 'test-hang'}),
        )
        linter._rules = (*linter._rules, _InfiniteLoopRule())
        findings = linter.check('.a { color: #FFFFFF; }\n')
        rule_ids = {f.rule_id for f in findings}
        self.assertIn('hex-short', rule_ids)
        self.assertIn('watchdog-timeout', rule_ids)


class TestWatchdogContextManager(unittest.TestCase):
    """`watchdog` context manager works in isolation."""

    def test_quick_block_does_not_fire(self) -> None:
        with watchdog(seconds=1.0, rule_id='x'):
            pass

    def test_slow_block_raises_timeout(self) -> None:
        with self.assertRaises(RuleTimeoutError):
            with watchdog(seconds=0.1, rule_id='x'):
                while True:
                    pass

    def test_zero_timeout_disables_watchdog(self) -> None:
        counter = 0
        with watchdog(seconds=0, rule_id='x'):
            for _ in range(1000):
                counter += 1
        self.assertEqual(counter, 1000)


if __name__ == '__main__':
    unittest.main()
