"""Direct unit tests for the function modules under `lessish.functions`.

These exercise the argument-coercion helpers (`_helpers.py`) and the
edge cases of individual built-in functions that the compile-pass
tests in `test_compile.py` don't reach — fatal `ArgumentError`
branches, defensive type guards, percentage / number coercion, list
/ string / svg / math / type-check edges.
"""

from __future__ import annotations

import unittest
from typing import cast

from lessish import Lessish
from lessish.ast_nodes import Dimension, Quoted
from lessish.errors import ArgumentError, LessError


def compile_value(src: str) -> str:
    """Compile a Less program and return its CSS output."""
    return Lessish().compile(src)


def compile_err(src: str) -> LessError | None:
    """Return the exception raised by compile, or None on success."""
    try:
        Lessish().compile(src)
        return None
    except LessError as e:
        return e


class TestHelpers(unittest.TestCase):
    """`functions/_helpers.py` — coercion helpers used by every fn impl."""

    def test_unwrap_value_with_single_expression(self) -> None:
        from lessish.ast_nodes import Expression, Value
        from lessish.functions._helpers import unwrap

        v = Value(
            index=0,
            expressions=[Expression(index=0, values=[Dimension(index=0, value=5, unit='px')])],
        )
        result = unwrap(v)
        # Unwraps Value→Expression→Dimension.
        self.assertIsInstance(result, Dimension)
        result = cast(Dimension, result)
        self.assertEqual(result.value, 5)

    def test_unwrap_expression_with_single_value(self) -> None:
        from lessish.ast_nodes import Expression
        from lessish.functions._helpers import unwrap

        e = Expression(index=0, values=[Dimension(index=0, value=5, unit='px')])
        result = unwrap(e)
        self.assertIsInstance(result, Dimension)

    def test_unwrap_stops_on_multi_expression_value(self) -> None:
        # Value with > 1 expression returns Value as-is.
        from lessish.ast_nodes import Expression, Value
        from lessish.functions._helpers import unwrap

        v = Value(
            index=0,
            expressions=[
                Expression(index=0, values=[Dimension(index=0, value=1, unit='')]),
                Expression(index=0, values=[Dimension(index=0, value=2, unit='')]),
            ],
        )
        self.assertIs(unwrap(v), v)

    def test_as_dimension_raises_on_non_dimension(self) -> None:
        from lessish.ast_nodes import Keyword
        from lessish.functions._helpers import as_dimension

        with self.assertRaises(ArgumentError) as cm:
            as_dimension(Keyword(index=0, value='red'), fn_name='myfn')
        self.assertIn('myfn', str(cm.exception))
        self.assertIn('Keyword', str(cm.exception))

    def test_as_color_from_quoted_hex(self) -> None:
        # `"#ff0000"` — Quoted that parses through the color table.
        from lessish.functions._helpers import as_color

        c = as_color(Quoted(index=0, quote='"', value='#ff0000', escaped=False))
        self.assertEqual(c.rgb, (255, 0, 0))

    def test_as_color_strict_rejects_call_passthrough(self) -> None:
        # `var(--x)` is a Call — under `strict=True` the helper raises
        # a *fatal* ArgumentError tagged for runtime-error reporting.
        from lessish.ast_nodes import Call
        from lessish.functions._helpers import as_color

        with self.assertRaises(ArgumentError) as cm:
            as_color(
                Call(index=0, name='var', args=[]),
                fn_name='darken',
                strict=True,
            )
        self.assertIn('Argument cannot be evaluated', str(cm.exception))
        self.assertTrue(cm.exception.__dict__.get('_fatal'))

    def test_as_color_strict_rejects_anonymous(self) -> None:
        from lessish.ast_nodes import Anonymous
        from lessish.functions._helpers import as_color

        with self.assertRaises(ArgumentError) as cm:
            as_color(
                Anonymous(index=0, value='unknown'),
                fn_name='darken',
                strict=True,
            )
        self.assertTrue(cm.exception.__dict__.get('_fatal'))

    def test_keyword_value_for_quoted(self) -> None:
        from lessish.functions._helpers import keyword_value

        self.assertEqual(
            keyword_value(Quoted(index=0, quote='"', value='hello', escaped=False)),
            'hello',
        )

    def test_keyword_value_for_unknown_node_returns_none(self) -> None:
        from lessish.functions._helpers import keyword_value

        self.assertIsNone(keyword_value(Dimension(index=0, value=1, unit='px')))


