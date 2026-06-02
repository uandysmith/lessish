"""Untrusted-input hardening: the ReDoS/round/math/output/wall-clock
guards, `disabled_functions` validation, `neutralize_escape`, and the
`Lessish.hardened()` preset. Each guards a denial-of-service or
output-injection vector that pure-stdlib `re`/arithmetic would otherwise
leave open.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from lessish import RESTRICTED_FUNCTIONS, Lessish
from lessish.errors import ArgumentError, EvalError, LessError, ParseError, SecurityError, UnsupportedFeatureError


class TestReplaceReDoS(unittest.TestCase):
    def test_overlapping_alternation_under_quantifier_rejected(self) -> None:
        """`(a|a|aa)*$` overlaps under a `*` — the classic
        alternation ReDoS. It must be rejected, not handed to `re`."""
        subject = 'a' * 40 + 'X'
        src = f'.x {{ y: replace("{subject}", "(a|a|aa)*$", "z"); }}'
        start = time.monotonic()
        with self.assertRaises(LessError):
            Lessish().compile(src)
        self.assertLess(time.monotonic() - start, 5.0)

    def test_nested_quantifier_rejected(self) -> None:
        with self.assertRaises(LessError):
            Lessish().compile('.x { y: replace("aaa", "(a+)+$", "z"); }')

    def test_bounded_outer_quantifier_rejected(self) -> None:
        """A *bounded* outer count (`(a+){25}`) still backtracks
        super-linearly on a non-match — it must be rejected fast, not
        handed to `re`."""
        subject = 'a' * 40 + 'X'
        start = time.monotonic()
        with self.assertRaises(LessError):
            Lessish().compile(f'.x {{ y: replace("{subject}", "(a+){{25}}$", "z"); }}')
        self.assertLess(time.monotonic() - start, 5.0)

    def test_at_most_one_outer_rep_allowed(self) -> None:
        """`(a+)?` and `(a+){1}` cap the outer group at one repetition,
        so they can't backtrack super-linearly and stay allowed."""
        self.assertIn('z', Lessish().compile('.x { y: replace("aaa", "(a+)?", "z"); }'))
        self.assertIn('z', Lessish().compile('.x { y: replace("aaa", "(a+){1}", "z"); }'))

    def test_delimited_bounded_group_allowed(self) -> None:
        """A bounded group with a literal delimiter (`(\\d{1,3}\\.){3}`)
        can't backtrack ambiguously, so it is not over-rejected."""
        out = Lessish().compile(r'.x { y: replace("10.0.0.", "(\d{1,3}\.){3}", "Z"); }')
        self.assertIn('Z', out)

    def test_disjoint_alternation_still_runs(self) -> None:
        """Distinct first characters can't backtrack super-linearly, so
        a disjoint alternation under a quantifier is allowed."""
        out = Lessish().compile('.x { y: replace("px-em", "(px|em)", "U", "g"); }')
        self.assertIn('U-U', out)


class TestRoundBound(unittest.TestCase):
    def test_huge_precision_rejected(self) -> None:
        """`round(_, 1e8)` would build a 100M-digit int — bound it."""
        with self.assertRaises(ArgumentError):
            Lessish().compile('.x { y: round(1.5, 100000000); }')

    def test_normal_round_works(self) -> None:
        self.assertIn('1.57', Lessish().compile('.x { y: round(1.567, 2); }'))


class TestMathErrorsAreLessErrors(unittest.TestCase):
    def test_domain_and_overflow_wrapped(self) -> None:
        """A raw `ValueError`/`OverflowError` from a math built-in must
        surface as a `LessError` — the documented contract."""
        for src in (
            '.x { y: sqrt(-1); }',
            '.x { y: mod(5, 0); }',
            '.x { y: pow(10, 100000); }',
            '.x { y: acos(2); }',
        ):
            with self.assertRaises(LessError):
                Lessish().compile(src)


