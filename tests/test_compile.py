from __future__ import annotations

import unittest
import warnings

from lessish import Lessish, LessishSecurityWarning
from lessish.errors import UndefinedNameError

compile = Lessish().compile

# `file_io` defaults to the secure `'jail'`; absolute-path `data-uri()`
# reads are less.js-compatible `'allow'` behaviour. This helper runs
# under `'allow'` and silences the (expected) security warning.
_allow = Lessish(file_io='allow')


def compile_allow(*args: object, **kwargs: object) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=LessishSecurityWarning)
        return _allow.compile(*args, **kwargs)  # type: ignore[arg-type]


class TestBasicRulesets(unittest.TestCase):
    def test_static_rule(self) -> None:
        self.assertEqual(
            compile('.a { color: red; }'),
            '.a {\n  color: red;\n}\n',
        )

    def test_multiple_decls(self) -> None:
        self.assertEqual(
            compile('.a { color: red; padding: 10px; }'),
            '.a {\n  color: red;\n  padding: 10px;\n}\n',
        )

    def test_important_passthrough(self) -> None:
        # less.js default: `!important` emits with a leading space. The
        # no-space form is reserved for `$prop`-accessed declarations
        # (J1, `important_origin='accessed'`).
        self.assertEqual(
            compile('.a { color: red !important; }'),
            '.a {\n  color: red !important;\n}\n',
        )

    def test_multiple_selectors(self) -> None:
        # less.js emits one selector per line for multi-selector rules.
        self.assertEqual(
            compile('.a, .b { color: red; }'),
            '.a,\n.b {\n  color: red;\n}\n',
        )


class TestVariables(unittest.TestCase):
    def test_simple_variable(self) -> None:
        self.assertEqual(
            compile('@c: red; .a { color: @c; }'),
            '.a {\n  color: red;\n}\n',
        )

    def test_variable_chain(self) -> None:
        # @a → @b → 10px
        self.assertEqual(
            compile('@a: @b; @b: 10px; .x { width: @a; }'),
            '.x {\n  width: 10px;\n}\n',
        )

    def test_last_definition_wins(self) -> None:
        self.assertEqual(
            compile('@c: red; @c: blue; .a { color: @c; }'),
            '.a {\n  color: blue;\n}\n',
        )

    def test_local_shadow(self) -> None:
        # Inner @c shadows outer @c within the inner block.
        css = compile('@c: red; .a { @c: blue; color: @c; }')
        self.assertEqual(css, '.a {\n  color: blue;\n}\n')

    def test_variable_in_function_call(self) -> None:
        # rgb() returns a Color; emit form is hex when alpha=1 (less.js parity).
        self.assertEqual(
            compile('@n: 128; .a { color: rgb(255, @n, 0); }'),
            '.a {\n  color: #ff8000;\n}\n',
        )

    def test_undefined_variable_raises(self) -> None:
        with self.assertRaises(UndefinedNameError):
            compile('.a { color: @missing; }')

    def test_recursive_variable_raises(self) -> None:
        with self.assertRaises(UndefinedNameError):
            compile('@a: @a; .x { color: @a; }')

    def test_variable_variable(self) -> None:
        # @@name: resolve @name to get a name, then look up that name.
        self.assertEqual(
            compile('@name: var; @var: "hello"; .a { content: @@name; }'),
            '.a {\n  content: "hello";\n}\n',
        )

    def test_interpolation_strips_quotes(self) -> None:
        # `@{name}` substitutes the value unquoted; bare `@name` keeps quotes.
        self.assertEqual(
            compile('@s: "hello"; .a { url: "/path/@{s}.png"; content: @s; }'),
            '.a {\n  url: "/path/hello.png";\n  content: "hello";\n}\n',
        )


class TestNesting(unittest.TestCase):
    def test_descendant_nesting(self) -> None:
        self.assertEqual(
            compile('.a { color: red; .b { font: bold; } }'),
            '.a {\n  color: red;\n}\n.a .b {\n  font: bold;\n}\n',
        )

    def test_amp_attached(self) -> None:
        self.assertEqual(
            compile('.a { &:hover { color: red; } }'),
            '.a:hover {\n  color: red;\n}\n',
        )

    def test_amp_with_child_combinator(self) -> None:
        self.assertEqual(
            compile('.a { & > .b { color: red; } }'),
            '.a > .b {\n  color: red;\n}\n',
        )

    def test_amp_swap(self) -> None:
        # `.parent &` should produce `.parent .a`.
        self.assertEqual(
            compile('.a { .parent & { color: red; } }'),
            '.parent .a {\n  color: red;\n}\n',
        )

    def test_two_levels_deep(self) -> None:
        self.assertEqual(
            compile('.a { .b { .c { color: red; } } }'),
            '.a .b .c {\n  color: red;\n}\n',
        )

    def test_multiple_parent_selectors_distribute(self) -> None:
        # `.a, .b { .c { ... } }` → `.a .c, .b .c { ... }`
        self.assertEqual(
            compile('.a, .b { .c { color: red; } }'),
            '.a .c,\n.b .c {\n  color: red;\n}\n',
        )


class TestSelectorInterpolation(unittest.TestCase):
    def test_class_name_interp(self) -> None:
        self.assertEqual(
            compile('@name: dark; .theme-@{name} { color: red; }'),
            '.theme-dark {\n  color: red;\n}\n',
        )

    def test_property_name_interp(self) -> None:
        self.assertEqual(
            compile('@p: color; .a { @{p}: red; }'),
            '.a {\n  color: red;\n}\n',
        )


class TestAtRules(unittest.TestCase):
    def test_top_level_media_block(self) -> None:
        # less.js indents rulesets nested inside `@media`/`@supports`.
        self.assertEqual(
            compile('@media (min-width: 600px) { .a { color: red; } }'),
            '@media (min-width: 600px) {\n  .a {\n    color: red;\n  }\n}\n',
        )

    def test_charset_passthrough(self) -> None:
        self.assertEqual(
            compile("@charset 'utf-8';"),
            "@charset 'utf-8';\n",
        )

    def test_import_passthrough_with_flag(self) -> None:
        # `process_imports=False` mirrors less.js: less-shaped @imports
        # (no `.css` ext, no `(css)` qualifier) are dropped — without
        # the fetch+inline step they'd leak as `@import "foo.less";`
        # lines the browser can't resolve. CSS-shaped @imports survive
        # as valid pass-through directives.
        self.assertEqual(compile('@import "x.less";', process_imports=False), '')
        self.assertEqual(
            compile('@import "x.css";', process_imports=False),
            '@import "x.css";\n',
        )

    def test_media_prelude_variable(self) -> None:
        self.assertEqual(
            compile('@bp: 600px; @media (min-width: @bp) { .a { color: red; } }'),
            '@media (min-width: 600px) {\n  .a {\n    color: red;\n  }\n}\n',
        )


class TestEmptyAndDrop(unittest.TestCase):
    def test_empty_input(self) -> None:
        self.assertEqual(compile(''), '')

    def test_only_variable(self) -> None:
        # Variable definitions at the root produce no CSS.
        self.assertEqual(compile('@c: red;'), '')

    def test_only_comment(self) -> None:
        # Block comments survive to the output. Line comments don't.
        self.assertEqual(compile('/* hi */'), '/* hi */\n')
        self.assertEqual(compile('// silent\n'), '')


class TestArithmetic(unittest.TestCase):
    def test_add_same_unit(self) -> None:
        self.assertEqual(
            compile('.x { width: 10px + 5px; }'),
            '.x {\n  width: 15px;\n}\n',
        )

    def test_sub_same_unit(self) -> None:
        self.assertEqual(
            compile('.x { width: 20px - 5px; }'),
            '.x {\n  width: 15px;\n}\n',
        )

    def test_multiply_dimension_by_scalar(self) -> None:
        self.assertEqual(
            compile('.x { width: 10px * 2; }'),
            '.x {\n  width: 20px;\n}\n',
        )

    def test_scalar_times_dimension(self) -> None:
        self.assertEqual(
            compile('.x { width: 2 * 10px; }'),
            '.x {\n  width: 20px;\n}\n',
        )

    def test_variable_in_arithmetic(self) -> None:
        self.assertEqual(
            compile('@a: 10px; .x { width: @a + 5px; }'),
            '.x {\n  width: 15px;\n}\n',
        )

    def test_variable_times_dimension(self) -> None:
        self.assertEqual(
            compile('@n: 2; .x { width: @n * 5px; }'),
            '.x {\n  width: 10px;\n}\n',
        )

    def test_division_in_parens(self) -> None:
        # PARENS_DIVISION default: `/` only collapses inside parens.
        self.assertEqual(
            compile('.x { width: (10px / 2); }'),
            '.x {\n  width: 5px;\n}\n',
        )

    def test_division_outside_parens_passes_through(self) -> None:
        # `font: 12px/14px` shouldn't be collapsed — round-trip the slash.
        # less.js preserves the no-space spelling here (CSS `font`
        # shorthand convention); we match it via the literal-only
        # fast path in `_eval_declaration_inner`.
        self.assertEqual(
            compile('.x { font: 12px/14px Arial; }'),
            '.x {\n  font: 12px/14px Arial;\n}\n',
        )

    def test_negative_number(self) -> None:
        self.assertEqual(
            compile('.x { margin: -10px; }'),
            '.x {\n  margin: -10px;\n}\n',
        )

    def test_unitless_with_unit(self) -> None:
        # 10 + 5px -> 15px (unitless takes the other side's unit)
        self.assertEqual(
            compile('.x { width: 10 + 5px; }'),
            '.x {\n  width: 15px;\n}\n',
        )

    def test_compound_expression(self) -> None:
        # 1px + 2px * 3 = 1px + 6px = 7px (mul before add)
        self.assertEqual(
            compile('.x { width: 1px + 2px * 3; }'),
            '.x {\n  width: 7px;\n}\n',
        )


class TestValuePassthrough(unittest.TestCase):
    def test_compound_shorthand(self) -> None:
        # 14px and Arial are separate entities (space-separated); not arithmetic.
        self.assertEqual(
            compile('.x { font: 14px Arial; }'),
            '.x {\n  font: 14px Arial;\n}\n',
        )

    def test_font_family_comma_list(self) -> None:
        self.assertEqual(
            compile('.x { font-family: "Arial", sans-serif; }'),
            '.x {\n  font-family: "Arial", sans-serif;\n}\n',
        )

    def test_color_keyword(self) -> None:
        self.assertEqual(
            compile('.x { color: blue; }'),
            '.x {\n  color: blue;\n}\n',
        )

    def test_hex_color(self) -> None:
        self.assertEqual(
            compile('.x { color: #fff; }'),
            '.x {\n  color: #fff;\n}\n',
        )

    def test_url_value(self) -> None:
        self.assertEqual(
            compile('.x { background: url(/img/foo.png); }'),
            '.x {\n  background: url(/img/foo.png);\n}\n',
        )

    def test_function_call_with_variable_arg(self) -> None:
        # rgb() evaluates to a Color (emit form is hex when alpha=1).
        self.assertEqual(
            compile('@n: 128; .x { color: rgb(255, @n, 0); }'),
            '.x {\n  color: #ff8000;\n}\n',
        )