class TestSvgGradient(unittest.TestCase):
    """`functions/svg.py` — direction validation, stop parsing, linear
    vs radial rendering."""

    def test_no_args_raises_direction_error(self) -> None:
        e = compile_err('.a { background: svg-gradient(); }')
        self.assertIsInstance(e, ArgumentError)
        self.assertIn('direction', str(e))

    def test_invalid_direction_raises(self) -> None:
        e = compile_err('.a { background: svg-gradient(invalid, red, blue); }')
        self.assertIsInstance(e, ArgumentError)
        self.assertIn('direction', str(e))

    def test_direction_with_non_keyword_element_invalid(self) -> None:
        # `to 5px` — `5px` isn't a keyword inside the direction phrase.
        e = compile_err('.a { background: svg-gradient(to 5px, red, blue); }')
        self.assertIsInstance(e, ArgumentError)

    def test_first_arg_color_not_direction_invalid(self) -> None:
        # `red` as first arg — not a direction keyword.
        e = compile_err('.a { background: svg-gradient(red, blue, green); }')
        self.assertIsInstance(e, ArgumentError)

    def test_direction_only_no_stops_raises(self) -> None:
        e = compile_err('.a { background: svg-gradient(to bottom); }')
        self.assertIsInstance(e, ArgumentError)
        self.assertIn('color', str(e))

    def test_single_stop_raises(self) -> None:
        # Less than 2 stops — `_build_stops` len check.
        e = compile_err('.a { background: svg-gradient(to bottom, red); }')
        self.assertIsInstance(e, ArgumentError)

    def test_bare_dimension_between_colors_invalid(self) -> None:
        # `red, 50%, blue` — bare position with no attached color.
        e = compile_err('.a { background: svg-gradient(to bottom, red, 50%, blue); }')
        self.assertIsInstance(e, ArgumentError)

    def test_unknown_color_keyword_invalid_at_stop_position(self) -> None:
        # `notacolor` isn't in the CSS named-color table; can't promote.
        e = compile_err('.a { background: svg-gradient(to bottom, notacolor, blue); }')
        self.assertIsInstance(e, ArgumentError)

    def test_linear_gradient_to_bottom(self) -> None:
        out = compile_value('.a { background: svg-gradient(to bottom, red, blue); }')
        self.assertIn('linearGradient', out)
        self.assertIn('url(', out)

    def test_linear_gradient_with_named_color_stops(self) -> None:
        # `orange` and `mediumblue` are promoted from Keyword to Color.
        out = compile_value('.a { background: svg-gradient(to bottom, orange, mediumblue); }')
        self.assertIn('linearGradient', out)

    def test_linear_gradient_with_explicit_positions(self) -> None:
        # Output is URL-encoded; `=` becomes `%3D`.
        out = compile_value('.a { background: svg-gradient(to bottom, red 0%, green 50%, blue 100%); }')
        self.assertIn('offset%3D%220%25%22', out)
        self.assertIn('offset%3D%2250%25%22', out)

    def test_linear_gradient_with_fractional_position(self) -> None:
        # `1.5%` — `_format_dim` non-int branch.
        out = compile_value('.a { background: svg-gradient(to bottom, red 1.5%, blue); }')
        self.assertIn('1.5%', out)

    def test_linear_gradient_from_variable_list(self) -> None:
        # `@stops` is a Value with multiple expressions — `_flatten_arg`
        # spreads it.
        out = compile_value('@stops: red, blue, green; .a { background: svg-gradient(to bottom, @stops); }')
        self.assertIn('linearGradient', out)

    def test_bare_dimension_as_direction_invalid(self) -> None:
        # `svg-gradient(5px, ...)` — Dimension as direction; not a
        # Keyword or Expression → `_stringify_keywords` falls through
        # to the final `return ''`.
        e = compile_err('.a { background: svg-gradient(5px, red, blue); }')
        self.assertIsInstance(e, ArgumentError)

    def test_stop_expression_with_unknown_keyword_invalid(self) -> None:
        # `red 50% notacolor` — Expression in a stop with a third
        # element that's neither Color nor Dimension → raises.
        e = compile_err('.a { background: svg-gradient(to bottom, red 50% notacolor, blue); }')
        self.assertIsInstance(e, ArgumentError)

    def test_stop_expression_without_color_invalid(self) -> None:
        # `50% 25%` — two Dimensions, no Color; the post-loop
        # `if color_part is None: raise` fires.
        e = compile_err('.a { background: svg-gradient(to bottom, 50% 25%, blue); }')
        self.assertIsInstance(e, ArgumentError)

    def test_radial_gradient_ellipse_at_center(self) -> None:
        # The only radial direction less.js / lessish accept.
        out = compile_value('.a { background: svg-gradient(ellipse at center, red, blue); }')
        self.assertIn('radialGradient', out)
        # `cx=` URL-encoded.
        self.assertIn('cx%3D', out)