class TestOutputSizeBudget(unittest.TestCase):
    def _amplifying_src(self) -> str:
        body = ''.join(f'p{j}:{j};' for j in range(50))
        return '.m(){' + body + '} ' + ''.join(f'.r{i}{{.m();}}' for i in range(40))

    def test_output_cap_trips(self) -> None:
        with self.assertRaises(EvalError) as ctx:
            Lessish(max_output_size=2000).compile(self._amplifying_src())
        self.assertIn('output exceeded', ctx.exception.message)

    def test_output_cap_trips_with_source_map(self) -> None:
        with self.assertRaises(EvalError):
            Lessish(max_output_size=2000).compile_with_source_map(self._amplifying_src())

    def test_under_cap_passes(self) -> None:
        self.assertIn('color', Lessish(max_output_size=1000).compile('.a { color: red; }'))


class TestWallClockBudget(unittest.TestCase):
    def test_exponential_expansion_interrupted(self) -> None:
        """A doubling mixin would run for many seconds; a sub-second
        wall-clock budget must abort it with a time-budget error."""
        bomb = (
            '.a0(){x:1}\n'
            + ''.join(f'.a{i}() when (1=1){{.a{i - 1}();.a{i - 1}();}}\n' for i in range(1, 22))
            + '.z{.a21();}'
        )
        with self.assertRaises(EvalError) as ctx:
            Lessish(max_eval_seconds=0.5).compile(bomb)
        self.assertIn('time budget', ctx.exception.message)

    def test_flat_mixin_free_workload_interrupted(self) -> None:
        """A large flat block invokes no mixins, so it never reaches the
        trampoline's deadline check — the per-declaration check must
        still bound it. Regression: the budget used to fire only at
        mixin-invocation boundaries, so this ran unbounded."""
        flat = '\n'.join(f'.r{i} {{ a{i}: {i}px + {i}px; b: #aabbcc; }}' for i in range(6000))
        start = time.monotonic()
        with self.assertRaises(EvalError) as ctx:
            Lessish(max_eval_seconds=0.1).compile(flat)
        self.assertIn('time budget', ctx.exception.message)
        self.assertLess(time.monotonic() - start, 5.0)


class TestInputSizeBudget(unittest.TestCase):
    """`max_input_size` bounds the parser input (entry + every imported
    file) before lexing — the one phase the wall-clock deadline misses."""

    def test_off_by_default(self) -> None:
        big = '.a { color: red; }\n' * 5000
        self.assertIn('color: red', Lessish().compile(big))

    def test_oversized_entry_rejected(self) -> None:
        big = '.a { color: red; }' + 'x' * 500
        with self.assertRaises(ParseError) as ctx:
            Lessish(max_input_size=100).compile(big)
        self.assertIn('max_input_size', str(ctx.exception))

    def test_entry_under_limit_passes(self) -> None:
        self.assertIn('color: red', Lessish(max_input_size=10_000).compile('.a { color: red; }'))

    def test_cumulative_import_fanout_rejected(self) -> None:
        """Each imported file fits under the budget alone, but their sum
        exceeds it — the cumulative counter must reject the fan-out."""
        d = tempfile.mkdtemp()
        for i in range(6):
            with open(os.path.join(d, f'p{i}.less'), 'w') as fh:
                fh.write(f'.c{i} {{ a: {i}px; }}\n' * 100)
        entry = ''.join(f'@import "p{i}.less";\n' for i in range(6)) + '.main { x: 1; }'
        with self.assertRaises(ParseError) as ctx:
            Lessish(max_input_size=3000).compile(entry, filename=os.path.join(d, 'entry.less'), paths=[d])
        self.assertIn('max_input_size', str(ctx.exception))

    def test_imports_under_budget_pass(self) -> None:
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'p.less'), 'w') as fh:
            fh.write('.c { a: 1px; }\n')
        out = Lessish(max_input_size=10_000).compile(
            '@import "p.less"; .main { x: 1; }', filename=os.path.join(d, 'entry.less'), paths=[d]
        )
        self.assertIn('.c', out)

    def test_tokenize_standalone_bounded(self) -> None:
        with self.assertRaises(ParseError):
            Lessish().tokenize('x' * 200, max_input_size=50)

    def test_parse_standalone_bounded(self) -> None:
        with self.assertRaises(ParseError):
            Lessish().parse('.a { color: red; }' + 'x' * 200, max_input_size=50)