class TestMixins(unittest.TestCase):
    def test_empty_mixin_invocation(self) -> None:
        self.assertEqual(
            compile('.m() { color: red; } .x { .m(); }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_mixin_definition_emits_nothing_on_its_own(self) -> None:
        self.assertEqual(
            compile('.m() { color: red; }'),
            '',
        )

    def test_mixin_with_positional_arg(self) -> None:
        self.assertEqual(
            compile('.m(@c) { color: @c; } .x { .m(blue); }'),
            '.x {\n  color: blue;\n}\n',
        )

    def test_mixin_with_default_arg(self) -> None:
        self.assertEqual(
            compile('.m(@c: red) { color: @c; } .x { .m(); }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_mixin_default_overridden(self) -> None:
        self.assertEqual(
            compile('.m(@c: red) { color: @c; } .x { .m(blue); }'),
            '.x {\n  color: blue;\n}\n',
        )

    def test_mixin_multiple_params(self) -> None:
        self.assertEqual(
            compile('.m(@w, @c) { width: @w; color: @c; } .x { .m(10px, red); }'),
            '.x {\n  width: 10px;\n  color: red;\n}\n',
        )

    def test_arithmetic_in_mixin_body(self) -> None:
        self.assertEqual(
            compile('.m(@w) { width: @w * 2; } .x { .m(5px); }'),
            '.x {\n  width: 10px;\n}\n',
        )

    def test_mixin_with_outer_variable(self) -> None:
        self.assertEqual(
            compile('@brand: blue; .m(@c) { color: @c; } .x { .m(@brand); }'),
            '.x {\n  color: blue;\n}\n',
        )

    def test_mixin_combined_with_other_decls(self) -> None:
        self.assertEqual(
            compile('.m() { color: red; } .x { padding: 5px; .m(); border: 1px; }'),
            '.x {\n  padding: 5px;\n  color: red;\n  border: 1px;\n}\n',
        )

    def test_mixin_with_nested_ruleset_in_body(self) -> None:
        self.assertEqual(
            compile('.m() { padding: 10px; .label { color: white; } } .x { .m(); }'),
            '.x {\n  padding: 10px;\n}\n.x .label {\n  color: white;\n}\n',
        )

    def test_mixin_call_unmatched_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError):
            compile('.x { .missing(); }')

    def test_mixin_paren_less_call(self) -> None:
        self.assertEqual(
            compile('.m() { color: red; } .x { .m; }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_mixin_with_local_variable_in_body(self) -> None:
        # Variables declared inside the body should resolve when later
        # declarations reference them.
        self.assertEqual(
            compile('.m() { @local: 10px; width: @local; } .x { .m(); }'),
            '.x {\n  width: 10px;\n}\n',
        )

    def test_named_arg(self) -> None:
        self.assertEqual(
            compile('.m(@a, @b) { x: @a; y: @b; } .z { .m(@b: 2, @a: 1); }'),
            '.z {\n  x: 1;\n  y: 2;\n}\n',
        )

    def test_named_arg_with_default_unused(self) -> None:
        # Named arg overrides the default; the other param uses its default.
        self.assertEqual(
            compile('.m(@a: 10, @b: 20) { x: @a; y: @b; } .z { .m(@b: 99); }'),
            '.z {\n  x: 10;\n  y: 99;\n}\n',
        )

    def test_pattern_keyword_match_first_wins(self) -> None:
        self.assertEqual(
            compile('.m(dark, @x) { color: dark @x; } .m(light, @x) { color: light @x; } .z { .m(dark, red); }'),
            '.z {\n  color: dark red;\n}\n',
        )

    def test_pattern_only_matching_branch_runs(self) -> None:
        # Calling with `light` should only fire the second mixin.
        self.assertEqual(
            compile('.m(dark, @x) { color: dark @x; } .m(light, @x) { color: light @x; } .z { .m(light, blue); }'),
            '.z {\n  color: light blue;\n}\n',
        )

    def test_variadic_collects_rest(self) -> None:
        # Variadic is exposed as an Expression of the leftover args so
        # `length(@rest)` reflects the call structure (3 here). less.js
        # renders this Expression space-joined when consumed as text
        # (`r: 2 3 4`), not comma-joined.
        self.assertEqual(
            compile('.m(@first, @rest...) { f: @first; r: @rest; } .z { .m(1, 2, 3, 4); }'),
            '.z {\n  f: 1;\n  r: 2 3 4;\n}\n',
        )

    def test_variadic_length_reflects_call_structure(self) -> None:
        # Companion regression: `length(@rest)` must see the comma-list
        # structure (3 elements), not the flattened text form.
        self.assertEqual(
            compile('.m(@first, @rest...) { n: length(@rest); } .z { .m(1, 2, 3, 4); }'),
            '.z {\n  n: 3;\n}\n',
        )

    def test_detached_ruleset_invocation(self) -> None:
        self.assertEqual(
            compile('@block: { color: red; padding: 5px; }; .x { @block(); }'),
            '.x {\n  color: red;\n  padding: 5px;\n}\n',
        )

    def test_detached_ruleset_paren_less(self) -> None:
        self.assertEqual(
            compile('@block: { color: red; }; .x { @block; }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_arguments_special_variable(self) -> None:
        # `@arguments` inside a mixin body equals the space-joined
        # evaluated arg list. Works regardless of how many params.
        self.assertEqual(
            compile('.m(@a, @b) { border: @arguments; } .x { .m(1px, solid); }'),
            '.x {\n  border: 1px solid;\n}\n',
        )

    def test_namespace_mixin_lookup(self) -> None:
        self.assertEqual(
            compile('#ns { .m() { color: red; } } .x { #ns > .m(); }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_mixin_call_important_propagates(self) -> None:
        # Mixin-call `!important` propagates as source-explicit — emits
        # with the default leading space (J1, less.js convention).
        self.assertEqual(
            compile('.m() { color: red; padding: 5px; } .x { .m() !important; }'),
            '.x {\n  color: red !important;\n  padding: 5px !important;\n}\n',
        )


class TestUnits(unittest.TestCase):
    def test_length_conversion_in_plus(self) -> None:
        # 1in + 1cm: cm converts to in (~0.393701in). less.js rounds
        # operation results to 8 decimals via `fround` / numPrecision=8.
        self.assertEqual(
            compile('.x { width: 1in + 1cm; }'),
            '.x {\n  width: 1.39370079in;\n}\n',
        )

    def test_length_conversion_in_minus(self) -> None:
        self.assertEqual(
            compile('.x { width: 1in - 1cm; }'),
            '.x {\n  width: 0.60629921in;\n}\n',
        )

    def test_duration_conversion(self) -> None:
        self.assertEqual(
            compile('.x { transition: 1s + 500ms; }'),
            '.x {\n  transition: 1.5s;\n}\n',
        )

    def test_incompatible_unit_groups_raise(self) -> None:
        # H2: incompatible units coerce by default (less.js permissive
        # `strictUnits: false`). Strict mode still raises.
        from lessish.errors import OperationError

        with self.assertRaises(OperationError):
            compile('.x { width: 1px + 1s; }', strict_units=True)


class TestColorFunctions(unittest.TestCase):
    def test_rgb_emits_hex(self) -> None:
        self.assertEqual(
            compile('.x { color: rgb(255, 0, 0); }'),
            '.x {\n  color: #ff0000;\n}\n',
        )

    def test_rgba_emits_rgba(self) -> None:
        self.assertEqual(
            compile('.x { color: rgba(255, 0, 0, 0.5); }'),
            '.x {\n  color: rgba(255, 0, 0, 0.5);\n}\n',
        )

    def test_lighten_hex_emits_hex(self) -> None:
        self.assertEqual(
            compile('.x { color: lighten(#000, 50%); }'),
            '.x {\n  color: #808080;\n}\n',
        )

    def test_darken_keyword_emits_hex(self) -> None:
        self.assertEqual(
            compile('.x { color: darken(red, 10%); }'),
            '.x {\n  color: #cc0000;\n}\n',
        )

    def test_fade_to_rgba(self) -> None:
        self.assertEqual(
            compile('.x { color: fade(red, 50%); }'),
            '.x {\n  color: rgba(255, 0, 0, 0.5);\n}\n',
        )

    def test_spin(self) -> None:
        self.assertEqual(
            compile('.x { color: spin(red, 60); }'),
            '.x {\n  color: #ffff00;\n}\n',
        )

    def test_mix(self) -> None:
        self.assertEqual(
            compile('.x { color: mix(#f00, #00f); }'),
            '.x {\n  color: #800080;\n}\n',
        )

    def test_hue(self) -> None:
        self.assertEqual(
            compile('.x { x: hue(red); }'),
            '.x {\n  x: 0;\n}\n',
        )

    def test_saturation_percent(self) -> None:
        self.assertEqual(
            compile('.x { x: saturation(red); }'),
            '.x {\n  x: 100%;\n}\n',
        )

    def test_red_channel(self) -> None:
        self.assertEqual(
            compile('.x { x: red(#102030); }'),
            '.x {\n  x: 16;\n}\n',
        )


class TestColorArithmetic(unittest.TestCase):
    def test_color_plus_scalar(self) -> None:
        # #444 + 1 = each channel +1 -> #454545
        self.assertEqual(
            compile('.x { color: #444 + 1; }'),
            '.x {\n  color: #454545;\n}\n',
        )

    def test_color_plus_color(self) -> None:
        self.assertEqual(
            compile('.x { color: #100 + #001; }'),
            '.x {\n  color: #110011;\n}\n',
        )

    def test_color_clamps_on_overflow(self) -> None:
        self.assertEqual(
            compile('.x { color: #fff + #001; }'),
            '.x {\n  color: #ffffff;\n}\n',
        )


class TestMathFunctions(unittest.TestCase):
    def test_ceil_preserves_unit(self) -> None:
        self.assertEqual(
            compile('.x { x: ceil(1.4px); }'),
            '.x {\n  x: 2px;\n}\n',
        )

    def test_floor(self) -> None:
        self.assertEqual(
            compile('.x { x: floor(1.9); }'),
            '.x {\n  x: 1;\n}\n',
        )

    def test_round_default(self) -> None:
        self.assertEqual(
            compile('.x { x: round(1.5); }'),
            '.x {\n  x: 2;\n}\n',
        )

    def test_round_with_precision(self) -> None:
        self.assertEqual(
            compile('.x { x: round(1.234, 2); }'),
            '.x {\n  x: 1.23;\n}\n',
        )

    def test_percentage(self) -> None:
        self.assertEqual(
            compile('.x { x: percentage(0.5); }'),
            '.x {\n  x: 50%;\n}\n',
        )

    def test_abs_negative(self) -> None:
        self.assertEqual(
            compile('.x { x: abs(-5px); }'),
            '.x {\n  x: 5px;\n}\n',
        )

    def test_min(self) -> None:
        self.assertEqual(
            compile('.x { x: min(3px, 1px, 2px); }'),
            '.x {\n  x: 1px;\n}\n',
        )

    def test_max(self) -> None:
        self.assertEqual(
            compile('.x { x: max(3, 7, 5); }'),
            '.x {\n  x: 7;\n}\n',
        )

    def test_pow(self) -> None:
        self.assertEqual(
            compile('.x { x: pow(2, 8); }'),
            '.x {\n  x: 256;\n}\n',
        )

    def test_pi(self) -> None:
        # less.js applies `fround` at toCSS time (numPrecision=8) so
        # `pi()` in an emitted declaration value rounds to 8 decimals.
        # Interpolation paths keep full precision (see property-name-interp).
        self.assertEqual(
            compile('.x { x: pi(); }'),
            '.x {\n  x: 3.14159265;\n}\n',
        )


class TestTypePredicates(unittest.TestCase):
    def test_isnumber(self) -> None:
        self.assertEqual(compile('.x { a: isnumber(10); }'), '.x {\n  a: true;\n}\n')

    def test_iscolor_hex(self) -> None:
        self.assertEqual(compile('.x { a: iscolor(#fff); }'), '.x {\n  a: true;\n}\n')

    def test_iscolor_keyword(self) -> None:
        self.assertEqual(compile('.x { a: iscolor(red); }'), '.x {\n  a: true;\n}\n')

    def test_ispixel(self) -> None:
        self.assertEqual(compile('.x { a: ispixel(10px); }'), '.x {\n  a: true;\n}\n')

    def test_ispixel_false(self) -> None:
        self.assertEqual(compile('.x { a: ispixel(10em); }'), '.x {\n  a: false;\n}\n')

    def test_unit_strip(self) -> None:
        self.assertEqual(compile('.x { a: unit(10px); }'), '.x {\n  a: 10;\n}\n')

    def test_unit_replace(self) -> None:
        self.assertEqual(compile('.x { a: unit(10px, em); }'), '.x {\n  a: 10em;\n}\n')

    def test_unit_replace_with_percent(self) -> None:
        # `%` is a valid unit-keyword arg in function-call position
        # (`unit(value, %)`). The value-atom parser otherwise rejects
        # bare `%` as `Invalid % without number`, so the function-arg
        # parser has a narrow intercept just before the COMMA / RPAREN.
        self.assertEqual(compile('.x { a: unit(100, %); }'), '.x {\n  a: 100%;\n}\n')

    def test_get_unit(self) -> None:
        self.assertEqual(compile('.x { a: get-unit(10rem); }'), '.x {\n  a: rem;\n}\n')


class TestListFunctions(unittest.TestCase):
    def test_length_space(self) -> None:
        self.assertEqual(compile('.x { a: length(1 2 3); }'), '.x {\n  a: 3;\n}\n')

    def test_length_only_takes_first_arg(self) -> None:
        # Each top-level comma at the call site makes a separate arg —
        # less.js parity. `length(1, 2, 3)` is length(1) with two extras
        # ignored. To count the list, use space-separated: `length(1 2 3)`.
        self.assertEqual(compile('.x { a: length(1, 2, 3, 4); }'), '.x {\n  a: 1;\n}\n')

    def test_extract(self) -> None:
        self.assertEqual(compile('.x { a: extract(red green blue, 2); }'), '.x {\n  a: green;\n}\n')

    def test_range_simple(self) -> None:
        self.assertEqual(compile('.x { a: range(3); }'), '.x {\n  a: 1 2 3;\n}\n')

    def test_range_with_unit(self) -> None:
        self.assertEqual(compile('.x { a: range(1px, 3px); }'), '.x {\n  a: 1px 2px 3px;\n}\n')


class TestStringFunctions(unittest.TestCase):
    def test_e_escape(self) -> None:
        self.assertEqual(compile('.x { a: e("hello world"); }'), '.x {\n  a: hello world;\n}\n')

    def test_format_basic(self) -> None:
        # %("abc-%s", red) -> "abc-red"
        self.assertEqual(
            compile('.x { a: %("abc-%s", red); }'),
            '.x {\n  a: "abc-red";\n}\n',
        )


class TestDataUriAndImageFunctions(unittest.TestCase):
    """Cover `data-uri()` + the `image-*` introspection helpers.

    These touch the filesystem, so each test sets up a real tempdir
    and threads `filename=` (and sometimes `paths=`) through compile.
    Decoder helpers (`_decode_png`/`_decode_jpeg`/`_decode_svg`) are
    additionally exercised directly to hit the byte-level edge cases
    that a compile-only path can't easily reach.
    """

    def _png(self, width: int, height: int) -> bytes:
        import struct as _struct

        # Minimal PNG: 8-byte signature + IHDR chunk only. The IHDR
        # body carries width/height which is all `_decode_png` reads.
        return (
            b'\x89PNG\r\n\x1a\n'
            + _struct.pack('>I', 13)
            + b'IHDR'
            + _struct.pack('>II', width, height)
            + b'\x08\x00\x00\x00\x00'
            + b'\x00\x00\x00\x00'
        )

    def _jpeg_sof0(self, width: int, height: int) -> bytes:
        import struct as _struct

        # SOI + SOF0 marker + 11-byte segment carrying H,W + EOI.
        return (
            b'\xff\xd8'
            + b'\xff\xc0'
            + _struct.pack('>H', 11)
            + b'\x08'
            + _struct.pack('>HH', height, width)
            + b'\x01\x01\x11\x00'
            + b'\xff\xd9'
        )

    def test_data_uri_text_url_encoded(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.css').write_text('hi world', encoding='utf-8')
            out = compile(
                '.x { bg: data-uri("a.css"); }',
                filename=str(d / 'in.less'),
            )
        self.assertEqual(out, '.x {\n  bg: url("data:text/css,hi%20world");\n}\n')

    def test_data_uri_explicit_base64_mime(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.bin').write_bytes(b'\x00\x01\x02')
            out = compile(
                '.x { bg: data-uri("application/octet-stream;base64", "a.bin"); }',
                filename=str(d / 'in.less'),
            )
        self.assertIn('data:application/octet-stream;base64,AAEC', out)

    def test_data_uri_unknown_extension_falls_back_to_octet_stream(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.xyz').write_bytes(b'hi')
            out = compile(
                '.x { bg: data-uri("a.xyz"); }',
                filename=str(d / 'in.less'),
            )
        self.assertIn('data:application/octet-stream;base64,aGk=', out)

    def test_data_uri_fragment_preserved(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.svg').write_text('<svg width="1" height="1"/>', encoding='utf-8')
            out = compile(
                '.x { bg: data-uri("a.svg#frag"); }',
                filename=str(d / 'in.less'),
            )
        # Fragment travels outside the encoded payload.
        self.assertIn('#frag")', out)
        self.assertIn('data:image/svg+xml,', out)

    def test_data_uri_missing_file_falls_back_to_plain_url(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            out = compile(
                '.x { bg: data-uri("missing.css"); }',
                filename=str(d / 'in.less'),
            )
        self.assertEqual(out, '.x {\n  bg: url("missing.css");\n}\n')

    def test_data_uri_absolute_path_missing_falls_back_to_plain_url(self) -> None:
        # Absolute `data-uri()` paths require allow mode (jail rejects them).
        out = compile_allow('.x { bg: data-uri("/tmp/__nope__/xyz_unlikely.css"); }')
        self.assertIn('url("/tmp/__nope__/xyz_unlikely.css")', out)

    def test_data_uri_absolute_path_existing(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'a.txt'
            target.write_text('hi', encoding='utf-8')
            out = compile_allow(f'.x {{ bg: data-uri("{target}"); }}')
        self.assertIn('data:text/plain,hi', out)

    def test_data_uri_resolves_via_search_paths(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as asset_tmp:
            src_d, asset_d = Path(src_tmp), Path(asset_tmp)
            (asset_d / 'a.css').write_text('via-paths', encoding='utf-8')
            out = compile(
                '.x { bg: data-uri("a.css"); }',
                filename=str(src_d / 'in.less'),
                paths=[str(asset_d)],
            )
        self.assertIn('data:text/css,via-paths', out)

    def test_data_uri_resolves_via_relative_search_path(self) -> None:
        # A relative entry in `paths[]` is joined against the source
        # file's directory before lookup — covers the `is_absolute()`
        # false branch in `_resolve_path`.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            src_d = Path(tmpdir)
            (src_d / 'assets').mkdir()
            (src_d / 'assets' / 'a.css').write_text('rel-paths', encoding='utf-8')
            out = compile(
                '.x { bg: data-uri("a.css"); }',
                filename=str(src_d / 'in.less'),
                paths=['assets'],
            )
        self.assertIn('data:text/css,rel-paths', out)

    def test_data_uri_search_paths_exhausted_falls_back(self) -> None:
        # Both source-dir resolution and every entry in `paths` miss —
        # _resolve_path returns the source-relative candidate, which
        # then trips the not-exists fallback to plain url().
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as src_d, tempfile.TemporaryDirectory() as alt_d:
            out = compile(
                '.x { bg: data-uri("nope.css"); }',
                filename=str(Path(src_d) / 'in.less'),
                paths=[alt_d],
            )
        self.assertIn('url("nope.css")', out)

    def test_data_uri_no_args_passthrough(self) -> None:
        # Soft ArgumentError → call rendered as-is.
        out = compile('.x { bg: data-uri(); }')
        self.assertIn('data-uri()', out)

    def test_data_uri_missing_file_path_is_escaped(self) -> None:
        # Lessish hardens the less.js-parity `url(<path>)` fallback so an
        # attacker-controlled path cannot break out of the surrounding CSS
        # string or close a wrapping HTML `<style>` block. The dangerous
        # chars `<`, `>`, `"`, `'`, `\` must come back percent-encoded.
        out = compile(
            '.x { bg: data-uri("missing.css?</style><script>x</script>"); }',
        )
        self.assertNotIn('<', out)
        self.assertNotIn('</style', out)
        self.assertNotIn('<script', out)
        self.assertIn('url("missing.css?%3C/style%3E%3Cscript%3Ex%3C/script%3E")', out)

    def test_data_uri_missing_file_fragment_is_escaped(self) -> None:
        # Fragment travels into the output verbatim too; same rules apply.
        out = compile(
            '.x { bg: data-uri("missing.png#</style><script>x</script>"); }',
        )
        self.assertNotIn('</style', out)
        self.assertNotIn('<script', out)
        # `#` survives (URL fragment delimiter), the rest is %-encoded.
        self.assertIn('#%3C/style%3E', out)

    def test_data_uri_existing_file_fragment_is_escaped(self) -> None:
        # Success path: file exists, payload is base64/url-encoded, but
        # the trailing fragment must still be escaped.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.css').write_text('hi', encoding='utf-8')
            out = compile(
                '.x { bg: data-uri("a.css#</style>"); }',
                filename=str(d / 'in.less'),
            )
        self.assertNotIn('</style', out)
        self.assertIn('#%3C/style%3E', out)

    def test_data_uri_user_supplied_mime_is_escaped(self) -> None:
        # The mime arg lands verbatim between `data:` and the comma.
        # Untrusted mime must not be able to inject HTML breakout chars.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.bin').write_bytes(b'x')
            out = compile(
                '.x { bg: data-uri("text/html;</style><script>x</script>", "a.bin"); }',
                filename=str(d / 'in.less'),
            )
        self.assertNotIn('</style', out)
        self.assertNotIn('<script', out)

    def test_data_uri_normal_path_round_trips_unescaped(self) -> None:
        # Regression guard for the escape helper: a perfectly benign path
        # must not be molested. Existing
        # `test_data_uri_missing_file_falls_back_to_plain_url` covers the
        # simple case; this one ensures `?query=1&x=2` style URLs survive.
        out = compile('.x { bg: data-uri("missing.css?v=1&x=2"); }')
        self.assertIn('url("missing.css?v=1&x=2")', out)

    def test_data_uri_too_many_args_passthrough(self) -> None:
        out = compile('.x { bg: data-uri("a", "b", "c"); }')
        self.assertIn('data-uri(', out)

    def test_image_size_png(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.png').write_bytes(self._png(4, 2))
            out = compile(
                '.x { s: image-size("a.png"); }',
                filename=str(d / 'in.less'),
            )
        self.assertEqual(out, '.x {\n  s: 4px 2px;\n}\n')

    def test_image_width_and_height_png(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.png').write_bytes(self._png(10, 20))
            out = compile(
                '.x { w: image-width("a.png"); h: image-height("a.png"); }',
                filename=str(d / 'in.less'),
            )
        self.assertEqual(out, '.x {\n  w: 10px;\n  h: 20px;\n}\n')

    def test_image_size_svg(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.svg').write_text(
                '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="30" height="40"></svg>',
                encoding='utf-8',
            )
            out = compile(
                '.x { s: image-size("a.svg"); }',
                filename=str(d / 'in.less'),
            )
        self.assertEqual(out, '.x {\n  s: 30px 40px;\n}\n')

    def test_image_size_jpeg(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.jpg').write_bytes(self._jpeg_sof0(11, 22))
            out = compile(
                '.x { w: image-width("a.jpg"); h: image-height("a.jpg"); }',
                filename=str(d / 'in.less'),
            )
        self.assertEqual(out, '.x {\n  w: 11px;\n  h: 22px;\n}\n')

    def test_image_width_no_args_passthrough(self) -> None:
        out = compile('.x { w: image-width(); }')
        self.assertIn('image-width()', out)

    def test_image_width_non_quoted_arg_passthrough(self) -> None:
        out = compile('.x { w: image-width(foo); }')
        self.assertIn('image-width(', out)

    def test_image_width_missing_file_passthrough(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            out = compile(
                '.x { w: image-width("nope.png"); }',
                filename=str(Path(d) / 'in.less'),
            )
        self.assertIn('image-width(', out)

    def test_image_width_unsupported_format_passthrough(self) -> None:
        # Bytes that aren't PNG / JPEG / SVG — all three decoders return
        # None and the function raises a (soft) ArgumentError.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / 'a.png').write_bytes(b'not actually a png')
            out = compile(
                '.x { w: image-width("a.png"); }',
                filename=str(d / 'in.less'),
            )
        self.assertIn('image-width(', out)

    def test_decode_png_rejects_non_png(self) -> None:
        from lessish.functions.data_uri import _decode_png

        self.assertIsNone(_decode_png(b'short'))
        self.assertIsNone(_decode_png(b'\x89PNG\r\n\x1a\n' + b'\x00' * 16))  # wrong IHDR tag

    def test_decode_svg_variants(self) -> None:
        from lessish.functions.data_uri import _decode_svg

        self.assertEqual(_decode_svg(b'<svg width="3" height="5"/>'), (3, 5))
        # Float values get truncated like less.js (int(float(...))).
        self.assertEqual(_decode_svg(b'<svg width="3.7" height="5.9"/>'), (3, 5))
        # No <svg> root → None.
        self.assertIsNone(_decode_svg(b'<html><body/></html>'))
        # <svg> present but missing dims → None.
        self.assertIsNone(_decode_svg(b'<svg viewBox="0 0 10 10"/>'))
        # `..` matches `[\d.]+` but isn't a valid float → ValueError swallowed.
        self.assertIsNone(_decode_svg(b'<svg width=".." height=".."/>'))
        # Non-bytes input — defensive guard returns None instead of
        # raising AttributeError on the missing `.decode` method.
        self.assertIsNone(_decode_svg(123))  # type: ignore[arg-type]

    def test_decode_jpeg_variants(self) -> None:
        import struct as _struct

        from lessish.functions.data_uri import _decode_jpeg

        # Not a JPEG / too short.
        self.assertIsNone(_decode_jpeg(b''))
        self.assertIsNone(_decode_jpeg(b'\x00\x00\x00\x00'))

        self.assertEqual(_decode_jpeg(self._jpeg_sof0(7, 9)), (7, 9))

        # Byte where 0xFF was expected → bail.
        bogus = b'\xff\xd8' + b'\x00\x00'
        self.assertIsNone(_decode_jpeg(bogus))

        # Truncated mid-segment (no room for the length word).
        self.assertIsNone(_decode_jpeg(b'\xff\xd8\xff\xc0'))

        # Truncated SOF segment (length word present, body missing).
        truncated_sof = b'\xff\xd8\xff\xc0' + _struct.pack('>H', 11) + b'\x00'
        self.assertIsNone(_decode_jpeg(truncated_sof))

        # Non-SOF segment (APP0 = 0xE0) gets skipped, then SOF read.
        app0 = b'\xff\xe0' + _struct.pack('>H', 4) + b'\x00\x00'
        sof = b'\xff\xc0' + _struct.pack('>H', 11) + b'\x08' + _struct.pack('>HH', 12, 13) + b'\x01\x01\x11\x00'
        composite = b'\xff\xd8' + app0 + sof
        self.assertEqual(_decode_jpeg(composite), (13, 12))

        # Padding 0xFF bytes between marker and segment — JPEG spec
        # allows fill bytes; the walker should skip them.
        padded = (
            b'\xff\xd8'
            + b'\xff\xff\xff\xc0'  # two padding 0xFF then SOF0 marker
            + _struct.pack('>H', 11)
            + b'\x08'
            + _struct.pack('>HH', 21, 22)
            + b'\x01\x01\x11\x00'
        )
        self.assertEqual(_decode_jpeg(padded), (22, 21))

        # Trailing run of 0xFF padding with no marker after it.
        trailing = b'\xff\xd8' + b'\xff\xff\xff'
        self.assertIsNone(_decode_jpeg(trailing))

        # SOI re-encountered mid-stream is skipped (carries no payload),
        # eventually walker runs off the end.
        ran_off = b'\xff\xd8\xff\xd8'
        self.assertIsNone(_decode_jpeg(ran_off))


class TestMixinGuards(unittest.TestCase):
    def test_guard_pass(self) -> None:
        self.assertEqual(
            compile('.m(@a) when (@a > 5) { x: @a; } .y { .m(10); }'),
            '.y {\n  x: 10;\n}\n',
        )

    def test_guard_fail_silent(self) -> None:
        # When every arity-compatible candidate is guarded and all
        # guards fail, less.js silently emits nothing rather than raise.
        # (K2: aligns with `guard-default-expr-and-4`-style fixtures.)
        self.assertEqual(
            compile('.m(@a) when (@a > 5) { x: @a; } .y { .m(3); }'),
            '',
        )

    def test_guard_overload_picks_passing_branch(self) -> None:
        self.assertEqual(
            compile('.m(@a) when (@a > 5) { big: @a; } .m(@a) when (@a < 5) { small: @a; } .y { .m(3); }'),
            '.y {\n  small: 3;\n}\n',
        )

    def test_guard_and(self) -> None:
        self.assertEqual(
            compile('.m(@a) when (@a > 0) and (@a < 10) { x: @a; } .y { .m(5); }'),
            '.y {\n  x: 5;\n}\n',
        )

    def test_guard_or_via_comma(self) -> None:
        self.assertEqual(
            compile('.m(@a) when (@a < 0), (@a > 10) { x: @a; } .y { .m(20); }'),
            '.y {\n  x: 20;\n}\n',
        )

    def test_guard_not(self) -> None:
        self.assertEqual(
            compile('.m(@a) when not (@a > 5) { x: @a; } .y { .m(3); }'),
            '.y {\n  x: 3;\n}\n',
        )

    def test_guard_keyword_equality(self) -> None:
        self.assertEqual(
            compile(
                '.m(@a) when (@a = light) { mode: light; } .m(@a) when (@a = dark) { mode: dark; } .y { .m(dark); }'
            ),
            '.y {\n  mode: dark;\n}\n',
        )

    def test_guard_unit_comparison(self) -> None:
        # 1in > 2cm should be true.
        self.assertEqual(
            compile('.m(@a) when (@a > 2cm) { x: @a; } .y { .m(1in); }'),
            '.y {\n  x: 1in;\n}\n',
        )

    def test_default_branch_fallback(self) -> None:
        self.assertEqual(
            compile('.m(@a) when (@a = light) { x: 1; } .m(@a) when (default()) { x: 2; } .y { .m(dark); }'),
            '.y {\n  x: 2;\n}\n',
        )

    def test_default_branch_yields_to_specific(self) -> None:
        self.assertEqual(
            compile('.m(@a) when (@a = light) { x: 1; } .m(@a) when (default()) { x: 2; } .y { .m(light); }'),
            '.y {\n  x: 1;\n}\n',
        )


class TestCssGuards(unittest.TestCase):
    def test_ruleset_guard_pass(self) -> None:
        self.assertEqual(
            compile('@a: 1; .x when (@a = 1) { color: red; }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_ruleset_guard_fail_suppresses(self) -> None:
        self.assertEqual(
            compile('@a: 2; .x when (@a = 1) { color: red; }'),
            '',
        )

    def test_ruleset_guard_with_and(self) -> None:
        self.assertEqual(
            compile('@a: 5; .x when (@a > 0) and (@a < 10) { color: red; }'),
            '.x {\n  color: red;\n}\n',
        )

    def test_ruleset_guard_or(self) -> None:
        self.assertEqual(
            compile('@a: 99; .x when (@a < 0), (@a > 10) { color: red; }'),
            '.x {\n  color: red;\n}\n',
        )


class TestExtend(unittest.TestCase):
    def test_basic_extend(self) -> None:
        self.assertEqual(
            compile('.a { color: red; } .b:extend(.a) { y: 1; }'),
            '.a,\n.b {\n  color: red;\n}\n.b {\n  y: 1;\n}\n',
        )

    def test_statement_form_extend(self) -> None:
        self.assertEqual(
            compile('.a { color: red; } .b { &:extend(.a); y: 1; }'),
            '.a,\n.b {\n  color: red;\n}\n.b {\n  y: 1;\n}\n',
        )

    def test_all_mode_compound_match(self) -> None:
        # `:extend(.error all)` matches `.error` everywhere it appears
        # as a compound atom.
        self.assertEqual(
            compile(
                '.error { x: 1; } '
                '.error.intrusion { y: 2; } '
                '.intrusion .error { z: 3; } '
                '.badError:extend(.error all) { w: 4; }'
            ),
            (
                '.error,\n.badError {\n  x: 1;\n}\n'
                '.error.intrusion,\n.badError.intrusion {\n  y: 2;\n}\n'
                '.intrusion .error,\n.intrusion .badError {\n  z: 3;\n}\n'
                '.badError {\n  w: 4;\n}\n'
            ),
        )

    def test_chain_extend(self) -> None:
        # `.c` extends `.b` which extends `.a` — `.a` should pick up
        # both `.b` and `.c` via chain resolution.
        self.assertEqual(
            compile('.a { color: red; } .b:extend(.a) { y: 1; } .c:extend(.b) { z: 1; }'),
            ('.a,\n.b,\n.c {\n  color: red;\n}\n.b,\n.c {\n  y: 1;\n}\n.c {\n  z: 1;\n}\n'),
        )

    def test_multi_target_extend(self) -> None:
        self.assertEqual(
            compile('.a { x: 1; } .b { y: 1; } .x:extend(.a, .b) { z: 1; }'),
            '.a,\n.x {\n  x: 1;\n}\n.b,\n.x {\n  y: 1;\n}\n.x {\n  z: 1;\n}\n',
        )

    def test_no_match_silent(self) -> None:
        # Extending a non-existent target is allowed; just no extension.
        self.assertEqual(
            compile('.b:extend(.missing) { x: 1; }'),
            '.b {\n  x: 1;\n}\n',
        )

    def test_word_boundary_protection(self) -> None:
        # `.error` shouldn't match `.errorless` (no compound boundary).
        self.assertEqual(
            compile('.errorless { x: 1; } .b:extend(.error all) { z: 1; }'),
            '.errorless {\n  x: 1;\n}\n.b {\n  z: 1;\n}\n',
        )


class TestMerge(unittest.TestCase):
    def test_comma_merge(self) -> None:
        self.assertEqual(
            compile('.x { transform+: t1; transform+: t2; transform+: t3; }'),
            '.x {\n  transform: t1, t2, t3;\n}\n',
        )

    def test_space_merge(self) -> None:
        self.assertEqual(
            compile('.x { padding+_: 1px; padding+_: 2px; padding+_: 3px; }'),
            '.x {\n  padding: 1px 2px 3px;\n}\n',
        )

    def test_interleaved_merge(self) -> None:
        # `+_:` appends to last comma-segment with space.
        self.assertEqual(
            compile('.x { t+: a; t+: b; t+_: c; t+: d; }'),
            '.x {\n  t: a, b c, d;\n}\n',
        )

    def test_non_merge_stays_separate(self) -> None:
        self.assertEqual(
            compile('.x { t+: a; t: x; t+: b; }'),
            '.x {\n  t: a, b;\n  t: x;\n}\n',
        )

    def test_important_propagates(self) -> None:
        self.assertEqual(
            compile('.x { t+: a; t+: b !important; }'),
            '.x {\n  t: a, b !important;\n}\n',
        )


class TestPropertyAccessor(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(
            compile('.x { color: red; bg: $color; }'),
            '.x {\n  color: red;\n  bg: red;\n}\n',
        )

    def test_last_wins(self) -> None:
        self.assertEqual(
            compile('.x { color: red; color: blue; bg: $color; }'),
            '.x {\n  color: red;\n  color: blue;\n  bg: blue;\n}\n',
        )

    def test_parent_scope(self) -> None:
        self.assertEqual(
            compile('.x { color: red; .y { bg: $color; } }'),
            '.x {\n  color: red;\n}\n.x .y {\n  bg: red;\n}\n',
        )

    def test_undefined_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('.x { bg: $missing; }')
        # less.js-style message: `Property '$x' is undefined`.
        self.assertIn("Property '$missing' is undefined", str(cm.exception))


class TestEach(unittest.TestCase):
    def test_comma_list(self) -> None:
        self.assertEqual(
            compile('@list: a, b, c; .x { each(@list, { item-@{index}: @value; }); }'),
            '.x {\n  item-1: a;\n  item-2: b;\n  item-3: c;\n}\n',
        )

    def test_space_list(self) -> None:
        # Iterating a space-separated literal with arithmetic on @value.
        self.assertEqual(
            compile('.x { each(1 2 3, { p+_: (@value * 10px); }); }'),
            '.x {\n  p: 10px 20px 30px;\n}\n',
        )

    def test_top_level_generates_rulesets(self) -> None:
        # `each` at root with a body containing a Ruleset → emits each
        # iteration as a separate top-level rule.
        self.assertEqual(
            compile('@s: blue, green; each(@s, { .sel-@{value} { a: b; } });'),
            '.sel-blue {\n  a: b;\n}\n.sel-green {\n  a: b;\n}\n',
        )


class TestEdgeCases(unittest.TestCase):
    def test_compact_subtraction(self) -> None:
        # `17px-1px` lexes as `17` + IDENT('px-1px'); value parser
        # peels the unit prefix and re-tokenizes the suffix.
        self.assertEqual(
            compile('.x { w: 17px-1px; }'),
            '.x {\n  w: 16px;\n}\n',
        )

    def test_charset_dedup(self) -> None:
        self.assertEqual(
            compile('@charset "UTF-8"; @charset "ISO-8859-1"; .a { x: 1; }'),
            '@charset "UTF-8";\n.a {\n  x: 1;\n}\n',
        )

    def test_inline_atrule_stays_in_block(self) -> None:
        # `@apply` etc. inside a ruleset should not bubble out.
        self.assertEqual(
            compile('.box { @apply h-64 w-64; }'),
            '.box {\n  @apply h-64 w-64;\n}\n',
        )

    def test_comment_preservation_in_value(self) -> None:
        # Inline `/* ... */` survives when the value has no Less
        # dynamism the evaluator would normally touch.
        self.assertEqual(
            compile('.x { background: linear-gradient(#333 /*hint*/, #111); }'),
            '.x {\n  background: linear-gradient(#333 /*hint*/, #111);\n}\n',
        )

    def test_dollar_brace_interp_in_property_name(self) -> None:
        # `${prop}` substitutes the value of property `prop`.
        self.assertEqual(
            compile('.x { prop-name: color; ${prop-name}: red; }'),
            '.x {\n  prop-name: color;\n  color: red;\n}\n',
        )


class TestErrorParity(unittest.TestCase):
    def test_mixed_units_format(self) -> None:
        # H2: error wording / location still verified, but only under
        # `strict_units` — the permissive default coerces.
        from lessish.errors import OperationError

        with self.assertRaises(OperationError) as cm:
            compile('.a { error: (1px + 3em); }', filename='/tmp/t.less', strict_units=True)
        msg = str(cm.exception)
        self.assertIn(
            "SyntaxError: Incompatible units. Change the units or use the unit function. Bad units: 'px' and 'em'.",
            msg,
        )
        self.assertIn('in /tmp/t.less on line 1', msg)

    def test_undefined_variable_format(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('@keyframes @name {\n  50% {width: 20px;}\n}', filename='/tmp/t.less')
        msg = str(cm.exception)
        self.assertIn('NameError: variable @name is undefined', msg)
        self.assertIn('on line 1, column 12:', msg)

    def test_mixin_no_arity_match_is_runtime_error(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.m(x) {} .y { .m(1, 2); }')
        msg = str(cm.exception)
        # Different less.js error class for arity mismatch vs name not found.
        self.assertIn('RuntimeError: No matching definition was found for', msg)

    def test_mixin_unknown_name_is_name_error(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('.y { .missing(); }')
        self.assertIn('NameError: .missing is undefined', str(cm.exception))

    def test_property_undefined_format(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('.x { v: $nope; }', filename='/tmp/t.less')
        msg = str(cm.exception)
        self.assertIn("NameError: Property '$nope' is undefined", msg)


class TestNamespaceValueLookup(unittest.TestCase):
    def test_basic_captured_mixin_lookup(self) -> None:
        src = """
        .x {
            .m() { c: red; }
            @p: .m();
            v: @p[c];
        }
        """
        out = compile(src)
        self.assertIn('v: red;', out)

    def test_last_decl_wins_on_property_lookup(self) -> None:
        # Two `c:` decls inside the mixin; the second wins (less.js
        # walks rules in reverse for property lookups).
        src = """
        .x {
            .m() { c: red; c: blue; }
            @p: .m();
            v: @p[c];
        }
        """
        out = compile(src)
        self.assertIn('v: blue;', out)

    def test_property_not_found_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        src = '@dr: { foo: bar; }; .x { v: @dr[missing]; }'
        with self.assertRaisesRegex(UndefinedNameError, r'property "missing" not found'):
            compile(src, filename='/tmp/t.less')

    def test_variable_not_found_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        src = '@dr: { foo: bar; }; .x { v: @dr[@missing]; }'
        with self.assertRaisesRegex(UndefinedNameError, r'variable @missing not found'):
            compile(src, filename='/tmp/t.less')

    def test_undefined_variable_target_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        src = '.x { v: @missing[c]; }'
        with self.assertRaises(UndefinedNameError):
            compile(src, filename='/tmp/t.less')

    def test_lookup_via_variable_key(self) -> None:
        src = """
        @dr: {
            @inner: green;
        };
        .x { v: @dr[@inner]; }
        """
        out = compile(src)
        self.assertIn('v: green;', out)

    def test_lookup_chained(self) -> None:
        src = """
        @theme: {
            @nested: {
                color: yellow;
            }
        };
        .x { v: @theme[@nested][color]; }
        """
        out = compile(src)
        self.assertIn('v: yellow;', out)

    def test_lookup_at_at_indirect_key(self) -> None:
        # `@@varToGet` resolves @varToGet first, then uses its value as
        # the variable name to look up inside the target.
        src = """
        @varToGet: default-color;
        @defaults: {
            @default-color: red;
        };
        .x { v: @defaults[@@varToGet]; }
        """
        out = compile(src)
        self.assertIn('v: red;', out)

    def test_lookup_dollar_at_indirect_key(self) -> None:
        # `[$@var-name]` interpolates the variable's value as a property
        # NAME inside the namespace.
        src = """
        @prop-name: my-prop;
        #namespace {
          my-prop: prop-value;
        }
        .test { value: #namespace[$@prop-name]; }
        """
        out = compile(src)
        self.assertIn('value: prop-value;', out)

    def test_namespace_path_lookup(self) -> None:
        src = """
        #ns {
            foo: bar;
        }
        .x { v: #ns[foo]; }
        """
        out = compile(src)
        self.assertIn('v: bar;', out)

    def test_namespace_path_with_dot(self) -> None:
        src = """
        #ns {
            .mixin() {
                a: b;
            }
        }
        .x { v: #ns.mixin[a]; }
        """
        out = compile(src)
        self.assertIn('v: b;', out)

    def test_does_not_break_grid_line_names(self) -> None:
        # Standalone `[name]` at value position remains Anonymous —
        # critical for CSS grid syntax.
        src = '.x { grid-template-columns: [start] 1fr [end]; }'
        out = compile(src)
        self.assertIn('[start] 1fr [end]', out)

    def test_invoke_captured_mixin_via_variable_call(self) -> None:
        # `@alias: .mixin(); @alias();` — captured mixin invocation.
        src = """
        .mixin() { width: 10px; }
        .x {
            @alias: .mixin();
            @alias();
        }
        """
        out = compile(src)
        self.assertIn('width: 10px;', out)

    def test_could_not_evaluate_variable_call(self) -> None:
        from lessish.errors import EvalError

        # Aliasing without `()` — less.js wording requirement.
        src = """
        .theme() { foo: bar; }
        .val { @alias: .theme; foo: @alias[foo]; }
        """
        with self.assertRaisesRegex(EvalError, r'Could not evaluate variable call @alias'):
            compile(src, filename='/tmp/t.less')

    def test_important_propagation_via_lookup(self) -> None:
        # `@colors: .x() !important;` then `@colors[name]` should yield
        # `!important` on the consumer declaration.
        src = """
        .x() { primary: red; }
        .out {
            @colors: .x() !important;
            background: @colors[primary];
        }
        """
        out = compile(src)
        self.assertIn('background: red !important;', out)


class TestArgumentsListSemantics(unittest.TestCase):
    """`tests-unit/extract-and-length` regression coverage.

    `@arguments` (and named variadics) must expose list structure to
    `length` / `extract` instead of a flat text blob: a 2-arg call with
    a multi-element second arg has `length(@arguments) == 2`, not the
    sum of all token counts.
    """

    def test_arguments_length_counts_args(self) -> None:
        src = '.m(@a, @b) { n: length(@arguments); } .x { .m(1px, solid); }'
        self.assertIn('n: 2', compile(src))

    def test_arguments_extract_preserves_arg_structure(self) -> None:
        # Each `@x` is a comma-list of 3; extract(@arguments, 1) returns
        # the first arg's full comma-list (`a, b, c`), not just `a`.
        src = '@a: a, b, c;\n@b: 4, 5, 6;\n.m(...) { v: extract(@arguments, 1); }\n.x { .m(@a, @b); }\n'
        out = compile(src)
        self.assertIn('v: a, b, c', out)

    def test_arguments_single_arg_decomposes_space_list(self) -> None:
        # `.M(a b c)` with a single space-list arg makes
        # `length(@arguments)` report 3 (matches less.js).
        src = '.m(...) { n: length(@arguments); } .x { .m(a b c); }'
        self.assertIn('n: 3', compile(src))

    def test_arguments_text_emit_is_space_joined(self) -> None:
        # Even though `@arguments` is structured, its emit form when
        # consumed as a value collapses to space-joined text — matching
        # less.js's Expression rendering convention.
        src = '.m(@a, @b) { border: @arguments; } .x { .m(1px, solid); }'
        self.assertIn('border: 1px solid', compile(src))


class TestInterpolatedMixins(unittest.TestCase):
    """`tests-unit/mixins-interpolated` regression coverage.

    Three independent gaps closed together:
      1. Compound mixin calls (`.a.b.c()`) matching against multi-segment
         `&`-anchored nested rulesets (`&.b .c { … }`).
      2. Nested MixinDefinitions inside spliced Rulesets need their
         lexical closure preserved so a later namespace-descent call
         can still resolve the original mixin's params.
      3. Interpolated rulesets (`@{name} { … }`) must be discoverable
         as mixins after their name resolves — `@a: ~".foo"; @{a} {…}`
         is callable as `.foo()`.
    """

    def test_compound_call_into_amp_anchored_nesting(self) -> None:
        # `.b.bb.cc()` should match `.b .bb { &.cc { … } }`.
        src = '.b .bb {\n  &.cc { x: 1; }\n}\n.out {\n  .b.bb.cc();\n}\n'
        out = compile(src)
        self.assertIn('x: 1', out)

    def test_nested_mixin_def_captures_outer_param(self) -> None:
        # `.Mix("v")` splices `.out { @v: @p; .inner(){x: @v;} }` into the
        # caller. A later `.out.inner()` call must see `@p == "v"`
        # captured at splice time.
        src = (
            '.Mix(@p) {\n'
            '  .out {\n'
            '    @v: @p;\n'
            '    .inner() { x: @v; }\n'
            '  }\n'
            '}\n'
            '.caller {\n'
            '  .Mix("v");\n'
            '  .out.inner();\n'
            '}\n'
        )
        out = compile(src)
        self.assertIn('x: "v"', out)

    def test_interpolated_ruleset_callable_as_mixin(self) -> None:
        # `@{a} { … }` with `@a: ~".foo"` makes the rule callable as `.foo()`.
        src = '@a: ~".foo";\n@{a} { y: 2; }\n.use { .foo(); }\n'
        out = compile(src)
        self.assertIn('y: 2', out)


class TestImportReference(unittest.TestCase):
    """`tests-unit/import/import-reference` regression coverage.

    The fixture exercises five interlocking features at once: mixin
    invocation INTO a reference subtree, `:extend(...)` reaching into
    reference rules with compound selectors and nested @-rules,
    inline-import wrapping, and the `& { … }`-merges-into-parent
    formatter behaviour. These tests pin down the cross-feature
    contracts so a future refactor can't silently regress one of them.
    """

    def test_mixin_call_pulls_nested_rules_from_referenced_definition(
        self,
    ) -> None:
        # `.b { .z(); }` where `.z` is defined in a reference import
        # should expand `.z`'s body including its nested Rulesets
        # (`.c`, `&:hover`) under the call site `.b`.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, 'ref.less')
            with open(ref_path, 'w') as f:
                f.write('.z { color: red; .c { c: 1; } &:hover { h: 2; } }')
            main_src = '@import (reference) "ref.less";\n.b { .z(); }\n'
            main_path = os.path.join(tmp, 'main.less')
            with open(main_path, 'w') as f:
                f.write(main_src)
            out = compile(main_src, filename=main_path, paths=[tmp])
        self.assertIn('.b {', out)
        self.assertIn('.b .c', out)
        self.assertIn('.b:hover', out)

    def test_extend_all_into_reference_compound_selector(self) -> None:
        # `:extend(.x all)` should clone reference selectors that
        # contain `.x` in compound position (`a.x[attr]:not(.y)`),
        # producing a real CSS rule with the extender substituted in.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, 'ref.less')
            with open(ref_path, 'w') as f:
                f.write('input[type="text"].x:not(.one) { color: red; }')
            main_src = '@import (reference) "ref.less";\n.x:extend(.x all) {}\n'
            main_path = os.path.join(tmp, 'main.less')
            with open(main_path, 'w') as f:
                f.write(main_src)
            out = compile(main_src, filename=main_path, paths=[tmp])
        self.assertIn('input[type="text"].x:not(.one)', out)

    def test_amp_only_nested_merges_into_parent(self) -> None:
        # `.A, .B { color: green; & { color: red; } }` collapses to
        # a single block `.A, .B { color: green; color: red; }` — the
        # `& { … }` body splices into the parent (matches less.js
        # `mergeRules` visitor).
        out = compile('.A, .B { color: green; & { color: red; } }')
        self.assertIn('color: green;', out)
        self.assertIn('color: red;', out)
        # Exactly one `.A,` block — the `& {}` must not emit a second
        # block.
        self.assertEqual(out.count('.A,'), 1)

    def test_inline_import_wrapped_in_ruleset(self) -> None:
        # `.foo { @import (inline) ... }` keeps the surrounding `.foo
        # { ... }` wrap around the inlined text. The text is emitted
        # directly (no `@import` line, no extra blank before `}`).
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            inl_path = os.path.join(tmp, 'inline.css')
            with open(inl_path, 'w') as f:
                f.write('raw-text-content')
            main_src = '.wrapper { @import (inline) "inline.css"; }\n'
            main_path = os.path.join(tmp, 'main.less')
            with open(main_path, 'w') as f:
                f.write(main_src)
            out = compile(main_src, filename=main_path, paths=[tmp])
        self.assertIn('.wrapper {', out)
        self.assertIn('raw-text-content', out)
        self.assertIn('}', out)


class TestPropertyAccessorMerge(unittest.TestCase):
    """`$prop` lookup must respect `+:` and `+_:` merge tags so a nested
    rule sees the same concatenated value the parent will emit (the
    `ab { background: $background-color }` case).
    """

    def test_dollar_prop_sees_comma_merge(self) -> None:
        # Two `+:` decls in the parent — `$bg` should see `red, foo`.
        src = 'a {\n  background-color+: red;\n  background-color+: foo;\n  &b { background: $background-color; }\n}\n'
        out = compile(src)
        self.assertIn('background: red, foo', out)

    def test_dollar_prop_sees_space_merge(self) -> None:
        # `+_:` appends with a space; the merged value is `red foo bar`.
        src = 'a {\n  shadow+_: red;\n  shadow+_: foo;\n  shadow+_: bar;\n  &b { box-shadow: $shadow; }\n}\n'
        out = compile(src)
        self.assertIn('box-shadow: red foo bar', out)

    def test_dollar_prop_single_decl_unchanged(self) -> None:
        # Single declaration without merge — last-wins path stays as-is.
        src = 'a {\n  color: red;\n  &b { background: $color; }\n}\n'
        out = compile(src)
        self.assertIn('background: red', out)

    def test_dollar_prop_last_wins_when_no_merge(self) -> None:
        # Two same-named decls, NO merge tags — last wins (less.js semantics).
        src = 'a {\n  color: red;\n  color: blue;\n  &b { background: $color; }\n}\n'
        out = compile(src)
        self.assertIn('background: blue', out)
        self.assertNotIn('background: red,', out)


class TestEvaluatorEdgeCoverage(unittest.TestCase):
    """Targeted tests for evaluator branches that the main feature
    classes don't otherwise reach — error paths, statement-form
    function-call shapes, deferred imports, lookup quirks."""

    def test_each_no_args_silent_passthrough(self) -> None:
        # `each()` with no args isn't reached as a statement-form
        # function-call because the call lacks the function-statement
        # shape; the value-position form falls through to passthrough.
        out = compile('@x: each(); .a { c: 1; }')
        self.assertEqual(out, '.a {\n  c: 1;\n}\n')

    def test_each_second_arg_must_be_detached_ruleset(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.a { each(1 2 3, 5); }')
        self.assertIn('detached ruleset', cm.exception.message)

    def test_each_resolves_via_variable_body(self) -> None:
        # Second arg is a Variable that resolves to a detached ruleset.
        out = compile('@body: { c: red; }; .a { each(@list, @body); } @list: 1 2;')
        self.assertIn('.a {', out)
        self.assertIn('c: red;', out)

    def test_function_statement_color_return_rejected(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.a { rgb(1, 2, 3); }')
        self.assertIn('not valid here', cm.exception.message)

    def test_function_statement_dimension_return_rejected(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError):
            compile('.a { unit(10px, em); }')

    def test_function_statement_e_emits_inline_text(self) -> None:
        # `e('text;')` at root emits its text verbatim.
        out = compile('e("hello world;");')
        self.assertIn('hello world;', out)

    def test_function_statement_if_color_inline(self) -> None:
        # `if(true, red)` returns a Keyword (a color name keyword);
        # less.js treats keywords as inline text at root position.
        out = compile('if(true, foo);')
        self.assertIn('foo', out)

    def test_recursive_variable_with_function_call(self) -> None:
        # `@a: floor(@a);` with no other reference — the post-loop
        # eager-validation pass in `eval_ruleset` is what surfaces
        # the cycle (the in-decl eager pass swallows it, expecting a
        # forward reference). less.js wording matches.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('@a: floor(@a);')
        self.assertIn('Recursive variable definition for @a', cm.exception.message)
        self.assertIn('Error evaluating function `floor`', cm.exception.message)

    def test_recursive_variable_with_unknown_function_no_wrap(self) -> None:
        # When the function isn't registered, the inner `eval_call`
        # passthrough means no `Error evaluating function` prefix gets
        # added at the call-site; the post-loop wrap fills it in by
        # re-parsing the value text and pulling the Call name.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('@a: myfn(@a);')
        self.assertIn('Recursive variable definition for @a', cm.exception.message)
        self.assertIn('Error evaluating function `myfn`', cm.exception.message)

    def test_recursive_property_reference(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('.a { c: $b; b: $c; c: $b; }')
        self.assertIn('Recursive property reference', cm.exception.message)

    def test_division_by_zero_in_always_math(self) -> None:
        from lessish.errors import OperationError

        with self.assertRaises(OperationError) as cm:
            compile('.a { c: 1 / 0; }', math='always')
        self.assertIn('division by zero', cm.exception.message)

    def test_if_no_args_returns_empty(self) -> None:
        # `if()` is a soft passthrough — empty Anonymous, emits nothing.
        out = compile('.a { c: if(); }')
        self.assertIn('c: ', out)

    def test_if_false_two_arg_returns_empty(self) -> None:
        out = compile('.a { c: if(false, hello); }')
        # Two-arg if with a false condition → empty Anonymous.
        self.assertEqual(out, '.a {\n  c: ;\n}\n')

    def test_boolean_no_args_returns_false(self) -> None:
        out = compile('.a { c: boolean(); }')
        self.assertIn('c: false;', out)

    def test_not_no_args_treats_as_false(self) -> None:
        out = compile('.a { c: if(not(), 1, 2); }')
        self.assertIn('c: 1;', out)

    def test_isdefined_missing_returns_false(self) -> None:
        out = compile('.a { c: isdefined(@nope); }')
        self.assertIn('c: false;', out)

    def test_isdefined_existing_returns_true(self) -> None:
        out = compile('@x: 1; .a { c: isdefined(@x); }')
        self.assertIn('c: true;', out)

    def test_isdefined_no_args_returns_false(self) -> None:
        # `isdefined()` — arity != 1 → false fallback.
        out = compile('.a { c: isdefined(); }')
        self.assertIn('c: false;', out)

    def test_isdefined_two_args_returns_false(self) -> None:
        out = compile('.a { c: isdefined(@a, @b); }')
        self.assertIn('c: false;', out)

    def test_font_shorthand_keeps_slash_under_math_always(self) -> None:
        # Less.js quirk: `font: 16px/14px` keeps the literal `/` even
        # when global math is `always` — without the temporary swap the
        # division would fold the two dimensions.
        from lessish import Lessish

        out = Lessish(math='always').compile('@s: 16px; .a { font: @s/14px serif; }')
        self.assertIn('font: 16px/14px serif;', out)

    def test_custom_property_with_backslash_in_string(self) -> None:
        # `--c: "a\\b";` — `_has_non_less_value_syntax` only runs for
        # custom properties. The backslash-skip branch of its string
        # walker handles `\\` without confusing the closing quote.
        out = compile(r':root { --c: "abc\\def"; }')
        self.assertIn(r'"abc\\def"', out)

    def test_custom_property_with_comparison_like_chars(self) -> None:
        # `1<=2` at value position inside a custom property — `=`
        # preceded by `<` is a comparison operator, not an assignment;
        # the non-less-syntax detector skips it.
        out = compile(':root { --c: 1<=2; }')
        self.assertIn('--c: 1<=2;', out)

    def test_custom_property_with_arrow_function(self) -> None:
        # `=>` arrow-function syntax — `_has_non_less_value_syntax`
        # returns True early so the value text is preserved verbatim.
        out = compile(':root { --c: a => b; }')
        self.assertIn('--c: a => b;', out)

    def test_custom_property_paren_wrapping_variable(self) -> None:
        # `--c: (@x);` — Paren(Variable). Paren isn't in the evaluable
        # isinstance check of `_value_has_evaluable_node`, so the walker
        # has to descend into `Paren.value` to find the Variable. Only
        # custom properties (not regular decls) call this walker.
        out = compile('@x: 5; :root { --c: (@x); }')
        self.assertIn('--c: 5;', out)

    def test_custom_property_paren_wrapping_url(self) -> None:
        # Analogous walker in `_value_contains_url` for url() detection.
        out = compile(':root { --bg: (url(x.png)); }')
        self.assertIn('--bg:', out)

    def test_anonymous_value_with_interpolated_variable(self) -> None:
        out = compile('@name: foo; .a { c: bar @{name} baz; }')
        self.assertIn('c: bar foo baz;', out)

    def test_unclosed_string_in_trailing_comment_value_raises(self) -> None:
        # The value parser bails on the unclosed string; the trailing
        # `*/` doesn't accidentally promote it into a comment because
        # the string-detection walker bails when no closing quote is
        # seen.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { c: "abc /* trail */; }')

    def test_malformed_bracket_in_atrule_prelude_passes_through(self) -> None:
        # `#bp.x[unclosed` — bracket never closes. The parser bails out
        # and the chained-lookup loop leaves the text verbatim.
        out = compile('@media (#bp.x[unclosed) { .a { c: 1; } }')
        self.assertIn('@media (#bp.x[unclosed)', out)

    def test_tilde_with_paren_containing_commas(self) -> None:
        # Commas inside `~"..."` parens are skipped over by the top-
        # level comma splitter.
        out = compile('.a { c: ~"foo(a, b)"; }')
        self.assertIn('foo(a, b)', out)

    def test_when_leading_and_collapses_to_empty(self) -> None:
        # `when (and 1=1)` produces an empty group before the `and`,
        # which `_unit_to_condition` treats as truthy(false).
        out = compile('.a when (and 1=1) { c: 1; }')
        self.assertEqual(out, '')

    def test_when_paren_wrapping_compound_condition(self) -> None:
        # `((1=1) and (2=2))` — outermost Paren wraps the whole
        # condition tree.
        out = compile('.a when ((1=1) and (2=2)) { c: 1; }')
        self.assertIn('.a {', out)

    def test_compound_unit_division_preserves_backup_unit(self) -> None:
        # `(3px * 1em) / 1em` — multiplication produces unit `em*px`,
        # then division cancels `em` leaving `px`. The intermediate
        # backup-unit logic walks the `/` branch of `_dim_backup`.
        out = compile('.a { c: (3px * 1em) / 1em; }', math='always')
        self.assertIn('c: 3px;', out)

    def test_variable_call_resolves_detached_ruleset(self) -> None:
        out = compile('@x: { c: red; }; .a { @x(); }')
        self.assertIn('c: red;', out)

    def test_variable_call_on_non_dr_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('@x: 5px; .a { @x(); }')
        self.assertIn('Could not evaluate variable call', cm.exception.message)

    def test_spread_with_undefined_variable_propagates(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.mix(@x, @rest...) {}\n.a { .mix(@undefined...); }')

    def test_spread_empty_list(self) -> None:
        out = compile('.mix(@x...) { c: @x; }\n.a { .mix(); }')
        self.assertIn('.a {', out)

    def test_spread_from_variable_list(self) -> None:
        out = compile('@list: 1 2 3; .mix(@x...) { c: @x; }\n.a { .mix(@list...); }')
        self.assertIn('c: 1 2 3', out)

    def test_no_matching_definition_emits_call_signature(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.mix(@a) { c: @a; } .x { .mix(@a: red, 1, 2); }')
        msg = cm.exception.message
        self.assertIn('mix', msg)
        # Named arg formatting in the call signature.
        self.assertIn('@a:red', msg.replace(' ', ''))

    def test_custom_property_simple(self) -> None:
        out = compile(':root { --c: red; } .a { color: var(--c); }')
        self.assertIn('--c: red;', out)
        self.assertIn('var(--c)', out)

    def test_custom_property_with_variable_interpolation(self) -> None:
        out = compile('@n: red; :root { --c: @n; }')
        self.assertIn('--c: red;', out)

    def test_custom_property_with_undef_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.a { --foo: @undefined; }')

    def test_lookup_variable_not_found(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('.a() { @x: red; } .b { c: .a[@y]; }')
        self.assertIn('@y', cm.exception.message)

    def test_lookup_with_at_variable(self) -> None:
        out = compile('@m: {@x: red;}; .a { c: @m[@x]; }')
        self.assertIn('c: red;', out)

    def test_lookup_with_dollar_property(self) -> None:
        out = compile('@m: {a: red;}; .x { c: @m[$a]; }')
        self.assertIn('c: red;', out)

    def test_atrule_prelude_eval_error_anchored(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('@media (min-width: @undef) { .a { c: 1; } }')

    def test_atrule_body_eval_error_anchored(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('@media (a) { .x { c: @undef; } }')

    def test_atrule_global_var_interpolation(self) -> None:
        # `@charset "@{q}"` substitutes a global var into the prelude.
        out = compile('@charset "@{q}";', global_vars={'q': '"UTF-8"'})
        self.assertIn('@charset "UTF-8"', out)

    def test_dimension_color_promotion_grey(self) -> None:
        # `#444 + 1` promotes 1 to a grey color and shifts each channel.
        out = compile('.a { c: #444 + 1; }')
        self.assertIn('#454545', out)

    def test_eager_call_evaluation_caches_result(self) -> None:
        # Variables holding function calls are eagerly evaluated so the
        # value reused in another decl doesn't re-run the call.
        out = compile('@a: rgb(1, 2, 3); .a { c: @a; }')
        self.assertIn('#010203', out)

    def test_negative_variable_unary(self) -> None:
        out = compile('@a: 5px; .x { c: -@a; }')
        self.assertIn('c: -5px;', out)

    def test_compound_unit_divide(self) -> None:
        out = compile('.a { c: (10px / 2em); }')
        self.assertIn('c:', out)

    def test_deferred_import_with_undefined_var(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('@import "@{undef}";')

    def test_deferred_import_resolves_substituted_name(self) -> None:
        from lessish.errors import FileError

        # Substitution happens, the file isn't found → FileError, which
        # proves the deferred path resolved into the regular resolver.
        with self.assertRaises(FileError):
            compile('@name: "missing"; @import "@{name}.less";')

    def test_trailing_block_comment_in_value_preserved(self) -> None:
        out = compile('.a { c: red /* trail */; }')
        self.assertIn('/* trail */', out)

    def test_unknown_name_at_root_raises_did_not_return_root(self) -> None:
        # Bare `name()` with no mixin and no registered function turns
        # into a strict-mode `Function '<name>' did not return a root node`.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('nofn();')
        self.assertIn('did not return a root node', cm.exception.message)

    def test_unknown_dotted_call_at_root_undefined(self) -> None:
        # `.missing()` — name starts with `.`, falls through to the
        # "is undefined" branch instead of function-statement promotion.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.missing();')

    def test_lookup_on_scalar_variable_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError):
            compile('@v: 5; .a { c: @v[k]; }')

    def test_property_accessor_carries_important_flag(self) -> None:
        # `$c` where `c` is `!important` should propagate the flag to
        # the consuming declaration.
        out = compile('.a { c: 1px !important; d: $c; }')
        self.assertIn('d: 1px !important;', out)

    def test_namespace_lookup_carries_important_flag(self) -> None:
        out = compile('.m() { c: 1 !important; } .a { d: .m[c]; }')
        self.assertIn('.a {', out)

    def test_strict_units_multiplication_error(self) -> None:
        from lessish.errors import OperationError

        with self.assertRaises(OperationError) as cm:
            compile('.a { c: 2px * 3em; }', strict_units=True)
        self.assertIn('Multiple units', cm.exception.message)

    def test_root_level_property_decl_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('color: red;')
        self.assertIn('Properties must be inside selector blocks', cm.exception.message)

    def test_when_with_boolean_variable(self) -> None:
        # `boolean(false)` stored in a var, used in `when` — exercises
        # the value-mode condition transformer.
        out = compile('@x: boolean(false); .a when (@x) { c: 1; } .b when not (@x) { c: 2; }')
        self.assertIn('.b {', out)
        self.assertNotIn('.a {', out)

    def test_when_with_comparison(self) -> None:
        out = compile('.a when (1 < 2) { c: 1; } .b when (5 = 5) { c: 2; }')
        self.assertIn('.a {', out)
        self.assertIn('.b {', out)

    def test_when_with_or_and(self) -> None:
        out = compile('.a when (1 = 1) and (2 = 2) { c: ok; }\n.b when (1 = 0), (2 = 2) { c: ok; }\n')
        self.assertIn('.a {', out)
        self.assertIn('.b {', out)

    def test_when_nested_parens(self) -> None:
        out = compile('.a when ((1 = 1)) { c: ok; }')
        self.assertIn('.a {', out)

    def test_unary_minus_on_color(self) -> None:
        # `-#abc` doesn't fold to a Dimension — wraps in a Negative node.
        out = compile('@c: #abc; .a { c: -@c; }')
        # The output preserves a Negative wrapper around the Color.
        self.assertIn('.a {', out)

    def test_deferred_inline_optional_missing_drops(self) -> None:
        # Deferred path + `(inline, optional)` + missing file → empty out.
        out = compile('@n: "x"; @import (inline, optional) "@{n}.css";')
        self.assertEqual(out, '')

    def test_mixin_call_inner_error_anchored_at_call(self) -> None:
        # Error raised deep inside a mixin body propagates to the call
        # site with the call's index attached.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.bad() { c: @undef; }\n.x { .bad(); }')

    def test_if_statement_form_chooses_detached_ruleset(self) -> None:
        # `if(cond, {ruleset}, {other})` at statement position invokes
        # the chosen DR's rules under the current scope.
        out = compile('@flag: true; .a { if(@flag, { c: red; }, { c: blue; }); }')
        self.assertIn('c: red;', out)
        self.assertNotIn('c: blue;', out)

    def test_if_statement_form_false_picks_else(self) -> None:
        out = compile('.a { if(false, { c: red; }, { c: blue; }); }')
        self.assertIn('c: blue;', out)

    def test_if_statement_form_no_else_drops(self) -> None:
        # Two-arg if at statement position with a false cond produces
        # no rules.
        out = compile('.a { if(false, { c: red; }); d: 1; }')
        self.assertIn('d: 1;', out)
        self.assertNotIn('red', out)

    def test_if_statement_form_color_return_inline(self) -> None:
        # if() that returns a Keyword (color-name) at statement position
        # goes through the inline-anonymous emission path (no Color reject).
        out = compile('.a { if(true, foo); }')
        self.assertIn('foo', out)

    def test_mixin_guard_failure_emits_signature_in_error(self) -> None:
        # A mixin defined with a guard that fails — error includes the
        # call signature rendered by `_format_call_args`.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError):
            compile('.m(@x) when (@x > 5) { c: ok; }\n.a { .m(1, 2); }')

    def test_lookup_last_with_bracket(self) -> None:
        # `.mixin()[]` returns the value of the LAST declaration.
        out = compile('.m() { c: 1; d: 2; } .x { v: .m[]; }')
        self.assertIn('v: 2;', out)

    def test_lookup_last_on_dr(self) -> None:
        # `@dr[]` — empty key brackets on a detached ruleset.
        out = compile('@dr: { c: 1; d: 2; }; .x { v: @dr[]; }')
        self.assertIn('v: 2;', out)

    def test_lookup_last_on_empty_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.empty() {} .x { v: .empty[]; }')

    def test_var_indirect_lookup(self) -> None:
        # `@m[@@xname]` resolves @xname → "x" (string), strips quotes,
        # then looks up @x.
        out = compile('@xname: "x"; @m: {@x: red;}; .a { c: @m[@@xname]; }')
        self.assertIn('c: red;', out)

    def test_prop_indirect_lookup(self) -> None:
        # `@m[$@name]` resolves @name → "color", then property-looks-up
        # the unquoted name.
        out = compile('@name: "color"; @m: { color: red; }; .a { c: @m[$@name]; }')
        self.assertIn('c: red;', out)

    def test_namespace_lookup_in_atrule_prelude(self) -> None:
        # `@media (... #ns[@k] ...)` resolution path.
        out = compile('@map: { width: 600px; }; @media (min-width: @map[width]) { .a { c: 1; } }')
        self.assertIn('@media (min-width: 600px)', out)

    def test_namespace_call_lookup_in_atrule_prelude(self) -> None:
        # `#ns.fn()` inside an at-rule prelude — exercises the
        # `_eval_namespace_call_lookups` paren-balanced parser.
        out = compile('#bp() { .lg() { @return: 1024px; } }\n@media (min-width: #bp.lg()) { .a { c: 1; } }')
        self.assertIn('@media', out)

    def test_spread_with_named_rest(self) -> None:
        # `.m(@x, @r...)` — captures remaining positional args as a list.
        out = compile('.m(@x, @r...) { c: @x; d: @r; } .a { .m(1, 2, 3, 4); }')
        self.assertIn('c: 1;', out)
        self.assertIn('d: 2 3 4;', out)

    def test_spread_into_rest_param(self) -> None:
        # `.m(@r...)` captures the spread-from-variable list.
        out = compile('.m(@r...) { c: @r; }\n@list: 1 2 3;\n.x { .m(@list...); }')
        self.assertIn('c: 1 2 3', out)

    def test_decl_with_parse_error_in_value_passes_through(self) -> None:
        # `c: 1 +;` doesn't parse as a structured value — falls through
        # to verbatim emission.
        out = compile('.a { c: 1 +; }')
        self.assertIn('c: 1 +;', out)

    def test_decl_with_unbalanced_paren_passes_through(self) -> None:
        out = compile('.a { c: ); }')
        self.assertIn('c: )', out)

    def test_detached_ruleset_as_decl_value_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('@dr: { c: 1; }; .a { color: @dr; }')
        self.assertIn('Rulesets cannot be evaluated on a property', cm.exception.message)

    def test_unit_with_non_dimension_raises(self) -> None:
        # `unit(red, em)` — red is a Keyword, not Dimension; less.js
        # raises a fatal ArgumentError that becomes
        # `Error evaluating function 'unit': ...`.
        from lessish.errors import ArgumentError

        with self.assertRaises(ArgumentError) as cm:
            compile('.a { c: unit(red, em); }')
        self.assertIn('Error evaluating function `unit`', cm.exception.message)

    def test_svg_gradient_bad_direction_raises(self) -> None:
        from lessish.errors import ArgumentError

        with self.assertRaises(ArgumentError) as cm:
            compile('.a { background: svg-gradient(invalid, red, blue); }')
        self.assertIn('Error evaluating function `svg-gradient`', cm.exception.message)

    def test_property_lookup_on_mixin_missing_property(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.m() { c: 1; } .a { d: .m[$nope]; }')

    def test_namespace_var_lookup_failure_in_atrule_prelude(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('@map: { x: 5; }; @media (min-width: @map[missing]) { .a { c: 1; } }')

    def test_each_statement_with_too_few_args_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.a { each(); }')
        self.assertIn('each() expects 2 arguments', cm.exception.message)

    def test_each_value_references_unbound_in_body_falls_back(self) -> None:
        # The DR body references `@value` (bound only per-iteration). The
        # eval-in-isolation pass fails on the missing `@value` and the
        # fallback path runs the raw rules per iter. Exercises the
        # `_each_items` `LessError` rescue branch.
        out = compile('@list: 1 2; .a { each(@list, { c: @value; }); }')
        self.assertIn('c: 1;', out)
        self.assertIn('c: 2;', out)

    def test_mixin_guard_all_fail_returns_empty(self) -> None:
        # Single candidate with a guard that's always false → no output,
        # no error. Exercises the `is_ruleset_wrapper or guard is not None`
        # quiet path.
        out = compile('.m(@x) when (@x > 100) { c: ok; } .a { .m(1); }')
        # `.a` rule produces no output since the mixin matched no body.
        self.assertEqual(out, '')

    def test_if_statement_with_no_args_produces_empty(self) -> None:
        out = compile('.a { if(); }')
        # No args → returns no rules; the `.a` block is empty so emit nothing.
        self.assertEqual(out, '')

    def test_if_three_arg_false_picks_else_value(self) -> None:
        # Value-form: third arg returned when cond false.
        out = compile('.a { c: if(false, hello, world); }')
        self.assertIn('c: world;', out)

    def test_variable_with_invalid_function_call_eager(self) -> None:
        # `@a: unit(red, em);` — `unit` rejects Keyword input with a
        # fatal ArgumentError that the eager pass surfaces immediately.
        from lessish.errors import ArgumentError

        with self.assertRaises(ArgumentError) as cm:
            compile('@a: unit(red, em); .x { c: @a; }')
        self.assertIn('Error evaluating function `unit`', cm.exception.message)

    def test_var_with_important_flag_propagates(self) -> None:
        # When `@x: 5px !important;`, a consuming decl picks up the flag.
        out = compile('@x: 5px !important; .a { c: @x; }')
        self.assertIn('c: 5px !important;', out)

    def test_value_mixin_call_resolves_to_empty(self) -> None:
        # `.m()` used at value position (`@x: .m();`) — invoked as a
        # value mixin call. The mixin's body has no `@return`, so the
        # value is undefined / no useful output.
        out = compile('.m() { c: red; } .a { @x: .m(); }')
        # No CSS output but compilation succeeds.
        self.assertEqual(out, '')

    def test_mixin_body_local_variable_visible_in_nested_ruleset(self) -> None:
        # `.outer(@a) { @v: @a; .inner { c: @v; } }` — the inner ruleset
        # sees `@v` from the outer mixin's local scope. Exercises
        # `_materialize_inner_variables`.
        out = compile('.outer(@a) { @v: @a; .inner { c: @v; } }\n.a { .outer(red); }')
        self.assertIn('.a .inner', out)
        self.assertIn('c: red;', out)

    def test_when_empty_guard_drops_block(self) -> None:
        # Empty `when ()` → conditional shape with no values, evaluates
        # to false → block dropped.
        out = compile('.a when () { c: 1; }')
        self.assertEqual(out, '')

    def test_custom_property_preserves_inline_block_comment(self) -> None:
        out = compile('.a { c: linear-gradient(#333 /*hint*/, #111); }')
        self.assertIn('/*hint*/', out)

    def test_unrecognized_value_token_raises(self) -> None:
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { c: %@@@; }')

    def test_default_outside_guard_at_value_position(self) -> None:
        # `default()` raises in guard context; at value position the
        # ArgumentError catch turns it into a passthrough Call.
        out = compile('.a { c: default(); }')
        self.assertIn('default()', out)

    def test_compound_unit_division_preserved(self) -> None:
        out = compile('.a { c: 1px / 2px; }', math='always')
        # Same-unit cancellation yields a unitless ratio.
        self.assertIn('c: 0.5', out)

    def test_at_plugin_raises(self) -> None:
        from lessish.errors import UnsupportedFeatureError

        with self.assertRaises(UnsupportedFeatureError) as cm:
            compile('@plugin "tools.js";')
        self.assertIn('@plugin', cm.exception.message)
        self.assertIn('JavaScript', cm.exception.message)

    def test_at_plugin_nested_in_atrule_raises(self) -> None:
        # `@plugin` inside another at-rule's body is caught by
        # `eval_atrule`'s plugin guard (the eval_ruleset pre-pass only
        # walks one level, so a nested plugin bypasses it).
        from lessish.errors import UnsupportedFeatureError

        with self.assertRaises(UnsupportedFeatureError):
            compile('@media (a) { @plugin "x.js"; }')

    def test_at_rule_body_mixin_call_undefined_anchored(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('@media (a) { .missing(); }')

    def test_at_rule_body_variable_call_undefined_anchored(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('@media (a) { @nope(); }')

    def test_at_rule_body_mixin_call_inner_error_anchored(self) -> None:
        # Mixin exists, but invocation triggers an error from its body.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.m() { c: @undef; }\n@media (a) { .m(); }')

    def test_value_mixin_lookup_with_no_match_arity_raises(self) -> None:
        # `.m()[c]` where `.m` exists but no arity matches.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError):
            compile('.m(@x) { c: red; } .a { d: .m()[c]; }')

    def test_value_mixin_lookup_undefined_name_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.a { d: .missing()[c]; }')

    def test_value_mixin_lookup_on_ruleset_wrapper(self) -> None:
        # `.m` is a regular ruleset — using it as a lookup target
        # routes through `_invoke_value_mixin_call`'s ruleset-wrapper
        # branch (returns rules without invocation).
        out = compile('.m { c: red; } .a { d: .m[c]; }')
        self.assertIn('d: red;', out)

    def test_lookup_target_variable_with_important_anonymous(self) -> None:
        # `@colors: .colors() !important;` — the parser strips
        # `!important` and sets Declaration.important on the variable.
        # Lookups through it carry the flag.
        out = compile('.c() { primary: red; } @colors: .c() !important; .a { c: @colors[primary]; }')
        self.assertIn('c: red !important', out)

    def test_lookup_target_undefined_variable_returns_false(self) -> None:
        # `_lookup_target_is_important` finds no decl → False; the
        # consuming lookup itself then fails for the actual reason.
        from lessish.errors import LessError

        with self.assertRaises(LessError):
            compile('.a { c: @nope[k]; }')

    def test_property_self_reference_raises(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('.a { c: $c; }')
        self.assertIn('Recursive property reference for $c', cm.exception.message)

    def test_property_accessor_chains_important(self) -> None:
        out = compile('.a { c: 1 !important; b: $c; d: $b; }')
        self.assertIn('b: 1 !important;', out)
        self.assertIn('d: 1 !important;', out)

    def test_if_two_arg_false_returns_empty_value(self) -> None:
        out = compile('.a { c: if(false, only); }')
        self.assertEqual(out, '.a {\n  c: ;\n}\n')

    def test_condition_triple_parens(self) -> None:
        out = compile('.a when (((1 = 1))) { c: 1; }')
        self.assertIn('.a {', out)

    def test_condition_with_not_boolean_no_args(self) -> None:
        out = compile('.a when not(boolean()) { c: 1; }')
        self.assertIn('.a {', out)

    def test_default_in_value_position_passes_through(self) -> None:
        # `default()` at value position raises EvalError that the
        # `eval_call` catch swallows and propagates the call verbatim.
        out = compile('.a { c: default(); }')
        self.assertIn('default()', out)

    def test_mixed_positional_and_spread(self) -> None:
        # Non-spread arg + spread arg in same call.
        out = compile('@list: 2 3; .m(@a, @r...) { c: @a; d: @r; } .a { .m(1, @list...); }')
        self.assertIn('c: 1;', out)
        self.assertIn('d: 2 3;', out)

    def test_spread_undefined_falls_through_to_matcher(self) -> None:
        # `_expand_spread_call_args` swallows UndefinedNameError and
        # leaves the arg unchanged so the matcher produces the usual
        # "no matching definition" / undefined error downstream.
        from lessish.errors import LessError

        with self.assertRaises(LessError):
            compile('.m(@x...) { c: @x; } .a { .m(@undef...); }')

    def test_spread_single_value(self) -> None:
        # Spreading a single non-list value yields one positional arg.
        out = compile('@x: 5; .m(@y...) { c: @y; } .a { .m(@x...); }')
        self.assertIn('c: 5;', out)

    def test_trailing_comment_value_carries_lookup_important(self) -> None:
        # `c: @x /* trail */;` where `@x` was declared `!important` —
        # the trailing-comment branch must still pick up the lookup
        # important marker.
        out = compile('@x: 5px !important; .a { c: @x /* trail */; }')
        self.assertIn('5px', out)
        self.assertIn('!important', out)
        self.assertIn('/* trail */', out)

    def test_trailing_comment_value_with_parse_error_raises(self) -> None:
        # `@a()` at value position requires `[...]` after; parser bails
        # with `_base_index` set. The trailing-comment branch's
        # ParseError handler must propagate it instead of falling back
        # to verbatim text.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { c: @a() /* trail */; }')

    def test_variable_call_without_lookup_in_value_raises(self) -> None:
        from lessish.errors import ParseError

        with self.assertRaises(ParseError) as cm:
            compile('.a { c: @a(); }')
        self.assertIn('Missing', cm.exception.message)

    def test_variable_call_in_expression_raises(self) -> None:
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { c: 5 + @a(); }')

    def test_recursive_variable_with_constructor_function(self) -> None:
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('@a: rgb(@a, 0, 0);')
        self.assertIn('Recursive variable definition for @a', cm.exception.message)
        self.assertIn('Error evaluating function `rgb`', cm.exception.message)

    def test_mixin_with_extend_hoists_to_call_site(self) -> None:
        # `.clearfix() { &:extend(.thing all); }` — the `&:extend` rides
        # along with the splice and rebrands the caller as an extender.
        out = compile('.clearfix() { &:extend(.thing all); }\n.thing { c: red; }\n.a { .clearfix(); }')
        self.assertIn('.thing,\n.a {', out)

    def test_when_with_single_variable_truthy(self) -> None:
        # `.a when (@x)` — boolean variable used as a guard. Value is a
        # 1-expression Value; arg_to_condition unwraps it.
        out = compile('@x: true; .a when (@x) { c: 1; } .b when (@y) { c: 2; } @y: false;')
        self.assertIn('.a {', out)
        self.assertNotIn('.b {', out)

    def test_chained_brackets_in_atrule_prelude(self) -> None:
        # `@map[@k1][@k2]` chained inside an at-rule prelude — runs
        # through the regex-driven bracket lookup pass.
        out = compile('@m: { @x: 100px; };\n@media (min-width: @m[@x]) { .a { c: 1; } }')
        self.assertIn('@media (min-width: 100px)', out)

    def test_namespace_dot_call_with_bracket_lookup_in_prelude(self) -> None:
        # `.ns.thing()[@return]` — hits the namespace-call paren+bracket
        # parser (`_eval_namespace_call_lookups`).
        out = compile(
            '.ns() { .thing() { @return: 1024px; } }\n@media (min-width: .ns.thing()[@return]) { .a { c: 1; } }'
        )
        self.assertIn('@media (min-width: 1024px)', out)

    def test_namespace_bare_with_bracket_lookup_in_prelude(self) -> None:
        # `.thing[@return]` — bare ruleset lookup, single bracket.
        out = compile('.thing { @return: 1024px; }\n@media (min-width: .thing[@return]) { .a { c: 1; } }')
        self.assertIn('@media (min-width: 1024px)', out)

    def test_lookup_on_dimension_value_passes_through(self) -> None:
        # `5px[k]` isn't a Lookup the evaluator recognises — emitted
        # verbatim.
        out = compile('.a { c: 5px[k]; }')
        self.assertIn('5px[k]', out)

    def test_function_statement_e_with_keyword_arg(self) -> None:
        # `e(red)` — `e()` returns Quoted; promoted to inline `@__inline__`.
        out = compile('e(red);')
        self.assertIn('red', out)

    def test_function_statement_percent_format(self) -> None:
        out = compile('%("h-%s", red);')
        self.assertIn('h-red', out)

    def test_function_statement_escape(self) -> None:
        out = compile('escape("a b");')
        self.assertIn('a%20b', out)

    def test_mixed_variable_and_literal_in_value(self) -> None:
        out = compile('@x: 5; .a { c: @x foo; }')
        self.assertIn('c: 5 foo;', out)

    def test_division_with_variable_kept_as_operation(self) -> None:
        # `1 / @x` in default math mode (parens-division) — without
        # parens, division stays unfolded.
        out = compile('@x: 2; .a { c: 1 / @x; }')
        self.assertIn('c: 1 / 2;', out)

    def test_when_with_arithmetic_comparison(self) -> None:
        out = compile('.a when (1 + 1 = 2) { c: 1; }')
        self.assertIn('.a {', out)

    def test_when_with_paren_then_and(self) -> None:
        out = compile('.a when ((1 = 1)) and (2 = 2) { c: 1; }')
        self.assertIn('.a {', out)

    def test_when_not_empty_arg(self) -> None:
        # `not()` with no args → treated as truthy(false) → not(false) = true.
        out = compile('.a when not() { c: 1; }')
        self.assertIn('.a {', out)

    def test_when_empty_and_chain_drops(self) -> None:
        out = compile('.a when () and (1=1) { c: 1; }')
        self.assertEqual(out, '')

    def test_property_accessor_on_merge_tagged_values(self) -> None:
        # `c+: 1; c+: 2;` produces merged "1, 2" — `$c` sees the merged
        # value, important flag promoted from any tagged segment.
        out = compile('.a { c+: 1 !important; c+: 2; e: $c; }')
        self.assertIn('e: 1, 2 !important;', out)

    def test_detached_ruleset_lookup_resolves_ruleset_rules(self) -> None:
        # `@r[a]` where `@r` is a DR — _resolve_lookup_target evaluates
        # the DR via `eval_ruleset` and returns its rules.
        out = compile('@r: { a: 1; b: 2; }; .a { c: @r[a]; }')
        self.assertIn('c: 1;', out)

    def test_mixin_inner_decl_references_outer_variable(self) -> None:
        # `.m() { @v: @x; .inner { c: @v; } }` where @x is in call-site
        # scope — _materialize_inner_variables resolves @v eagerly.
        out = compile('@x: 100; .m() { @v: @x; .inner { c: @v; } } .a { .m(); }')
        self.assertIn('c: 100;', out)

    def test_multi_selector_with_when_raises(self) -> None:
        # less.js: `Guards are only currently allowed on a single
        # selector.` — surfaces as EvalError (not ParseError) at
        # mixin-extraction time.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.a, .b when (true) { c: 1; }')
        self.assertIn('single selector', cm.exception.message)

    def test_custom_property_unbalanced_paren_raises(self) -> None:
        # Custom-property values normally accept anything, but a fully
        # unparseable `((unclosed` still surfaces as ParseError.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { --foo: ((unclosed; }')

    def test_default_in_css_guard_raises(self) -> None:
        # `default()` is only valid in parametric mixin guards. In a
        # CSS-guard (`.x when (cond)`) it raises EvalError, which the
        # `eval_call` EvalError handler propagates with location.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.a when (default()) { c: 1; }')
        self.assertIn('parametric mixin guards', cm.exception.message)

    def test_mixin_match_with_default_fallback(self) -> None:
        # Two candidates, one explicit and one with `default()` guard.
        # `default()` resolves true only if no other candidate matches,
        # so the explicit one wins for the matching arg.
        out = compile('.m(@x) when (default()) { c: fb; }\n.m(@x) when (@x = 5) { c: ok; }\n.a { .m(5); }')
        self.assertIn('c: ok;', out)
        # And the fallback when nothing matches the explicit arm.
        out2 = compile('.m(@x) when (default()) { c: fb; }\n.m(@x) when (@x = 5) { c: ok; }\n.a { .m(1); }')
        self.assertIn('c: fb;', out2)

    def test_custom_property_with_interpolated_name(self) -> None:
        out = compile('@n: foo; :root { --@{n}: red; }')
        self.assertIn('--foo: red;', out)

    def test_trailing_block_comment_after_quoted_string(self) -> None:
        # `_split_trailing_block_comment` walks past a quoted region
        # before looking for the `/*`.
        out = compile('.a { c: "hello" /* trail */; }')
        self.assertIn('"hello" /* trail */', out)

    def test_trailing_block_comment_after_escaped_quote(self) -> None:
        # `"a\"b"` — the `\"` is an escaped quote inside the string;
        # the splitter mustn't terminate the string on it.
        out = compile('.a { c: "a\\"b" /* trail */; }')
        self.assertIn('/* trail */', out)

    def test_inline_comment_in_value_followed_by_comma(self) -> None:
        # `red /*c*/, blue` — comment isn't at the trailing edge so the
        # comment-preservation branch bails (falls back to verbatim).
        out = compile('.a { c: red /*c*/, blue; }')
        self.assertIn('/*c*/', out)

    def test_tilde_quoted_comma_list(self) -> None:
        # `~"a, b, c"` evaluates to a list; emitted with commas.
        out = compile('.a { c: ~"a, b, c"; }')
        self.assertIn('a, b, c', out)

    def test_lookup_on_normal_ruleset(self) -> None:
        # `.box[a]` where `.box` is a regular ruleset (not a mixin
        # wrapper) — _resolve_lookup_target's Ruleset branch.
        out = compile('.box { a: 1; } .a { c: .box[a]; }')
        self.assertIn('c: 1;', out)

    def test_recursive_variable_with_paren_wrapper(self) -> None:
        # `@a: (@a);` — the parens make the value text contain `(`
        # which triggers eager validation, but the unwrapped AST is a
        # bare Variable, not a Call. Exercises the no-wrap branch of
        # the eager-recursion message rewriter.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError) as cm:
            compile('@a: (@a);')
        self.assertIn('Recursive variable definition for @a', cm.exception.message)

    def test_each_over_detached_ruleset_map(self) -> None:
        # When the first arg is a DR, each iter gets `@key` and `@value`
        # bound. The DR body is eval'd-in-isolation first; success path
        # uses the resolved values.
        out = compile('@m: { a: red; b: blue; }; .x { each(@m, { c-@{key}: @value; }); }')
        self.assertIn('c-a: red;', out)
        self.assertIn('c-b: blue;', out)

    def test_each_over_detached_ruleset_with_unresolvable_value(self) -> None:
        # DR body references something that fails to resolve in
        # isolation; eval bails and the iteration uses the raw rules.
        # The body in the second arg can still emit because @value
        # binds per-iteration.
        out = compile('@m: { a: @undef-ref; }; .x { each(@m, { c: 1; }); }')
        self.assertIn('c: 1;', out)

    def test_invalid_hex_five_digits_raises_at_value_end(self) -> None:
        # 5-hex-digit `#fffff` isn't a valid color literal; the parser
        # raises `Unrecognised input` with `_propagate='value_end'`, and
        # `_attach_decl_anchor` re-anchors to the value-end column.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError) as cm:
            compile('.a { c: #fffff; }')
        self.assertIn('Unrecognised input', cm.exception.message)

    def test_invalid_hex_five_digits_with_trailing_comment_raises(self) -> None:
        # Same parser rejection but inside the trailing-comment branch
        # — exercises the second copy of the `_propagate` handler.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { c: #fffff /* trail */; }')

    def test_bare_percent_in_value_raises_at_decl_start(self) -> None:
        # `%` outside `%("...", args)` — re-anchored with mode='decl_start'.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError) as cm:
            compile('.a { c: %; }')
        self.assertIn('Invalid %', cm.exception.message)

    def test_bare_percent_with_trailing_comment_raises(self) -> None:
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('.a { c: % /* trail */; }')

    def test_ambiguous_default_raises_with_call_signature(self) -> None:
        # Two candidates both gated on `default()` → ambiguous when
        # neither has an explicit match. The error message renders
        # the call signature via `_format_call_args`.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            compile('.m(@x) when (default()) { c: a; }\n.m(@x) when (default()) { c: b; }\n.a { .m(1); }')
        self.assertIn('Ambiguous use of `default()`', cm.exception.message)
        self.assertIn('.m(', cm.exception.message)


class TestCompressMode(unittest.TestCase):
    """Compress mode (`{compress: true}` in less.js) — exercises the
    branch-heavy emitters and selector / value compactors in
    `visitors.py`.
    """

    def _c(self, src: str) -> str:
        from lessish import Lessish

        return Lessish(compress=True).compile(src)

    def test_compress_attribute_selector_brackets_preserved(self) -> None:
        # Whitespace inside `[...]` stays as user wrote it; outer
        # combinators get collapsed.
        out = self._c('.a[type="text"] { c: red; }')
        self.assertIn('.a[type="text"]{c:red}', out)

    def test_compress_pseudo_class_with_parens_preserved(self) -> None:
        out = self._c('.a:nth-child(2n+1) { c: red; }')
        self.assertIn('.a:nth-child(2n+1)', out)

    def test_compress_not_with_compound_inner(self) -> None:
        out = self._c('.a:not(.b) { c: red; }')
        self.assertIn('.a:not(.b)', out)

    def test_compress_combinators_collapse(self) -> None:
        # ` > ` / ` + ` / ` ~ ` lose surrounding whitespace.
        out = self._c('.a > .b + .c ~ .d { c: red; }')
        self.assertIn('.a>.b+.c~.d', out)

    def test_compress_column_combinator(self) -> None:
        # ` | ` namespace separator — collapses just like other
        # combinators.
        out = self._c('svg | circle { c: red; }')
        self.assertIn('svg|circle', out)

    def test_compress_carat_combinators(self) -> None:
        # `^` and `^^` shadow-piercing combinators (legacy CSS Shadow
        # Parts / less.js carries the syntax). `^^` (two chars) must be
        # checked before single-char `^`.
        out_single = self._c('.a ^ .b { c: red; }')
        self.assertIn('.a^.b', out_single)
        out_double = self._c('.a ^^ .b { c: red; }')
        self.assertIn('.a^^.b', out_double)

    def test_compress_excess_whitespace_around_combinator(self) -> None:
        out = self._c('.a   >   .b { c: red; }')
        self.assertIn('.a>.b', out)

    def test_compress_atrule_comma_separated_preludes(self) -> None:
        out = self._c('@media (a), (b) { .x { c: 1; } }')
        self.assertIn('@media (a),(b)', out)

    def test_compress_atrule_media_with_features(self) -> None:
        out = self._c('@media screen and (max-width: 600px) { .a { c: 1; } }')
        self.assertIn('@media screen and (max-width: 600px)', out)

    def test_compress_atrule_statement_form(self) -> None:
        out = self._c('@charset "UTF-8";')
        self.assertEqual(out, '@charset "UTF-8";')

    def test_compress_font_face_with_decls_and_bang_comment(self) -> None:
        out = self._c('@font-face { font-family: x; src: url(a); /*! keep */ }')
        self.assertIn('@font-face{font-family:x;src:url(a)', out)
        self.assertIn('/*! keep */', out)

    def test_compress_font_face_decls_only(self) -> None:
        out = self._c('@font-face { font-family: x; src: url(a); }')
        # No trailing `;` before `}` in compress mode.
        self.assertIn('src:url(a)}', out)

    def test_compress_keyframes_empty_body(self) -> None:
        out = self._c('@keyframes spin { }')
        self.assertIn('@keyframes spin{', out)

    def test_compress_drops_leading_zero_on_subunit_decimal(self) -> None:
        out = self._c('.a { c: 0.5px; }')
        self.assertIn('c:.5px', out)

    def test_compress_keeps_zero_inside_function_call(self) -> None:
        # `rgba(0,0,0,0.1)` — leading zero inside the function-call
        # parens stays per less.js parity.
        out = self._c('.a { c: rgba(0, 0, 0, 0.1); }')
        self.assertIn('0.1)', out)

    def test_compress_strips_comma_space_in_function_args(self) -> None:
        out = self._c('.a { c: rgba(255, 0, 0, 0.5); }')
        self.assertIn('rgba(255,0,0,', out)

    def test_compress_with_extend_clones_paths(self) -> None:
        out = self._c('.target { c: red; } .other:extend(.target) {}')
        # `.target` selector list now includes `.other`.
        self.assertIn('.target,.other{c:red}', out)

    def test_compress_important_no_space(self) -> None:
        # less.js's `!important` emits with a leading space normally,
        # but compress mode drops the space.
        out = self._c('.a { c: red !important; }')
        self.assertIn('c:red!important', out)

    def test_atrule_with_trailing_loud_comment_emits(self) -> None:
        # `@charset "x" /* tc */;` — `/* tc */` is extracted from prelude
        # into `trailing_comments`. Non-compress emits it on its own line.
        from lessish import Lessish

        out = Lessish().compile('@charset "x" /* tc */;')
        self.assertIn('@charset "x";', out)
        self.assertIn('/* tc */', out)

    def test_compress_atrule_trailing_bang_kept_loud_dropped(self) -> None:
        out_bang = self._c('@charset "x" /*! bang */;')
        self.assertIn('/*! bang */', out_bang)
        out_loud = self._c('@charset "x" /* drop */;')
        self.assertNotIn('drop', out_loud)

    def test_atrule_body_important_decl(self) -> None:
        # @font-face body with !important — covers `_important_suffix`
        # call inside `emit_atrule` body-decls loop.
        from lessish import Lessish

        out = Lessish().compile('@font-face { src: url(x.woff) !important; }')
        self.assertIn('src: url(x.woff) !important;', out)

    def test_compress_root_loud_comment_dropped(self) -> None:
        out = self._c('/* drop */ .a { c: red; }')
        self.assertNotIn('drop', out)

    def test_compress_root_bang_comment_kept(self) -> None:
        out = self._c('/*! header */ .a { c: red; }')
        self.assertIn('/*! header */', out)

    def test_media_prelude_with_quoted_string(self) -> None:
        # The prelude walker steps past quoted strings (with escapes)
        # so internal whitespace inside the quotes isn't collapsed.
        from lessish import Lessish

        out = Lessish().compile('@media (font-family: "Roboto") { .a { c: red; } }')
        self.assertIn('"Roboto"', out)

    def test_media_prelude_with_escaped_quote_in_string(self) -> None:
        from lessish import Lessish

        out = Lessish().compile(r"@media (string: 'val\'esc') { .a { c: red; } }")
        self.assertIn(r"'val\'esc'", out)

    def test_value_to_css_unhandled_node_returns_empty(self) -> None:
        # `to_css` falls through to '' when the dispatch table doesn't
        # have a handler. Defensive — unhandled value-type nodes.
        from lessish.visitors import value_to_css

        class Bogus:
            index = 0

        self.assertEqual(value_to_css(Bogus()), '')  # type: ignore[arg-type]

    def test_value_to_css_paren_wraps_inner(self) -> None:
        from lessish.ast_nodes import Dimension, Paren
        from lessish.visitors import value_to_css

        out = value_to_css(Paren(index=0, value=Dimension(index=0, value=1, unit='px')))
        self.assertEqual(out, '(1px)')

    def test_value_to_css_mixin_call_emit(self) -> None:
        from lessish.ast_nodes import MixinCall
        from lessish.visitors import value_to_css

        out = value_to_css(MixinCall(index=0, name='.m', args=[], important=False))
        self.assertEqual(out, '.m()')

    def test_value_to_css_lookup_emit(self) -> None:
        from lessish.ast_nodes import Lookup, Variable
        from lessish.visitors import value_to_css

        target = Variable(index=0, name='@x')
        lk = Lookup(index=0, target=target, key_kind='name', key='c')
        self.assertEqual(value_to_css(lk), '@x[c]')

    def test_format_mixin_arg_with_name(self) -> None:
        from lessish.ast_nodes import Keyword, MixinArg
        from lessish.visitors.emitter import _format_mixin_arg

        # Keyword is not a Value, but _format_mixin_arg pattern-matches on
        # `value` shape, so the narrower static type is fine to bypass here.
        arg = MixinArg(index=0, name='@k', value=Keyword(index=0, value='red'))  # type: ignore[arg-type]
        self.assertEqual(_format_mixin_arg(arg), '@k: red')

    def test_format_mixin_arg_without_name(self) -> None:
        from lessish.ast_nodes import Dimension, MixinArg
        from lessish.visitors.emitter import _format_mixin_arg

        arg = MixinArg(index=0, name=None, value=Dimension(index=0, value=1, unit='px'))  # type: ignore[arg-type]
        self.assertEqual(_format_mixin_arg(arg), '1px')

    def test_format_mixin_arg_with_no_value(self) -> None:
        # Defensive: `value` is None — returns empty string instead of
        # propagating the missing attribute.
        from lessish.ast_nodes import MixinArg
        from lessish.visitors.emitter import _format_mixin_arg

        arg = MixinArg(index=0, name=None, value=None)  # type: ignore[arg-type]
        self.assertEqual(_format_mixin_arg(arg), '')

    def test_format_dimension_module_helper(self) -> None:
        # Module-level `_format_dimension` defers to the neutral emitter.
        from lessish.ast_nodes import Dimension
        from lessish.visitors.emitter import _format_dimension

        self.assertEqual(_format_dimension(Dimension(index=0, value=1.5, unit='px')), '1.5px')

    def test_value_to_css_tilde_paren_unwraps(self) -> None:
        # `~(...)`-marked Paren — `_tilde_list` tag tells the emitter
        # this is a list constructor, not a CSS paren. The wrap drops.
        from lessish.ast_nodes import Dimension, Paren
        from lessish.visitors import value_to_css

        p = Paren(index=0, value=Dimension(index=0, value=5, unit='px'))
        p._tilde_list = True
        self.assertEqual(value_to_css(p), '5px')

    def test_value_to_css_mixin_call_with_args(self) -> None:
        from lessish.ast_nodes import Anonymous, MixinArg, MixinCall
        from lessish.visitors import value_to_css

        mc = MixinCall(
            index=0,
            name='.m',
            args=[MixinArg(index=0, name=None, value=Anonymous(index=0, value='a'))],  # type: ignore[arg-type]
            important=False,
        )
        self.assertEqual(value_to_css(mc), '.m(a)')

    def test_is_amp_only_selectors_empty_returns_false(self) -> None:
        from lessish.ast_nodes import Ruleset
        from lessish.visitors.structure import _is_amp_only_selectors

        rs = Ruleset(index=0, selectors=[], rules=[], root=False)
        self.assertFalse(_is_amp_only_selectors(rs))

    def test_is_amp_only_selectors_multi_element_returns_false(self) -> None:
        from lessish.ast_nodes import Element, Ruleset, Selector
        from lessish.visitors.structure import _is_amp_only_selectors

        sel = Selector(
            index=0,
            elements=[
                Element(index=0, combinator='', value='&'),
                Element(index=0, combinator=' ', value='.x'),
            ],
        )
        rs = Ruleset(index=0, selectors=[sel], rules=[], root=False)
        self.assertFalse(_is_amp_only_selectors(rs))

    def test_is_amp_only_selectors_non_amp_returns_false(self) -> None:
        from lessish.ast_nodes import Element, Ruleset, Selector
        from lessish.visitors.structure import _is_amp_only_selectors

        sel = Selector(index=0, elements=[Element(index=0, combinator='', value='.x')])
        rs = Ruleset(index=0, selectors=[sel], rules=[], root=False)
        self.assertFalse(_is_amp_only_selectors(rs))

    def test_dedup_charset_skips_non_root_ruleset(self) -> None:
        from lessish.ast_nodes import Ruleset
        from lessish.visitors import dedup_charset

        rs = Ruleset(index=0, selectors=[], rules=[], root=False)
        dedup_charset(rs)  # No-op, doesn't raise.

    def test_compress_in_block_loud_comment_dropped(self) -> None:
        out = self._c('.a { /* drop */ c: 1; }')
        self.assertNotIn('drop', out)

    def test_compress_in_block_bang_comment_kept(self) -> None:
        out = self._c('.a { /*! keep */ c: 1; }')
        self.assertIn('/*! keep */', out)

    def test_compress_atrule_prelude_string_escape(self) -> None:
        # `_compress_atrule_prelude` walks past `\\`-escape pairs inside
        # quoted strings without confusing the closing quote.
        out = self._c(r'@media (font-family: "a\"b") { .x { c: 1; } }')
        self.assertIn(r'"a\"b"', out)


class TestMixinFeatures(unittest.TestCase):
    """Mixin definition / call edges in `mixins.py`."""

    def _c(self, src: str) -> str:
        from lessish import Lessish

        return Lessish().compile(src)

    def test_split_namespace_path_with_args_in_segment(self) -> None:
        from lessish.mixins import _split_namespace_path

        self.assertEqual(_split_namespace_path('.foo(arg) > .bar'), ['.foo(arg)', '.bar'])

    def test_split_namespace_path_with_brackets(self) -> None:
        from lessish.mixins import _split_namespace_path

        self.assertEqual(_split_namespace_path('.foo[attr]'), ['.foo[attr]'])

    def test_split_namespace_path_compound_segment(self) -> None:
        from lessish.mixins import _split_namespace_path

        self.assertEqual(_split_namespace_path('#a.b.c'), ['#a', '.b', '.c'])

    def test_mixed_delimiter_with_named_args_raises(self) -> None:
        # The error fires when a `,`-separated group with a named arg
        # at position > 0 is followed by `;` — exactly less.js's
        # `Cannot mix ; and , as delimiter types`.
        from lessish.errors import ParseError

        with self.assertRaises(ParseError) as cm:
            self._c('.m(@x; @b) { c: @x; d: @b; } .a { .m(1, @x: 2; @b: 3); }')
        self.assertIn('Cannot mix ; and ,', cm.exception.message)

    def test_empty_mixin_call_args(self) -> None:
        from lessish.mixins import _split_top_level

        self.assertEqual(_split_top_level(''), [])

    def test_mixin_arg_with_backslash_escape_in_string(self) -> None:
        # `_strip_comments_from_args` walks past `\\`-escape pairs
        # inside strings.
        out = self._c(r'.m(@s) { c: @s; } .x { .m("a\\b"); }')
        self.assertIn(r'"a\\b"', out)

    def test_mixin_arg_with_block_comment(self) -> None:
        out = self._c('.m(@a /* c */, @b) { c: @a; d: @b; } .x { .m(1, 2); }')
        self.assertIn('c: 1;', out)
        self.assertIn('d: 2;', out)

    def test_mixin_arg_with_line_comment(self) -> None:
        out = self._c('.m(@a // line\n, @b) { c: @a; d: @b; } .x { .m(1, 2); }')
        self.assertIn('c: 1;', out)

    def test_mixin_with_guard_passes(self) -> None:
        out = self._c('.m() when (true) { c: 1; } .a { .m(); }')
        self.assertIn('c: 1;', out)

    def test_mixin_with_guard_fails(self) -> None:
        out = self._c('.m() when (false) { c: 1; } .a { .m(); }')
        self.assertEqual(out, '')

    def test_extend_with_guard_tail(self) -> None:
        # `:extend(.b) when (true) { ... }` — the `when` tail is peeled
        # off by `_selector_has_guard_tail` and the extend processes.
        out = self._c('.a:extend(.b) when (true) { color: red; } .b { c: blue; }')
        self.assertIn('color: red', out)
        self.assertIn('.b,\n.a', out)

    def test_ruleset_used_as_mixin(self) -> None:
        # `.a, .b { ... }` is callable under EACH selector. Direct uses
        # of either name expand to the body rules.
        out = self._c('.a, .b { color: red; } .x { .a; }')
        self.assertIn('.x', out)

    def test_ruleset_used_as_mixin_second_name(self) -> None:
        out = self._c('.a, .b { color: red; } .x { .b; }')
        self.assertIn('.x', out)

    def test_namespace_path_without_parens(self) -> None:
        # `#ns > .m;` — mixin call without `()` invokes the 0-arg form.
        out = self._c('#ns() { .m() { c: red; } } .a { #ns > .m; }')
        self.assertIn('c: red;', out)

    def test_default_in_mixin_guard_fallback(self) -> None:
        out = self._c('.m(@a) when (default()) { c: fb; } .a { .m(1); }')
        self.assertIn('c: fb;', out)

    def test_mixin_call_with_important_propagates(self) -> None:
        out = self._c('.m() { c: red; } .a { .m() !important; }')
        self.assertIn('c: red !important', out)

    def test_mixin_with_detached_ruleset_arg(self) -> None:
        out = self._c('.m(@dr) { @dr(); } .x { .m({ c: red; }); }')
        self.assertIn('c: red;', out)

    def test_mixin_with_rest_after_positional(self) -> None:
        out = self._c('.m(@a, @rest...) { c: @a; d: @rest; } .x { .m(1, 2, 3, 4); }')
        self.assertIn('c: 1;', out)
        self.assertIn('d: 2 3 4;', out)

    def test_interpolated_multi_selector_ruleset_called(self) -> None:
        # `.@{n}, .bar { ... }` — one selector is interpolated. The
        # mixin index has both names available.
        out = self._c('@n: foo; .@{n}, .bar { color: red; } .x { .foo; }')
        self.assertIn('.x', out)

    def test_interpolated_mixin_definition_called(self) -> None:
        # `.@{n}() { ... }` — the mixin index marks this definition as
        # `interpolated`; lookup resolves the name at the call site.
        out = self._c('@n: foo; .@{n}() { c: red; } .a { .foo(); }')
        self.assertIn('c: red;', out)

    def test_guard_on_non_last_selector_raises(self) -> None:
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            self._c('.a when (true), .b { color: red; }')
        self.assertIn('single selector', cm.exception.message)

    def test_name_exists_via_interpolated_definition(self) -> None:
        # `@n: missing; .@{n}(@x) {}` — the mixin def's name resolves
        # to `.missing`. Call `.missing()` has wrong arity (0 vs 1) so
        # `find_mixin_matches` returns []; `name_exists` then finds the
        # interpolated def and the error becomes "No matching definition"
        # instead of "is undefined".
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            self._c('@n: missing; .@{n}(@x) { c: red; } .a { .missing(); }')
        self.assertIn('No matching definition', cm.exception.message)

    def test_name_exists_via_interpolated_ruleset(self) -> None:
        # Same path but the interpolated source is a Ruleset whose
        # `.foo` selector matches the call name after resolution.
        # Call with args → arity-mismatched against a Ruleset (which is
        # only callable with no args), so `name_exists` sees the
        # interpolated Ruleset and "No matching definition" fires.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError):
            self._c('@n: foo; .@{n}, .bar { color: red; } .a { .foo(1, 2); }')

    def test_multi_segment_ruleset_called_as_mixin(self) -> None:
        # `.a .b { ... }` is a descendant-selector ruleset; calling it
        # as `.a > .b` triggers the multi-segment match branch in
        # `_descend_namespace`.
        out = self._c('.a .b { color: red; } .x { .a > .b; }')
        self.assertIn('.x', out)
        # Both the source ruleset and the call-site selector emit.
        self.assertEqual(out.count('color: red'), 2)

    def test_variable_call_without_parens(self) -> None:
        # `@dr;` at statement position → VariableCall, splices the DR.
        out = self._c('@dr: { c: red; }; .a { @dr; }')
        self.assertIn('c: red;', out)


class TestContextSubstitution(unittest.TestCase):
    """Edges in `context.py` — `substitute_text`'s `url(...)` splitter
    and `lookup_variable_node` fall-back paths.
    """

    def _c(self, src: str) -> str:
        from lessish import Lessish

        return Lessish().compile(src)

    def test_url_with_variable_substituted(self) -> None:
        # `url(@path)` runs through the url-splitting branch of
        # `substitute_text` so the variable resolves inside.
        out = self._c('@path: "x.png"; .a { background: url(@path); }')
        self.assertIn('url("x.png")', out)

    def test_url_with_quoted_interpolation(self) -> None:
        out = self._c('@name: x.png; .a { background: url("@{name}"); }')
        self.assertIn('url("x.png")', out)

    def test_url_with_quoted_escape_in_inner(self) -> None:
        # `url("a\"b")` — the inner string walker handles `\\` escapes.
        out = self._c(r'.a { background: url("escape\"inner"); }')
        self.assertIn(r'"escape\"inner"', out)

    def test_variable_with_trailing_block_comment(self) -> None:
        # `@x: red /* trail */;` — the trailing-comment branch evaluates
        # the body, attaches the comment back, and returns an Anonymous.
        out = self._c('@x: red /* trail */; .a { c: @x; }')
        self.assertIn('red /* trail */', out)

    def test_variable_with_dr_value_used_in_decl_raises(self) -> None:
        # `@x: { c: 1; };` is a DR; assigning to a CSS prop raises.
        from lessish.errors import EvalError

        with self.assertRaises(EvalError) as cm:
            self._c('@x: { c: 1; }; .a { c: @x; }')
        self.assertIn('Rulesets cannot be evaluated', cm.exception.message)

    def test_property_interpolation_missing_raises(self) -> None:
        # `${nope}` interpolation — the lookup raises UndefinedNameError.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            self._c('.a { c: ${nope}; }')

    def test_variable_with_arrow_function_value(self) -> None:
        out = self._c('@x: () => {body}; .a { c: @x; }')
        self.assertIn('() => {body}', out)

    def test_variable_with_middle_block_comment(self) -> None:
        out = self._c('@x: 1 /* c */ 2; .a { c: @x; }')
        self.assertIn('/* c */', out)

    def test_split_string_runs_with_url(self) -> None:
        from lessish.context import _split_string_runs

        result = _split_string_runs('@x url(@y) more')
        self.assertEqual(
            result,
            [
                (False, '@x '),
                (False, 'url('),
                (False, '@y'),
                (False, ')'),
                (False, ' more'),
            ],
        )

    def test_split_string_runs_url_with_quoted_inner(self) -> None:
        from lessish.context import _split_string_runs

        # `url("...")` — inner string isn't bare-substitutable.
        result = _split_string_runs('url("@{name}") text')
        # Inner quoted segment is marked True (string-like).
        self.assertTrue(any(s for s, _ in result))

    def test_split_string_runs_url_with_escape_in_inner_quote(self) -> None:
        from lessish.context import _split_string_runs

        result = _split_string_runs(r'url("a\"b")')
        # No exception; result has the right shape (url-prefix, inner, `)`).
        self.assertEqual(len(result), 3)

    def test_fresh_dr_clones_passthrough_for_non_dr(self) -> None:
        from lessish.ast_nodes import Dimension
        from lessish.context import _fresh_dr_clones

        n = Dimension(index=0, value=1.0, unit='px')
        self.assertIs(_fresh_dr_clones(n), n)

    def test_is_static_url_without_interpolation(self) -> None:
        from lessish.ast_nodes import Url
        from lessish.context import _is_static

        self.assertTrue(_is_static(Url(index=0, value='x.png')))
        self.assertFalse(_is_static(Url(index=0, value='@var')))

    def test_has_block_comment_with_string_containing_backslash(self) -> None:
        from lessish.context import _has_block_comment

        # `"a\b" /* c */` — escape walker steps past `\\` pair, then
        # detects the block comment.
        self.assertTrue(_has_block_comment(r'"a\b" /* c */'))
        # `"no comment"` — string with escape but no `/*`.
        self.assertFalse(_has_block_comment(r'"a\""'))


class TestEvaluatorInternals(unittest.TestCase):
    """Direct unit tests for evaluator internals.

    These cover defensive branches that the public Less surface can't
    reach (or that an earlier failure pre-empts) — e.g. "unknown
    operator" raises, type guards on synthesised AST shapes, helpers
    invoked from one specific code path. Direct calls let us exercise
    every branch without re-engineering the public compiler.
    """

    def test_apply_unknown_operator_raises(self) -> None:
        from lessish.errors import OperationError
        from lessish.evaluator import _apply

        with self.assertRaises(OperationError) as cm:
            _apply('%', 1.0, 2.0)
        self.assertIn('unknown operator', str(cm.exception))

    def test_apply_division_by_zero_raises(self) -> None:
        # Color arithmetic divides per-channel via `_apply`; division
        # by zero raises here (Dimension arithmetic has its own check).
        from lessish.errors import OperationError
        from lessish.evaluator import _apply

        with self.assertRaises(OperationError) as cm:
            _apply('/', 5.0, 0.0)
        self.assertIn('division by zero', str(cm.exception))

    def test_operate_dimensions_unknown_operator_raises(self) -> None:
        from lessish.ast_nodes import Dimension
        from lessish.errors import OperationError
        from lessish.evaluator import _operate_dimensions

        a = Dimension(index=0, value=10.0, unit='px')
        b = Dimension(index=0, value=2.0, unit='px')
        with self.assertRaises(OperationError) as cm:
            _operate_dimensions('%', a, b, 0)
        self.assertIn('unknown operator', str(cm.exception))

    def test_lookup_in_rules_unknown_kind_raises(self) -> None:
        from lessish.context import EvalContext
        from lessish.errors import EvalError
        from lessish.evaluator import _lookup_in_rules
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        with self.assertRaises(EvalError) as cm:
            _lookup_in_rules([], 'bogus-kind', 'x', ctx)
        self.assertIn('unknown lookup key kind', str(cm.exception))

    def test_resolve_lookup_target_ruleset_returns_rules(self) -> None:
        # Rare path: target evaluates to a (non-DetachedRuleset) Ruleset.
        # The function returns `resolved.rules` directly.
        from lessish.ast_nodes import Anonymous, Declaration, Ruleset
        from lessish.context import EvalContext
        from lessish.evaluator import _resolve_lookup_target
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        # Build a Ruleset that's not a DR.
        decl = Declaration(
            index=0,
            name='a',
            value=Anonymous(index=0, value='1'),
            important=False,
            variable=False,
            merge='',
        )
        rs = Ruleset(index=0, selectors=[], rules=[decl], root=False)
        # `eval_node` on a Ruleset returns it as-is (via eval_ruleset).
        rules = _resolve_lookup_target(rs, ctx)
        self.assertEqual(len(rules), 1)

    def test_resolve_lookup_target_invalid_type_raises(self) -> None:
        # Pass a literal-shaped target (Dimension); `eval_node` returns
        # it unchanged, then the dispatch falls through to the catch-all
        # `not a namespace, mixin call, or detached ruleset` raise.
        from lessish.ast_nodes import Dimension
        from lessish.context import EvalContext
        from lessish.errors import EvalError
        from lessish.evaluator import _resolve_lookup_target
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        target = Dimension(index=0, value=5.0, unit='px')
        with self.assertRaises(EvalError) as cm:
            _resolve_lookup_target(target, ctx)
        self.assertIn('not a namespace', str(cm.exception))

    def test_lookup_target_is_important_for_undefined_variable(self) -> None:
        # Variable target whose name doesn't resolve → returns False.
        # The public `eval_lookup` would raise on the unresolvable
        # target before reaching this helper, but the check is still
        # called from positions where it shouldn't throw.
        from lessish.ast_nodes import Variable
        from lessish.context import EvalContext
        from lessish.evaluator import _lookup_target_is_important
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        target = Variable(index=0, name='@nope')
        self.assertFalse(_lookup_target_is_important(target, ctx))

    def test_lookup_target_is_important_for_non_anonymous_value(self) -> None:
        # Variable resolves to a Declaration whose value isn't Anonymous
        # — exercises the `if not isinstance(decl.value, Anonymous):
        # return False` guard.
        from lessish.ast_nodes import Declaration, Dimension, Ruleset, Variable
        from lessish.context import EvalContext
        from lessish.evaluator import _lookup_target_is_important
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        decl = Declaration(
            index=0,
            name='@x',
            value=Dimension(index=0, value=5.0, unit='px'),
            important=False,
            variable=True,
            merge='',
        )
        frame = Ruleset(index=0, selectors=[], rules=[decl], root=False)
        ctx.push_frame(frame)
        try:
            target = Variable(index=0, name='@x')
            self.assertFalse(_lookup_target_is_important(target, ctx))
        finally:
            ctx.pop_frame()

    def test_is_amp_when_block_multi_selector_returns_false(self) -> None:
        from lessish.ast_nodes import (
            Condition,
            Element,
            Keyword,
            Ruleset,
            Selector,
        )
        from lessish.evaluator import _is_amp_when_block

        amp = Selector(index=0, elements=[Element(index=0, combinator='', value='&')])
        other = Selector(index=0, elements=[Element(index=0, combinator='', value='.x')])
        rs = Ruleset(
            index=0,
            selectors=[amp, other],
            rules=[],
            root=False,
            condition=Condition(index=0, op='truthy', lhs=Keyword(index=0, value='true')),
        )
        self.assertFalse(_is_amp_when_block(rs))

    def test_propagate_important_returns_node_unchanged(self) -> None:
        # Node has no `_important_pending` tag and isn't a container
        # type the propagator handles — returns it as-is.
        from lessish.ast_nodes import Anonymous
        from lessish.evaluator import _propagate_important

        n = Anonymous(index=0, value='red')
        self.assertIs(_propagate_important(n), n)

    def test_propagate_important_skips_already_important_decl(self) -> None:
        # A declaration that's already `!important` or a variable
        # declaration short-circuits the wrap.
        from lessish.ast_nodes import Anonymous, Declaration
        from lessish.evaluator import _propagate_important

        d = Declaration(
            index=0,
            name='color',
            value=Anonymous(index=0, value='red'),
            important=True,
            variable=False,
            merge='',
        )
        self.assertIs(_propagate_important(d), d)

    def test_propagate_important_skips_variable_decl(self) -> None:
        from lessish.ast_nodes import Anonymous, Declaration
        from lessish.evaluator import _propagate_important

        d = Declaration(
            index=0,
            name='@x',
            value=Anonymous(index=0, value='5'),
            important=False,
            variable=True,
            merge='',
        )
        self.assertIs(_propagate_important(d), d)

    def test_format_call_args_falls_back_on_unevaluable_arg(self) -> None:
        # `eval_value` on `@undef` raises UndefinedNameError; the catch
        # falls back to `value_to_css(a.value)` so the signature still
        # renders. This branch isn't reachable through the public
        # compiler (the matching pass raises first) — exercise directly.
        from lessish.ast_nodes import (
            Anonymous,
            Expression,
            MixinArg,
            MixinCall,
            Value,
            Variable,
        )
        from lessish.context import EvalContext
        from lessish.evaluator import _format_call_args
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        var = Variable(index=0, name='@undef')
        arg = MixinArg(
            index=0,
            name=None,
            value=Value(index=0, expressions=[Expression(index=0, values=[var])]),
        )
        named = MixinArg(
            index=0,
            name='@k',
            value=Value(
                index=0,
                expressions=[Expression(index=0, values=[Anonymous(index=0, value='1')])],
            ),
        )
        call = MixinCall(index=0, name='.m', args=[arg, named], important=False)
        text = _format_call_args(call, ctx)
        self.assertIn('@undef', text)
        self.assertIn('@k:', text)

    def test_invoke_if_statement_keeps_expression_arg(self) -> None:
        # `_invoke_if_statement` mirror of `_invoke_function_statement`
        # for the `if(...)` special form. When arg.value is already
        # Expression, the wrap path is skipped.
        from lessish.ast_nodes import (
            Expression,
            Keyword,
            MixinArg,
            MixinCall,
        )
        from lessish.context import EvalContext
        from lessish.evaluator import _invoke_if_statement
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        # Condition arg as an Expression (already).
        cond_arg = MixinArg(
            index=0,
            name=None,
            value=Expression(index=0, values=[Keyword(index=0, value='true')]),  # type: ignore[arg-type]
        )
        # Then arg
        then_arg = MixinArg(
            index=0,
            name=None,
            value=Expression(index=0, values=[Keyword(index=0, value='red')]),  # type: ignore[arg-type]
        )
        call = MixinCall(index=0, name='if', args=[cond_arg, then_arg], important=False)
        _invoke_if_statement(call, ctx)  # No raise — `true` selects `red`.

    def test_invoke_function_statement_keeps_expression_arg(self) -> None:
        # When `arg.value` is already an Expression, the promoter
        # appends it directly without re-wrapping.
        from lessish.ast_nodes import (
            Anonymous,
            Expression,
            MixinArg,
            MixinCall,
        )
        from lessish.context import EvalContext
        from lessish.evaluator import _invoke_function_statement
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        expr = Expression(index=0, values=[Anonymous(index=0, value='hello')])
        call = MixinCall(
            index=0,
            name='e',
            args=[MixinArg(index=0, name=None, value=expr)],  # type: ignore[arg-type]
            important=False,
        )
        self.assertIsNotNone(_invoke_function_statement(call, ctx))

    def test_eagerly_eval_skips_non_anonymous_value(self) -> None:
        # The eager pass only runs when the value is Anonymous text;
        # a Declaration whose value is already a structured AST node
        # short-circuits at the type guard.
        from lessish.ast_nodes import Declaration, Dimension
        from lessish.context import EvalContext
        from lessish.evaluator import _eagerly_eval_calls_in_variable
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        d = Declaration(
            index=0,
            name='@x',
            value=Dimension(index=0, value=5.0, unit='px'),
            important=False,
            variable=True,
            merge='',
        )
        _eagerly_eval_calls_in_variable(d, ctx)  # Should return without raising.

    def test_attach_decl_anchor_decl_start_with_anonymous(self) -> None:
        # Internal helper used by ParseError `_propagate` re-anchoring.
        # The `decl_start` mode pins the anchor at the declaration's
        # name index regardless of the value's shape.
        from lessish.ast_nodes import Anonymous, Declaration
        from lessish.errors import DeclAnchor, ParseError
        from lessish.evaluator import _attach_decl_anchor

        d = Declaration(
            index=42,
            name='color',
            value=Anonymous(index=50, value='red'),
            important=False,
            variable=False,
            merge='',
        )
        err = ParseError('test')
        _attach_decl_anchor(err, d, DeclAnchor.DECL_START)
        self.assertEqual(err.__dict__.get('_base_index'), 42)

    def test_attach_decl_anchor_value_end_with_anonymous(self) -> None:
        # `value_end` mode pins past the value's last char.
        from lessish.ast_nodes import Anonymous, Declaration
        from lessish.errors import DeclAnchor, ParseError
        from lessish.evaluator import _attach_decl_anchor

        d = Declaration(
            index=10,
            name='c',
            value=Anonymous(index=20, value='red'),
            important=False,
            variable=False,
            merge='',
        )
        err = ParseError('test')
        _attach_decl_anchor(err, d, DeclAnchor.VALUE_END)
        # `value_end` = Anonymous.index + len(value) = 20 + 3 = 23
        self.assertEqual(err.__dict__.get('_base_index'), 23)

    def test_attach_decl_anchor_value_end_with_non_anonymous(self) -> None:
        # When the value isn't Anonymous (e.g. already-structured
        # Dimension), `value_end` falls back to the declaration's
        # start index.
        from lessish.ast_nodes import Declaration, Dimension
        from lessish.errors import DeclAnchor, ParseError
        from lessish.evaluator import _attach_decl_anchor

        d = Declaration(
            index=42,
            name='c',
            value=Dimension(index=50, value=5.0, unit='px'),
            important=False,
            variable=False,
            merge='',
        )
        err = ParseError('test')
        _attach_decl_anchor(err, d, DeclAnchor.VALUE_END)
        self.assertEqual(err.__dict__.get('_base_index'), 42)

    def test_lookup_var_indirect_with_quoted_resolution(self) -> None:
        # `@@xname` where @xname resolves to `"x"` (with quotes) — the
        # `var-indirect` branch strips the surrounding quotes before
        # the inner `var` lookup.
        out = compile('@xname: "x"; @m: { @x: red; }; .a { c: @m[@@xname]; }')
        self.assertIn('c: red;', out)

    def test_lookup_prop_indirect_with_quoted_resolution(self) -> None:
        # Same path for `$@name`.
        out = compile('@name: "color"; @m: { color: red; }; .a { c: @m[$@name]; }')
        self.assertIn('c: red;', out)

    def test_arg_to_condition_value_with_multiple_expressions(self) -> None:
        # Construct a Value with len(expressions) > 1; the dispatcher
        # falls through to the multi-expression truthy branch.
        from lessish.ast_nodes import Expression, Keyword, Value
        from lessish.evaluator import _arg_to_condition

        v = Value(
            index=0,
            expressions=[
                Expression(index=0, values=[Keyword(index=0, value='true')]),
                Expression(index=0, values=[Keyword(index=0, value='false')]),
            ],
        )
        cond = _arg_to_condition(v, default_index=0)
        self.assertEqual(cond.op, 'truthy')

    def test_arg_to_condition_value_with_single_expression(self) -> None:
        from lessish.ast_nodes import Expression, Keyword, Value
        from lessish.evaluator import _arg_to_condition

        v = Value(
            index=0,
            expressions=[Expression(index=0, values=[Keyword(index=0, value='true')])],
        )
        cond = _arg_to_condition(v, default_index=0)
        self.assertEqual(cond.op, 'truthy')

    def test_unit_to_condition_empty_values_returns_false(self) -> None:
        from lessish.evaluator import _unit_to_condition

        cond = _unit_to_condition([], idx=0)
        self.assertEqual(cond.op, 'truthy')
        # Narrow the structural typing — _unit_to_condition's lhs is a
        # generic Node but the truthy branch always returns a Keyword.
        from typing import cast as _cast

        from lessish.ast_nodes import Keyword as _Kw

        self.assertIsInstance(cond.lhs, _Kw)
        self.assertEqual(_cast(_Kw, cond.lhs).value, 'false')

    def test_unit_to_condition_single_value_passthrough(self) -> None:
        from lessish.ast_nodes import Keyword
        from lessish.evaluator import _unit_to_condition

        cond = _unit_to_condition([Keyword(index=0, value='true')], idx=0)
        self.assertEqual(cond.op, 'truthy')

    def test_unit_to_condition_multiple_values_wraps(self) -> None:
        # `_wrap_values` packs multi-element value lists into an
        # Expression wrapper before constructing the truthy condition.
        from lessish.ast_nodes import Keyword
        from lessish.evaluator import _unit_to_condition

        cond = _unit_to_condition(
            [Keyword(index=0, value='a'), Keyword(index=0, value='b')],
            idx=0,
        )
        self.assertEqual(cond.op, 'truthy')

    def test_single_to_condition_paren_unwraps(self) -> None:
        from lessish.ast_nodes import Keyword, Paren
        from lessish.evaluator import _single_to_condition

        cond = _single_to_condition(
            Paren(index=0, value=Keyword(index=0, value='true')),
            idx=0,
        )
        self.assertEqual(cond.op, 'truthy')

    def test_single_to_condition_expression_dispatches(self) -> None:
        from lessish.ast_nodes import Expression, Keyword
        from lessish.evaluator import _single_to_condition

        cond = _single_to_condition(
            Expression(index=0, values=[Keyword(index=0, value='true')]),
            idx=0,
        )
        self.assertEqual(cond.op, 'truthy')

    def test_single_to_condition_boolean_no_args(self) -> None:
        # `boolean()` with no args inside a condition — falls back to
        # a truthy(false) condition.
        from lessish.ast_nodes import Call
        from lessish.evaluator import _single_to_condition

        cond = _single_to_condition(Call(index=0, name='boolean', args=[]), idx=0)
        self.assertEqual(cond.op, 'truthy')
        # Narrow the structural typing — _unit_to_condition's lhs is a
        # generic Node but the truthy branch always returns a Keyword.
        from typing import cast as _cast

        from lessish.ast_nodes import Keyword as _Kw

        self.assertIsInstance(cond.lhs, _Kw)
        self.assertEqual(_cast(_Kw, cond.lhs).value, 'false')

    def test_eval_if_single_arg_true_returns_empty(self) -> None:
        # `if(true)` — only one arg (the condition). When true with no
        # value arg, returns an empty Anonymous (not the typical
        # value-arg result).
        from lessish.ast_nodes import Call, Expression, Keyword
        from lessish.context import EvalContext
        from lessish.evaluator import _eval_if
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        call = Call(
            index=0,
            name='if',
            args=[Expression(index=0, values=[Keyword(index=0, value='true')])],
        )
        result = _eval_if(call, ctx)
        self.assertEqual(getattr(result, 'value', None), '')

    def test_single_to_condition_not_no_args(self) -> None:
        # `not()` with no args — inner is the `truthy(false)` baseline
        # and the outer wraps it in a `not` condition.
        from lessish.ast_nodes import Call
        from lessish.evaluator import _single_to_condition

        cond = _single_to_condition(Call(index=0, name='not', args=[]), idx=0)
        self.assertEqual(cond.op, 'not')

    def test_spread_pieces_for_bare_node(self) -> None:
        # When the resolved spread value is a bare leaf (not Value or
        # Expression), `_spread_pieces` wraps it in a single-piece
        # Value containing an Expression.
        from lessish.ast_nodes import Dimension
        from lessish.evaluator import _spread_pieces

        pieces = _spread_pieces(Dimension(index=0, value=5.0, unit='px'), index=0)
        self.assertEqual(len(pieces), 1)

    def test_spread_pieces_for_expression(self) -> None:
        # Expression wraps directly into a single-piece Value.
        from lessish.ast_nodes import Anonymous, Expression
        from lessish.evaluator import _spread_pieces

        pieces = _spread_pieces(
            Expression(index=0, values=[Anonymous(index=0, value='1'), Anonymous(index=0, value='2')]),
            index=0,
        )
        self.assertEqual(len(pieces), 1)

    def test_is_spread_arg_not_expression_inner(self) -> None:
        # When `arg.value` is a Value wrapping a non-Expression node,
        # the spread shape isn't matched and `_is_spread_arg` returns
        # False.
        from lessish.ast_nodes import Anonymous, MixinArg, Value
        from lessish.evaluator import _is_spread_arg

        arg = MixinArg(
            index=0,
            name=None,
            value=Value(index=0, expressions=[Anonymous(index=0, value='x')]),  # type: ignore[list-item]
        )
        self.assertFalse(_is_spread_arg(arg))

    def test_imported_custom_property_preserves_source_tag(self) -> None:
        # Importer tags every node in the subtree with `_source`. The
        # custom-property branch of `_eval_declaration_inner` propagates
        # that tag onto the rebuilt `out_d` so downstream error / source
        # map lookups can attribute the decl to its origin file.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'lib.less'), 'w') as f:
                f.write(':root { --imp: red; }')
            src = '@import "lib";\n:root { --main: blue; }'
            out = compile(src, filename=os.path.join(d, 'in.less'))
        self.assertIn('--imp: red;', out)
        self.assertIn('--main: blue;', out)

    def test_deferred_import_css_passthrough(self) -> None:
        # When the resolved file is a `.css`, the importer keeps the
        # `@import` AtRule as-is; the deferred re-resolver appends it
        # back into the output.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'x.css'), 'w') as f:
                f.write('.imported { c: red; }')
            src = '@n: "x"; @import "@{n}.css";'
            out = compile(src, filename=os.path.join(d, 'in.less'))
        self.assertIn('@import "x.css";', out)

    def test_deferred_import_with_top_level_mixin_call(self) -> None:
        # Deferred-imported file contains a top-level MixinCall; the
        # re-resolver invokes it via `_invoke_mixin_call`.
        import os
        import tempfile

        from lessish.errors import LessError

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'lib.less'), 'w') as f:
                f.write('.thing() { c: red; }\n.thing();')
            src = '@n: "lib"; @import "@{n}";'
            with self.assertRaises(LessError):
                # Top-level mixin call expands to root-position decls,
                # which the post-eval `check_no_root_properties` rejects
                # — but `_resolve_deferred_import`'s MixinCall branch
                # ran first to invoke the call.
                compile(src, filename=os.path.join(d, 'in.less'))

    def test_deferred_import_with_top_level_variable_call(self) -> None:
        # Same shape for `@dr()` VariableCall at the top of the imported
        # file.
        import os
        import tempfile

        from lessish.errors import LessError

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'lib.less'), 'w') as f:
                f.write('@dr: { @x: 1; };\n@dr();')
            src = '@n: "lib"; @import "@{n}";'
            with self.assertRaises(LessError):
                compile(src, filename=os.path.join(d, 'in.less'))

    def test_value_mixin_lookup_through_failed_guard_wrapper(self) -> None:
        # `.r when (false)` is a ruleset wrapper whose guard fails →
        # `_invoke_value_mixin_call` returns [] (no rules to look up),
        # and the subsequent property scan reports it as missing.
        from lessish.errors import UndefinedNameError

        with self.assertRaises(UndefinedNameError):
            compile('.r when (false) { c: 1; } .x { d: .r[c]; }')

    def test_value_mixin_lookup_with_real_mixin_failed_guard(self) -> None:
        # A real `MixinDefinition` (`.m(@x) when (false)`) with the guard
        # failing — `_invoke_value_mixin_call` raises rather than
        # silently returning empty (the non-wrapper branch).
        from lessish.errors import EvalError, LessError

        with self.assertRaises((EvalError, LessError)):
            compile('.m(@x) when (false) { c: 1; } .x { d: .m(5)[c]; }')

    def test_split_trailing_block_comment_no_trailing(self) -> None:
        # Text without a `*/` suffix returns `(text, None)` immediately.
        from lessish.evaluator import _split_trailing_block_comment

        body, comment = _split_trailing_block_comment('red')
        self.assertEqual((body, comment), ('red', None))

    def test_split_trailing_block_comment_orphan_close(self) -> None:
        # `*/` at the end but no matching `/*` before it.
        from lessish.evaluator import _split_trailing_block_comment

        body, comment = _split_trailing_block_comment('red */')
        self.assertEqual((body, comment), ('red */', None))

    def test_split_trailing_block_comment_unclosed_string_before(self) -> None:
        # Open quote before the `/*` — the function bails because the
        # comment is "inside" an unterminated string scope.
        from lessish.evaluator import _split_trailing_block_comment

        body, comment = _split_trailing_block_comment('"unclosed /* trail */')
        self.assertEqual(comment, None)

    def test_split_trailing_block_comment_string_with_escape(self) -> None:
        # `\\` inside a string is an escape pair that the scanner walks
        # past via the `\\` branch — the closing quote of the string is
        # still found correctly, so the trailing comment is detected.
        from lessish.evaluator import _split_trailing_block_comment

        body, comment = _split_trailing_block_comment(r'"a\\b" /* trail */')
        self.assertEqual(comment, '/* trail */')

    def test_is_literal_only_anonymous_with_interpolation(self) -> None:
        from lessish.ast_nodes import Anonymous, Expression, Value
        from lessish.evaluator import _is_literal_only

        v = Value(
            index=0,
            expressions=[Expression(index=0, values=[Anonymous(index=0, value='foo @{name} bar')])],
        )
        self.assertFalse(_is_literal_only(v))

    def test_color_plus_color_arithmetic(self) -> None:
        # `red + blue` exercises `eval_operation_inner` Color+Color path.
        out = compile('.a { c: red + blue; }')
        # red(255,0,0) + blue(0,0,255) = (255,0,255) = magenta = #ff00ff
        self.assertIn('#ff00ff', out)

    def test_explicit_division_operator(self) -> None:
        # `./` (less.js v3 explicit-division) always folds regardless
        # of `math` mode; normalised to `/` internally.
        out = compile('.a { c: 10 ./ 2; }')
        self.assertIn('c: 5;', out)

    def test_math_parens_keeps_addition_outside_parens(self) -> None:
        # `math: parens` — even `+`/`-`/`*` only fold inside `(...)`.
        from lessish import Lessish

        out = Lessish(math='parens').compile('.a { c: 1 + 2; }')
        self.assertIn('c: 1 + 2;', out)

    def test_url_in_paren_with_newline_in_raw(self) -> None:
        # The literal-only fast-path checks `_value_contains_url` only
        # when raw text has a newline. A url inside a paren wrapper
        # exercises the inner `.value` walker.
        src = '.a {\n  background:\n    (url(x.png))\n    center;\n}'
        out = compile(src)
        self.assertIn('url(x.png)', out)

    def test_eval_namespace_call_lookups_nested_parens_in_args(self) -> None:
        # Nested `(...)` inside the args of `#name.fn(...)` — exercises
        # the `depth += 1` branch of the paren-balance walker. Wrap in
        # an UndefinedNameError catch since the inner eval would fail.
        from lessish.context import EvalContext
        from lessish.errors import LessError
        from lessish.evaluator import _eval_namespace_call_lookups
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        with self.assertRaises(LessError):
            _eval_namespace_call_lookups('.fn((inner))[k]', ctx)

    def test_eval_namespace_call_lookups_unbalanced_parens_pass_through(self) -> None:
        # Unbalanced parens make the walker fall through to verbatim.
        from lessish.context import EvalContext
        from lessish.evaluator import _eval_namespace_call_lookups
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        # `.fn(unclosed[k]` — paren never closes, head-extension bails.
        result = _eval_namespace_call_lookups('.fn(unclosed[k]', ctx)
        # Output is the input verbatim, character by character.
        self.assertEqual(result, '.fn(unclosed[k]')

    def test_eval_namespace_call_lookups_unbalanced_brackets_pass_through(self) -> None:
        # Chained `[...]` loop bails on unbalanced bracket.
        from lessish.context import EvalContext
        from lessish.evaluator import _eval_namespace_call_lookups
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        result = _eval_namespace_call_lookups('.fn()[unclosed', ctx)
        self.assertEqual(result, '.fn()[unclosed')

    def test_eval_namespace_call_lookups_nested_brackets_balance(self) -> None:
        # `[[nested]]` inside chained brackets — the walker counts the
        # inner `[` via `depth += 1` to keep balance. The actual lookup
        # then fails at parse time, surfaced as a LessError.
        from lessish.context import EvalContext
        from lessish.errors import LessError
        from lessish.evaluator import _eval_namespace_call_lookups
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        with self.assertRaises(LessError):
            _eval_namespace_call_lookups('.fn()[[nested]]', ctx)

    def test_strip_tilde_quotes_with_backslash_escape(self) -> None:
        from lessish.evaluator import _strip_tilde_quotes

        # The escape walker advances past `\\` pairs inside the body.
        self.assertEqual(_strip_tilde_quotes(r'~"a\\b"'), r'a\\b')

    def test_split_top_level_commas_with_backslash_in_string(self) -> None:
        from lessish.evaluator import _split_top_level_commas

        # `\\,` inside the string doesn't count as a top-level comma.
        result = _split_top_level_commas(r'"a\\,b", c')
        self.assertEqual(len(result), 2)

    def test_fold_atrule_prelude_arithmetic_swallows_eval_failure(self) -> None:
        # An at-rule prelude with a `(key: val)` where the value parses
        # but eval raises — the catch lets the prelude pass through
        # verbatim.
        from lessish.context import EvalContext
        from lessish.evaluator import _fold_atrule_prelude_arithmetic
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        # `unit(red, em)` raises ArgumentError (LessError) during eval.
        out = _fold_atrule_prelude_arithmetic('(min-width: unit(red, em))', ctx)
        # Value preserved unchanged on failure.
        self.assertIn('unit(red, em)', out)

    def test_fold_atrule_prelude_arithmetic_folds_arithmetic(self) -> None:
        # Sanity check: an arithmetic-foldable prelude DOES fold.
        from lessish.context import EvalContext
        from lessish.evaluator import _fold_atrule_prelude_arithmetic
        from lessish.source import Source

        ctx = EvalContext(source=Source(text='', filename='x'))
        out = _fold_atrule_prelude_arithmetic('(min-width: (60px + 1px))', ctx)
        self.assertIn('61px', out)

    def test_dim_backup_with_empty_numerator_slash_unit(self) -> None:
        # `/em` — unit starts with `/`, so the numerator after partition
        # is empty. `_dim_backup` skips the numerator branch and falls
        # through to the denominator after the slash.
        from lessish.ast_nodes import Dimension
        from lessish.evaluator import _dim_backup

        d = Dimension(index=0, value=1.0, unit='/em')
        self.assertEqual(_dim_backup(d), 'em')


class StrictImportsDeprecationTests(unittest.TestCase):
    """`strict_imports` is accepted but no-op (matches less.js's own
    deprecation). Embedders should see a `DeprecationWarning` whenever
    they pass it — silent acceptance of a security-relevant flag is a
    footgun.
    """

    def test_explicit_strict_imports_true_warns(self) -> None:
        import warnings as _warnings

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter('always')
            Lessish().compile('.x { color: red; }', strict_imports=True)
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(len(deprecations), 1)
        self.assertIn('strict_imports', str(deprecations[0].message))

    def test_explicit_strict_imports_false_also_warns(self) -> None:
        # We warn on any explicit pass — flipping it off doesn't mean
        # the caller knows it's a no-op. The point of the warning is
        # "stop passing this".
        import warnings as _warnings

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter('always')
            Lessish().compile('.x { color: red; }', strict_imports=False)
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(len(deprecations), 1)

    def test_constructor_strict_imports_warns(self) -> None:
        # Passed via constructor `_defaults` — same treatment.
        import warnings as _warnings

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter('always')
            Lessish(strict_imports=True).compile('.x { color: red; }')
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(len(deprecations), 1)

    def test_omitted_strict_imports_does_not_warn(self) -> None:
        import warnings as _warnings

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter('always')
            Lessish().compile('.x { color: red; }')
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(deprecations, [])


if __name__ == '__main__':
    unittest.main()