class TestStringFunctions(unittest.TestCase):
    """`functions/string.py` — e, escape, replace, %."""

    def test_e_no_args_soft_passthrough(self) -> None:
        out = compile_value('.a { c: e(); }')
        self.assertIn('e()', out)

    def test_escape_no_args_soft_passthrough(self) -> None:
        out = compile_value('.a { c: escape(); }')
        self.assertIn('escape()', out)

    def test_format_no_args_soft_passthrough(self) -> None:
        out = compile_value('.a { c: %(); }')
        self.assertIn('%()', out)

    def test_replace_too_few_args_soft_passthrough(self) -> None:
        out = compile_value('.a { c: replace("hello", "l"); }')
        self.assertIn('replace(', out)

    def test_e_with_non_quoted_input(self) -> None:
        out = compile_value('.a { c: e(red); }')
        self.assertIn('red', out)

    def test_escape_special_chars(self) -> None:
        out = compile_value('.a { c: escape("a=b"); }')
        # `=` → `%3D` (the secondary-replacement table).
        self.assertIn('a%3Db', out)

    def test_replace_with_backref_digit(self) -> None:
        out = compile_value(r'.a { c: replace("hello world", "(\w+)", "x-$1"); }')
        # `$1` becomes the captured group's text.
        self.assertIn('x-hello', out)

    def test_replace_with_dollar_dollar_literal(self) -> None:
        # `$$` is JS escape for a literal `$`.
        out = compile_value(r'.a { c: replace("hello", "l", "$$"); }')
        self.assertIn('$', out)

    def test_replace_with_whole_match_amp(self) -> None:
        # `$&` references the whole match.
        out = compile_value(r'.a { c: replace("hello", "(l+)", "<$&>"); }')
        self.assertIn('<ll>', out)

    def test_replace_with_named_group(self) -> None:
        # `${name}` references a named capture group — direct call into
        # the function bypasses Less's `${prop}` substitution.
        from lessish.functions.string import fn_replace

        result = fn_replace(
            [
                Quoted(index=0, quote='"', value='hello', escaped=False),
                Quoted(index=0, quote='"', value='(?P<x>l+)', escaped=False),
                Quoted(index=0, quote='"', value='[${x}]', escaped=False),
            ],
            None,  # type: ignore[arg-type]
        )
        self.assertIsInstance(result, Quoted)
        result = cast(Quoted, result)
        self.assertEqual(result.value, 'he[ll]o')

    def test_js_to_py_replacement_backslash_escape(self) -> None:
        # Literal `\` in the replacement string is doubled so re.sub
        # treats it as a literal backslash rather than a group ref.
        from lessish.functions.string import _js_to_py_replacement

        # No `$` → returns input unchanged.
        self.assertEqual(_js_to_py_replacement('plain'), 'plain')
        # With `\` — gets doubled.
        result = _js_to_py_replacement(r'\1$1')
        self.assertIn(r'\\', result)

    def test_replace_with_multiline_flag(self) -> None:
        # `m` flag → re.MULTILINE.
        out = compile_value(r'.a { c: replace("a\nb", "^a", "X", "m"); }')
        self.assertIn('X', out)

    def test_format_with_unfilled_token_keeps_token_verbatim(self) -> None:
        # More tokens than args — leftover tokens stay literal.
        out = compile_value('.a { c: %("%s %s", "a"); }')
        self.assertIn('%s', out)

    def test_format_uppercase_token_url_encodes(self) -> None:
        # `%S` (uppercase) URL-encodes the substituted value.
        out = compile_value('.a { c: %("u=%S", "a b"); }')
        self.assertIn('a%20b', out)

    def test_format_with_non_quoted_arg(self) -> None:
        # `%s` with non-Quoted arg — falls through to `value_to_css`.
        out = compile_value('.a { c: %("v=%s", 42); }')
        self.assertIn('v=42', out)

    def test_e_with_quoted_input_uses_value(self) -> None:
        # `e("text")` — Quoted arg → emits the raw value (no quotes).
        out = compile_value('.a { c: e("text"); }')
        self.assertIn('text', out)

    def test_replace_with_non_quoted_haystack(self) -> None:
        # `replace(hello, ...)` — Keyword as haystack; coerced via
        # `value_to_css` to an unquoted output.
        out = compile_value('.a { c: replace(hello, "l", "x"); }')
        self.assertIn('hexlo', out)