class TestHardenedSecurityLock(unittest.TestCase):
    """A `hardened()` instance refuses a per-call override that weakens
    its frozen security baseline; stricter overrides still apply."""

    SRC = '.a { color: red; }'

    def test_weakening_file_io_rejected(self) -> None:
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, file_io='allow')
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, file_io='jail')

    def test_weakening_disabled_functions_rejected(self) -> None:
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, disabled_functions=[])
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, disabled_functions=['replace'])  # drops 'range'

    def test_weakening_neutralize_escape_rejected(self) -> None:
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, neutralize_escape=False)

    def test_weakening_budgets_rejected(self) -> None:
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, max_eval_seconds=None)
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, max_eval_seconds=999.0)
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, max_output_size=None)
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, max_output_size=10**12)
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, max_input_size=None)
        with self.assertRaises(SecurityError):
            Lessish.hardened().compile(self.SRC, max_input_size=10**12)

    def test_strengthening_allowed(self) -> None:
        ls = Lessish.hardened()
        self.assertIn('red', ls.compile(self.SRC, max_eval_seconds=1.0))
        self.assertIn('red', ls.compile(self.SRC, max_output_size=1000))
        self.assertIn('red', ls.compile(self.SRC, max_input_size=1000))
        self.assertIn('red', ls.compile(self.SRC, file_io='deny'))
        self.assertIn('red', ls.compile(self.SRC, neutralize_escape=True))
        self.assertIn('red', ls.compile(self.SRC, disabled_functions=RESTRICTED_FUNCTIONS | {'lighten'}))

    def test_case_insensitive_disabled_functions_not_weakening(self) -> None:
        """Re-passing the locked set in a different case is not a
        weakening — the engine case-folds names."""
        self.assertIn('red', Lessish.hardened().compile(self.SRC, disabled_functions=['REPLACE', 'Range']))

    def test_plain_instance_not_locked(self) -> None:
        """A non-hardened instance keeps the less.js-compatible footgun
        (overrides win); the lock is hardened-only."""
        import warnings

        from lessish.errors import LessishSecurityWarning

        with warnings.catch_warnings():
            warnings.simplefilter('ignore', LessishSecurityWarning)
            out = Lessish(file_io='deny').compile(self.SRC, file_io='allow')
        self.assertIn('red', out)

    def test_construction_override_sets_baseline(self) -> None:
        """A tighter budget passed to hardened() becomes the locked
        baseline — a looser per-call value is then rejected against it."""
        ls = Lessish.hardened(max_eval_seconds=2.0)
        self.assertIn('red', ls.compile(self.SRC, max_eval_seconds=1.0))
        with self.assertRaises(SecurityError):
            ls.compile(self.SRC, max_eval_seconds=5.0)


class TestDisabledFunctionsValidation(unittest.TestCase):
    def test_unknown_name_rejected(self) -> None:
        """A typo'd name would silently disable nothing — reject it."""
        with self.assertRaises(ValueError):
            Lessish(disabled_functions=['replace ']).compile('.a { b: 1; }')

    def test_restricted_functions_accepted(self) -> None:
        out = Lessish(disabled_functions=RESTRICTED_FUNCTIONS).compile('.a { b: 1; }')
        self.assertIn('b: 1', out)

    def test_disabled_function_refused_at_call(self) -> None:
        with self.assertRaises(UnsupportedFeatureError):
            Lessish(disabled_functions={'replace'}).compile('.a { b: replace("x", "y", "z"); }')