class TestColorFunctions(unittest.TestCase):
    """`functions/color.py` — argument-validation & alt-syntax edges."""

    def test_rgba_modern_syntax(self) -> None:
        out = compile_value('.a { c: rgba(255 0 0 / 0.5); }')
        self.assertIn('rgba(255, 0, 0, 0.5)', out)

    def test_hsla_modern_syntax(self) -> None:
        out = compile_value('.a { c: hsla(120 50% 50% / 0.5); }')
        self.assertIn('hsla(', out)

    def test_rgb_with_color_input(self) -> None:
        # `rgb(<color>)` — single Color arg is a no-op passthrough.
        out = compile_value('.a { c: rgb(#ff0000); }')
        self.assertIn('#ff0000', out)

    def test_hsl_with_color_input(self) -> None:
        # `hsl(<color>)` reformulates a Color in HSL syntax.
        out = compile_value('.a { c: hsl(#ff0000); }')
        self.assertIn('hsl(0, 100%, 50%)', out)

    def test_hsl_with_color_below_alpha_one_emits_hsla(self) -> None:
        # Color with alpha < 1 routes through the `hsla` color-function
        # spelling.
        out = compile_value('@x: rgba(255,0,0,0.5); .a { c: hsl(@x); }')
        self.assertIn('hsla(', out)

    def test_rgba_with_three_args_passthrough(self) -> None:
        out = compile_value('.a { c: rgba(1, 2, 3); }')
        self.assertIn('rgba(', out)

    def test_hsva_wrong_count_passthrough(self) -> None:
        out = compile_value('.a { c: hsva(1, 2); }')
        self.assertIn('hsva(', out)

    def test_fade_wrong_count_passthrough(self) -> None:
        out = compile_value('.a { c: fade(red); }')
        self.assertIn('fade(', out)

    def test_spin_wrong_count_passthrough(self) -> None:
        out = compile_value('.a { c: spin(red); }')
        self.assertIn('spin(', out)

    def test_mix_wrong_count_passthrough(self) -> None:
        out = compile_value('.a { c: mix(red); }')
        self.assertIn('mix(', out)

    def test_contrast_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: contrast(); }')
        self.assertIn('contrast()', out)

    def test_color_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: color(); }')
        self.assertIn('color()', out)

    def test_color_with_bad_hex_raises_fatal(self) -> None:
        # `color("not-a-color")` — fatal ArgumentError, NOT soft
        # passthrough (matches less.js's stricter parse for `color()`).
        from lessish.errors import ArgumentError

        with self.assertRaises(ArgumentError):
            compile_value('.a { c: color("not-a-color"); }')

    def test_spin_with_negative_offset_wraps_below_zero(self) -> None:
        # Red is at hue 0; `spin(red, -30)` should wrap to hue 330.
        out = compile_value('.a { c: spin(red, -30); }')
        # Hex of HSL(330, 100%, 50%) ≈ #ff0080.
        self.assertIn('#ff0080', out)

    def test_grayscale_aliases_greyscale(self) -> None:
        # `grayscale(red)` → desaturated.
        out = compile_value('.a { c: grayscale(red); }')
        self.assertIn('#808080', out)

    def test_multiply_wrong_arity_passthrough(self) -> None:
        # Blend functions registered via `_register_blend` raise on
        # arity mismatch → soft passthrough at value position.
        out = compile_value('.a { c: multiply(red); }')
        self.assertIn('multiply(', out)

    def test_screen_three_args_passthrough(self) -> None:
        out = compile_value('.a { c: screen(red, green, blue); }')
        self.assertIn('screen(', out)


class TestListsFunctions(unittest.TestCase):
    """`functions/lists.py` — extract, range."""

    def test_extract_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: extract(); }')
        self.assertIn('extract()', out)

    def test_extract_with_non_numeric_index_passthrough(self) -> None:
        out = compile_value('.a { c: extract(1 2 3, foo); }')
        self.assertIn('extract(', out)

    def test_range_too_many_args_passthrough(self) -> None:
        # `range()` only takes 1–3 args; 4 args triggers the soft fail.
        out = compile_value('.a { c: range(1, 2, 3, 4); }')
        # Returned as soft passthrough, no `range()` text in output.
        self.assertIn('c:', out)

    def test_range_non_numeric_arg_passthrough(self) -> None:
        out = compile_value('.a { c: range(red); }')
        self.assertIn('range(', out)

    def test_range_non_numeric_step_passthrough(self) -> None:
        out = compile_value('.a { c: range(1, 5, red); }')
        self.assertIn('range(', out)

    def test_range_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: range(); }')
        self.assertIn('range()', out)

    def test_range_two_non_numeric_passthrough(self) -> None:
        out = compile_value('.a { c: range(red, blue); }')
        self.assertIn('range(', out)