class TestNeutralizeEscape(unittest.TestCase):
    """`neutralize_escape` rewrites `</` (the only sequence that ends a
    `<style>` rawtext element) to its six-digit CSS escape, on *every*
    output path — value, selector, property name, comment, at-rule
    prelude — not just escaped string values."""

    def _neutralized(self, src: str) -> str:
        out = Lessish(neutralize_escape=True).compile(src)
        self.assertNotIn('</', out)
        return out

    def test_e_emits_raw_by_default(self) -> None:
        self.assertIn('</style>', Lessish().compile('.x { y: e("</style>"); }'))

    def test_e_neutralized(self) -> None:
        out = self._neutralized('.x { y: e("</style>"); }')
        self.assertIn('\\00003c/style>', out)

    def test_tilde_string_neutralized(self) -> None:
        self._neutralized('.x { y: ~"</style>"; }')

    def test_plain_quoted_value_neutralized(self) -> None:
        self._neutralized('.x { y: "</style><script>x</script>"; }')

    def test_selector_interpolation_neutralized(self) -> None:
        self._neutralized('@v: ~"</style>"; @{v} { color: red; }')

    def test_property_name_interpolation_neutralized(self) -> None:
        self._neutralized('@n: ~"</style>"; .a { @{n}: 1; }')

    def test_comment_neutralized(self) -> None:
        self._neutralized('/* </style><script>x</script> */ .a { color: red; }')

    def test_atrule_prelude_interpolation_neutralized(self) -> None:
        self._neutralized('@v: ~"x) {} </style"; @media @{v} { .a { x: 1; } }')

    def test_compressed_output_neutralized(self) -> None:
        out = Lessish(neutralize_escape=True, compress=True).compile('.x { y: "</style>"; }')
        self.assertNotIn('</', out)

    def test_source_map_path_neutralized_and_valid(self) -> None:
        import json

        res = Lessish(neutralize_escape=True).compile_with_source_map(
            '@v: ~"</style>"; .a { content: "x</style>y"; } @{v} { top: 0; }'
        )
        body = res.css.replace(res.annotation_url, '')
        self.assertNotIn('</', body)
        m = json.loads(res.map_json)
        self.assertEqual(m['version'], 3)
        self.assertTrue(m['mappings'])

    def test_escape_decodes_back_losslessly(self) -> None:
        # `\00003c` is the CSS escape for `<`; a browser decodes it back
        # inside the string, so the rendered value is unchanged.
        self.assertIn('\\00003c/p>', self._neutralized('.a { content: "</p>"; }'))

    def test_media_query_range_operators_survive(self) -> None:
        # `width < 600px` has no `</`, so range media queries must NOT be
        # touched by neutralisation.
        out = Lessish(neutralize_escape=True).compile('@media (width < 600px) { .a { x: 1; } }')
        self.assertIn('width < 600px', out)

    def test_cdo_in_value_survives(self) -> None:
        out = Lessish(neutralize_escape=True).compile('.a { content: "<!--"; }')
        self.assertIn('<!--', out)

    def test_off_by_default_emits_raw(self) -> None:
        self.assertIn('</style>', Lessish().compile('.a { content: "</style>"; }'))


class TestHardenedPreset(unittest.TestCase):
    def test_blocks_replace(self) -> None:
        with self.assertRaises(LessError):
            Lessish.hardened().compile('.a { b: replace("x", "y", "z"); }')

    def test_neutralizes_escape(self) -> None:
        self.assertNotIn('</', Lessish.hardened().compile('.a { b: e("</style>"); }'))
        self.assertNotIn('</', Lessish.hardened().compile('.a { b: "</style>"; }'))
        self.assertNotIn('</', Lessish.hardened().compile('@v: ~"</style>"; @{v} { x: 1; }'))

    def test_normal_compile_works(self) -> None:
        self.assertIn('color: red', Lessish.hardened().compile('@c: red; .a { color: @c; }'))

    def test_override_applies(self) -> None:
        self.assertEqual(Lessish.hardened(compress=True).compile('.a { b: 1; }'), '.a{b:1}')


class TestDataUriJail(unittest.TestCase):
    def test_data_uri_cannot_escape_jail(self) -> None:
        """The shared `Importer.jail_allows` boundary must block a
        `data-uri()` `..`-escape just as `@import` does."""
        root = tempfile.mkdtemp()
        jail = os.path.join(root, 'jail')
        os.makedirs(jail)
        with open(os.path.join(root, 'secret.txt'), 'w') as f:
            f.write('TOPSECRET')
        entry = os.path.join(jail, 'in.less')
        src = '.a { background: data-uri("../secret.txt"); }'
        out = Lessish(filename=entry, paths=[jail]).compile(src, filename=entry, paths=[jail])
        self.assertNotIn('TOPSECRET', out)
        self.assertNotIn('VE9QU0VDUkVU', out)  # base64('TOPSECRET')


if __name__ == '__main__':
    unittest.main()