class TestMathFunctions(unittest.TestCase):
    """`functions/math.py` — round/floor/ceil/sin/asin/min/max/convert."""

    def test_asin_alias(self) -> None:
        out = compile_value('.a { c: asin(0.5); }')
        # asin(0.5) = π/6 ≈ 0.5236 radians.
        self.assertIn('0.523', out)
        self.assertIn('rad', out)

    def test_round_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: round(); }')
        self.assertIn('round(', out)

    def test_round_negative_value(self) -> None:
        # `round(-3.6)` — negation branch (`-` mirroring).
        out = compile_value('.a { c: round(-3.6); }')
        self.assertIn('-4', out)

    def test_floor_with_non_number_passthrough(self) -> None:
        out = compile_value('.a { c: floor(red); }')
        self.assertIn('floor(red)', out)

    def test_min_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: min(); }')
        self.assertIn('min(', out)

    def test_convert_wrong_count_passthrough(self) -> None:
        out = compile_value('.a { c: convert(1px); }')
        self.assertIn('convert(', out)

    def test_convert_non_unit_passthrough(self) -> None:
        out = compile_value('.a { c: convert(1px, 5); }')
        self.assertIn('convert(', out)

    def test_convert_same_unit_noop(self) -> None:
        out = compile_value('.a { c: convert(1px, px); }')
        self.assertIn('c: 1px', out)

    def test_convert_unconvertible_unit_returns_input(self) -> None:
        # `convert(1px, kg)` — units aren't compatible; `convert` returns
        # the input dimension unchanged.
        out = compile_value('.a { c: convert(1px, kg); }')
        self.assertIn('c: 1px', out)

    def test_percentage_on_non_dimension_raises_fatal(self) -> None:
        # `percentage(red)` — Color isn't a number; fatal ArgumentError.
        with self.assertRaises(ArgumentError) as cm:
            compile_value('.a { c: percentage(red); }')
        self.assertIn('must be a number', str(cm.exception))


class TestTypeCheckFunctions(unittest.TestCase):
    """`functions/type_check.py` — is*, unit, get-unit."""

    def test_isurl_true(self) -> None:
        out = compile_value('.a { c: isurl(url(x.png)); }')
        self.assertIn('c: true', out)

    def test_isunit_wrong_count_passthrough(self) -> None:
        out = compile_value('.a { c: isunit(1px); }')
        self.assertIn('isunit(', out)

    def test_isunit_with_quoted_unit_string(self) -> None:
        # `isunit(1px, "px")` — second arg is Quoted; converted via
        # `value_to_css` before comparison.
        out = compile_value('.a { c: isunit(1px, "px"); }')
        self.assertIn('true', out)

    def test_unit_three_args_passthrough(self) -> None:
        out = compile_value('.a { c: unit(1px, em, extra); }')
        # 3 args is invalid — soft passthrough.
        self.assertIn('c:', out)

    def test_unit_with_quoted_unit_string(self) -> None:
        # `unit(1, "em")` — second arg is Quoted.
        out = compile_value('.a { c: unit(1, "em"); }')
        self.assertIn('1em', out)

    def test_get_unit_on_non_dimension_passthrough(self) -> None:
        # Soft passthrough (the function bails with ArgumentError).
        out = compile_value('.a { c: get-unit(red); }')
        self.assertIn('get-unit(', out)

    def test_isunit_with_keyword_unit(self) -> None:
        # `isunit(1px, px)` — second arg is Keyword (not Quoted). The
        # function falls through to `value_to_css` for the unit text.
        out = compile_value('.a { c: isunit(1px, px); }')
        self.assertIn('c: true', out)

    def test_unit_with_keyword_unit_arg(self) -> None:
        # `unit(1, em)` — Keyword unit, non-Quoted branch.
        out = compile_value('.a { c: unit(1, em); }')
        self.assertIn('c: 1em', out)

    def test_unit_no_args_passthrough(self) -> None:
        out = compile_value('.a { c: unit(); }')
        self.assertIn('unit()', out)

    def test_isunit_with_dimension_unit_arg(self) -> None:
        # `isunit(1px, 1em)` — second arg is Dimension, not a Keyword
        # / Quoted / Anonymous. Falls through to `value_to_css` to
        # produce the comparison text.
        out = compile_value('.a { c: isunit(1px, 1em); }')
        self.assertIn('c: false', out)

    def test_unit_with_dimension_unit_arg(self) -> None:
        # `unit(1, 5)` — Dimension second arg → `value_to_css` path.
        out = compile_value('.a { c: unit(1, 5); }')
        self.assertIn('c: 15', out)


if __name__ == '__main__':
    unittest.main()
