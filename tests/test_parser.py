from __future__ import annotations

import unittest

from lessish.ast_nodes import (
    Anonymous,
    AtRule,
    Declaration,
    MixinCallStatement,
    Ruleset,
)
from lessish.errors import ParseError, UnsupportedFeatureError
from lessish.parser import parse
from lessish.source import Source


def parse_text(text: str) -> Ruleset:
    return parse(Source(text=text))


class TestEmpty(unittest.TestCase):
    def test_empty(self) -> None:
        root = parse_text('')
        self.assertTrue(root.root)
        self.assertEqual(root.rules, [])

    def test_only_whitespace(self) -> None:
        root = parse_text('   \n\n  ')
        self.assertEqual(root.rules, [])

    def test_only_comment(self) -> None:
        # Block comments now produce Comment AST nodes so emit can
        # preserve them. Line comments still drop.
        root = parse_text('/* hello */')
        self.assertEqual(len(root.rules), 1)
        node = root.rules[0]
        from lessish.ast_nodes import Comment

        assert isinstance(node, Comment)
        self.assertEqual(node.text, '/* hello */')
        self.assertFalse(node.silent)

        root = parse_text('// silent\n')
        self.assertEqual(root.rules, [])

    def test_stray_semicolons(self) -> None:
        root = parse_text(';;;')
        self.assertEqual(root.rules, [])


class TestDeclarations(unittest.TestCase):
    def test_simple_declaration(self) -> None:
        root = parse_text('color: red;')
        self.assertEqual(len(root.rules), 1)
        decl = root.rules[0]
        self.assertIsInstance(decl, Declaration)
        assert isinstance(decl, Declaration)
        self.assertEqual(decl.name, 'color')
        assert isinstance(decl.value, Anonymous)
        self.assertEqual(decl.value.value, 'red')
        self.assertFalse(decl.important)
        self.assertFalse(decl.variable)

    def test_declaration_without_trailing_semicolon(self) -> None:
        # Valid at end of file / block — last decl can drop `;`. Value
        # text now keeps trailing whitespace verbatim so emit can round-
        # trip declarations like `color: red ;` faithfully; the parser
        # only left-strips.
        root = parse_text('.a { color: red }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        decl = rs.rules[0]
        assert isinstance(decl, Declaration)
        assert isinstance(decl.value, Anonymous)
        self.assertEqual(decl.value.value.rstrip(), 'red')

    def test_declaration_with_important(self) -> None:
        root = parse_text('color: red !important;')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        self.assertTrue(decl.important)
        assert isinstance(decl.value, Anonymous)
        self.assertEqual(decl.value.value, 'red')

    def test_variable_declaration(self) -> None:
        root = parse_text('@brand: #f0f;')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        self.assertTrue(decl.variable)
        self.assertEqual(decl.name, '@brand')
        assert isinstance(decl.value, Anonymous)
        self.assertEqual(decl.value.value, '#f0f')

    def test_kebab_property_name(self) -> None:
        root = parse_text('background-color: blue;')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        self.assertEqual(decl.name, 'background-color')

    def test_value_with_function(self) -> None:
        root = parse_text('background: url(/a.png) no-repeat;')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        assert isinstance(decl.value, Anonymous)
        self.assertEqual(decl.value.value, 'url(/a.png) no-repeat')

    def test_value_with_commas_and_parens(self) -> None:
        root = parse_text('color: rgba(255, 255, 255, 0.5);')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        assert isinstance(decl.value, Anonymous)
        self.assertEqual(decl.value.value, 'rgba(255, 255, 255, 0.5)')

    def test_property_name_interpolation(self) -> None:
        root = parse_text('@{prefix}-color: red;')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        self.assertEqual(decl.name, '@{prefix}-color')

    def test_merge_comma(self) -> None:
        root = parse_text('transform+: scale(2);')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        self.assertEqual(decl.merge, '+')
        self.assertEqual(decl.name, 'transform')

    def test_merge_space(self) -> None:
        root = parse_text('transform+_: scale(2);')
        decl = root.rules[0]
        assert isinstance(decl, Declaration)
        self.assertEqual(decl.merge, '+_')


class TestRulesets(unittest.TestCase):
    def test_simple_rule(self) -> None:
        root = parse_text('.a { color: red; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        self.assertEqual(len(rs.selectors), 1)
        self.assertEqual(rs.selectors[0].elements[0].value, '.a')
        decl = rs.rules[0]
        assert isinstance(decl, Declaration)
        self.assertEqual(decl.name, 'color')

    def test_multiple_selectors(self) -> None:
        root = parse_text('.a, .b { color: red; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        self.assertEqual(len(rs.selectors), 2)
        self.assertEqual(rs.selectors[0].elements[0].value, '.a')
        self.assertEqual(rs.selectors[1].elements[0].value, '.b')

    def test_compound_selector(self) -> None:
        root = parse_text('a.foo#bar { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual([e.value for e in sel.elements], ['a', '.foo', '#bar'])
        self.assertEqual([e.combinator for e in sel.elements], ['', '', ''])

    def test_descendant_combinator(self) -> None:
        root = parse_text('.a .b { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual([e.value for e in sel.elements], ['.a', '.b'])
        self.assertEqual([e.combinator for e in sel.elements], ['', ' '])

    def test_child_combinator(self) -> None:
        root = parse_text('.a > .b { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual([e.combinator for e in sel.elements], ['', '>'])

    def test_adjacent_and_general_sibling(self) -> None:
        root = parse_text('.a + .b ~ .c { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual([e.combinator for e in sel.elements], ['', '+', '~'])

    def test_parent_selector_amp(self) -> None:
        root = parse_text('.a { &:hover { x: 1; } }')
        outer = root.rules[0]
        assert isinstance(outer, Ruleset)
        inner = outer.rules[0]
        assert isinstance(inner, Ruleset)
        sel = inner.selectors[0]
        self.assertEqual([e.value for e in sel.elements], ['&', ':hover'])

    def test_pseudo_with_args(self) -> None:
        root = parse_text(':nth-child(2n+1) { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual(sel.elements[0].value, ':nth-child(2n+1)')

    def test_pseudo_with_complex_args(self) -> None:
        root = parse_text('a:is(.b, .c) { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual([e.value for e in sel.elements], ['a', ':is(.b, .c)'])

    def test_double_colon_pseudo(self) -> None:
        root = parse_text('a::before { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual([e.value for e in sel.elements], ['a', '::before'])

    def test_attribute_selector(self) -> None:
        root = parse_text('a[href^="https"] { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        self.assertEqual(sel.elements[1].value, '[href^="https"]')

    def test_interpolated_selector(self) -> None:
        root = parse_text('.@{name} { x: 1; }')
        rs = root.rules[0]
        assert isinstance(rs, Ruleset)
        sel = rs.selectors[0]
        # `.` is a bare DOT (no ident chars after), then `@{name}` interpolation.
        # Both end up as separate elements with combinator='' (compound).
        self.assertEqual([e.value for e in sel.elements], ['.', '@{name}'])

    def test_nested_ruleset(self) -> None:
        root = parse_text('.a { color: red; .b { font: bold; } }')
        outer = root.rules[0]
        assert isinstance(outer, Ruleset)
        self.assertEqual(len(outer.rules), 2)
        decl = outer.rules[0]
        inner = outer.rules[1]
        assert isinstance(decl, Declaration)
        assert isinstance(inner, Ruleset)
        self.assertEqual(decl.name, 'color')
        self.assertEqual(inner.selectors[0].elements[0].value, '.b')

    def test_keyframe_percent_selector(self) -> None:
        root = parse_text('@keyframes x { 50% { x: 1; } }')
        at = root.rules[0]
        assert isinstance(at, AtRule)
        assert at.body is not None
        kf = at.body[0]
        assert isinstance(kf, Ruleset)
        self.assertEqual(kf.selectors[0].elements[0].value, '50%')


class TestAtRules(unittest.TestCase):
    def test_charset(self) -> None:
        root = parse_text("@charset 'utf-8';")
        at = root.rules[0]
        assert isinstance(at, AtRule)
        self.assertEqual(at.name, '@charset')
        self.assertEqual(at.prelude, "'utf-8'")
        self.assertIsNone(at.body)

    def test_import_statement(self) -> None:
        root = parse_text('@import "x.less";')
        at = root.rules[0]
        assert isinstance(at, AtRule)
        self.assertEqual(at.name, '@import')
        self.assertEqual(at.prelude, '"x.less"')
        self.assertIsNone(at.body)

    def test_media_block(self) -> None:
        root = parse_text('@media (min-width: 600px) { .a { x: 1; } }')
        at = root.rules[0]
        assert isinstance(at, AtRule)
        self.assertEqual(at.name, '@media')
        self.assertEqual(at.prelude, '(min-width: 600px)')
        assert at.body is not None
        self.assertEqual(len(at.body), 1)
        inner = at.body[0]
        assert isinstance(inner, Ruleset)
        self.assertEqual(inner.selectors[0].elements[0].value, '.a')

    def test_font_face(self) -> None:
        root = parse_text('@font-face { font-family: x; src: url(a); }')
        at = root.rules[0]
        assert isinstance(at, AtRule)
        self.assertEqual(at.name, '@font-face')
        assert at.body is not None
        self.assertEqual(len(at.body), 2)

    def test_vendor_keyframes(self) -> None:
        root = parse_text('@-webkit-keyframes x { 0% { } }')
        at = root.rules[0]
        assert isinstance(at, AtRule)
        self.assertEqual(at.name, '@-webkit-keyframes')


class TestStatements(unittest.TestCase):
    def test_mixin_call_with_args(self) -> None:
        root = parse_text('.mixin(10px, red);')
        stmt = root.rules[0]
        assert isinstance(stmt, MixinCallStatement)
        self.assertEqual(stmt.text, '.mixin(10px, red)')

    def test_paren_less_mixin_call(self) -> None:
        root = parse_text('.mixin;')
        stmt = root.rules[0]
        assert isinstance(stmt, MixinCallStatement)
        self.assertEqual(stmt.text, '.mixin')


class TestErrors(unittest.TestCase):
    def test_unclosed_ruleset(self) -> None:
        with self.assertRaises(ParseError):
            parse_text('.a { color: red;')

    def test_unmatched_paren_in_pseudo(self) -> None:
        with self.assertRaises(ParseError):
            parse_text(':nth-child(2 { x: 1; }')

    def test_backtick_string_rejected_by_parser(self) -> None:
        # The lexer tokenizes JS backticks as `BACKTICK_STRING` /
        # `TILDE_BACKTICK_STRING` (see `test_lexer.py`) — the *value-
        # position parser* is what rejects them. A custom Parser
        # subclass can override `_reject_backtick` to handle the
        # construct (e.g. surface as a warning, or implement a
        # sandboxed JS shim). The structural `parse()` keeps value
        # text opaque; `compile()` is where value-position parsing
        # runs, so that's the entry point that surfaces the error.
        from lessish import Lessish

        with self.assertRaises(UnsupportedFeatureError) as ctx:
            Lessish().compile('.a { color: `42`; }')
        self.assertIn('backtick', ctx.exception.message)

    def test_tilde_backtick_string_rejected_by_parser(self) -> None:
        from lessish import Lessish

        with self.assertRaises(UnsupportedFeatureError):
            Lessish().compile(".a { x: ~`'foo'`; }")

    def test_media_without_body_or_semicolon_raises(self) -> None:
        # `@media` with no prelude and no `{...}` body — less.js
        # escalates to "media definitions require block statements".
        with self.assertRaises(ParseError) as cm:
            parse_text('@media')
        self.assertIn('require block statements', str(cm.exception))

    def test_media_with_prelude_missing_body_raises(self) -> None:
        with self.assertRaises(ParseError) as cm:
            parse_text('@media all and (max-width: 100px) { .x { c: 1 }')
        self.assertIn('block statements', str(cm.exception))

    def test_supports_missing_body_raises(self) -> None:
        with self.assertRaises(ParseError):
            parse_text('@supports (a: b) { .x { c: 1; }')

    def test_media_stray_close_paren_in_prelude(self) -> None:
        with self.assertRaises(ParseError):
            parse_text('@media a) { .x { c: 1; } }')

    def test_media_lbrace_inside_feature_paren(self) -> None:
        # `{` inside a feature paren (innermost paren not a function
        # call) raises "Missing closing ')'".
        with self.assertRaises(ParseError) as cm:
            parse_text('@media (a: { x ) { .x { c: 1; } }')
        self.assertIn("Missing closing ')'", str(cm.exception))

    def test_media_unclosed_paren_at_eof(self) -> None:
        with self.assertRaises(ParseError) as cm:
            parse_text('@media (a')
        self.assertIn("expected ')'", str(cm.exception))

    def test_import_without_semicolon_raises(self) -> None:
        with self.assertRaises(ParseError) as cm:
            parse_text('@import "x.less"')
        self.assertIn('missing semi-colon', str(cm.exception))

    def test_extend_not_at_selector_end_raises(self) -> None:
        with self.assertRaises(ParseError) as cm:
            parse_text('.a:extend(.b).other { color: red; }')
        self.assertIn('Extend can only be used at the end of selector', str(cm.exception))

    def test_extend_followed_by_when_guard(self) -> None:
        # `.a:extend(.b):extend(.c) when (true)` — `when` peels off
        # cleanly and the multiple extends are collected.
        root = parse_text('.a:extend(.b):extend(.c) when (true) { color: red; }')
        self.assertIsInstance(root, Ruleset)

    def test_unmatched_interpolation_open_raises(self) -> None:
        with self.assertRaises(ParseError):
            parse_text('.a { color: @{ }')

    def test_standalone_extend_without_selector_raises(self) -> None:
        # `:extend(.b)` with no preceding selector — less.js wording.
        with self.assertRaises(ParseError) as cm:
            parse_text(':extend(.b) { color: red; }')
        self.assertIn('Extend must be used to extend a selector', str(cm.exception))

    def test_mixin_guard_bare_ident_raises(self) -> None:
        # A guard that isn't `(cond)` / `not (cond)` raises "expected
        # condition" at the mixin-call site.
        from lessish import Lessish

        with self.assertRaises(ParseError) as cm:
            Lessish().compile('.m() when foo { c: 1; } .a { .m(); }')
        self.assertIn('expected condition', str(cm.exception))

    def test_mixin_guard_top_level_and_chain(self) -> None:
        # `when (a) and (b)` — top-level AND-chain between clauses.
        from lessish import Lessish

        out = Lessish().compile('.m() when (1=1) and (2=2) { c: ok; } .a { .m(); }')
        self.assertIn('c: ok;', out)

    def test_mixin_guard_or_then_and_chain(self) -> None:
        # `when (a), (b) and (c)` — `,` is OR, then `and` chain on rhs.
        from lessish import Lessish

        out = Lessish().compile('.m() when (1=2), (3=3) and (4=4) { c: ok; } .a { .m(); }')
        self.assertIn('c: ok;', out)

    def test_parse_guard_text_direct(self) -> None:
        # Direct call to the public `parse_guard_text` for a non-trivial
        # condition tree.
        from lessish.parser import parse_guard_text

        tree = parse_guard_text('(1 = 1) and (2 = 2)')
        self.assertIsNotNone(tree)

    def test_parse_guard_text_invalid_raises(self) -> None:
        from lessish.parser import parse_guard_text

        with self.assertRaises(ParseError):
            parse_guard_text('foo bar')

    def test_stray_close_brace_at_root_raises(self) -> None:
        # `}` with no matching `{` at root level.
        with self.assertRaises(ParseError) as cm:
            parse_text('}')
        self.assertIn("missing opening '{'", str(cm.exception))

    def test_pseudo_class_with_paren_is_selector(self) -> None:
        # `.a:nth-child(2n+1) { ... }` — the colon + paren shape is a
        # CSS pseudo-class, not a declaration.
        root = parse_text('.a:nth-child(2n+1) { color: red; }')
        self.assertIsInstance(root, Ruleset)

    def test_block_in_value_position_raises(self) -> None:
        # `c: { nested }` at the value position is a parse error — the
        # block-lookahead bails because the `{` isn't a valid value.
        with self.assertRaises(ParseError):
            parse_text('.a { c: { nested } ; }')

    def test_ie_filter_progid_value(self) -> None:
        # `filter: progid:DXImageTransform...` is an IE-specific shape
        # that bypasses normal value parsing and survives as Anonymous
        # text.
        root = parse_text('.a { filter: progid:DXImageTransform.Microsoft.Alpha(opacity=50); }')
        self.assertIsInstance(root, Ruleset)

    def test_value_text_with_operations(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('1 + 2 * 3')
        self.assertIsNotNone(v)

    def test_value_text_with_nested_parens(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('1 + (2 + 3)')
        self.assertIsNotNone(v)

    def test_value_text_with_unary_minus(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('-1')
        self.assertIsNotNone(v)

    def test_value_text_paren_with_top_colon_kept_as_anonymous(self) -> None:
        # `(min-width: 600px)` — top-level `:` inside parens signals
        # CSS media-query feature syntax. The arithmetic-expression
        # parser bails and the paren contents stay as Anonymous text.
        from lessish.parser import parse_value_text

        v = parse_value_text('(min-width: 600px)')
        self.assertIsNotNone(v)

    def test_url_with_nested_parens_in_path(self) -> None:
        # `url(((nested)))` — the URL token consumer balances parens
        # so the nested ones don't terminate the URL prematurely.
        root = parse_text('.a { background: url((nested)); }')
        self.assertIsInstance(root, Ruleset)

    def test_tilde_paren_list_with_empty_piece(self) -> None:
        # `~(1; ; 2)` — an empty semicolon-separated piece is skipped
        # by the list builder.
        from lessish import Lessish

        out = Lessish().compile('.a { c: ~(1; ; 2); }')
        self.assertIn('.a {', out)

    def test_tilde_paren_list_with_multi_expression_piece(self) -> None:
        # `~(1, 2; 3, 4)` — each piece is itself a comma-list, so the
        # builder wraps it in an Expression wrapper.
        from lessish import Lessish

        out = Lessish().compile('.a { c: ~(1, 2; 3, 4); }')
        self.assertIn('.a {', out)

    def test_star_as_sole_property_name_raises(self) -> None:
        with self.assertRaises(ParseError) as cm:
            parse_text('.a { *: x; }')
        self.assertIn('Unrecognised input', str(cm.exception))

    def test_detached_ruleset_as_css_property_value_raises(self) -> None:
        # `color: { x: 1; };` — DR literal isn't a valid CSS value.
        with self.assertRaises(ParseError):
            parse_text('.a { color: { x: 1; }; }')

    def test_variable_assigned_detached_ruleset(self) -> None:
        # Variable assignment with DR value parses fine.
        root = parse_text('.a { @x: { y: 1; }; }')
        self.assertIsInstance(root, Ruleset)

    def test_custom_property_with_braces(self) -> None:
        # `--x: { y: 1 };` — custom properties accept braces in value.
        root = parse_text('.a { --x: { y: 1 }; }')
        self.assertIsInstance(root, Ruleset)

    def test_nested_interpolation_in_property_name(self) -> None:
        # `@{@{inner}}` — nested INTERP_OPEN bumps the depth counter.
        root = parse_text('.a { @{@{inner}}: red; }')
        self.assertIsInstance(root, Ruleset)

    def test_unclosed_interpolation_in_property_name(self) -> None:
        # EOF mid-`@{...}` interpolation in name position bails (decl
        # restore).
        with self.assertRaises(ParseError):
            parse_text('.a { @{unclosed')

    def test_trailing_comma_in_selector_list_raises(self) -> None:
        with self.assertRaises(ParseError):
            parse_text('.a, { c: 1; }')

    def test_leading_comma_at_root_raises(self) -> None:
        with self.assertRaises(ParseError):
            parse_text(', .a { c: 1; }')

    def test_value_text_empty(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('')
        self.assertEqual(v.expressions, [])

    def test_value_text_space_separated_numbers(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('1 1 1')
        self.assertIsNotNone(v)

    def test_value_text_comma_separated(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('1, 2')
        # Comma split becomes multiple Expressions.
        self.assertEqual(len(v.expressions), 2)

    def test_value_text_lookup_after_variable(self) -> None:
        from lessish.parser import parse_value_text

        v = parse_value_text('@x[a]')
        self.assertIsNotNone(v)

    def test_value_text_paren_with_top_colon_invalid(self) -> None:
        # `(1, 2)` — top-level `,` makes it NOT a media-feature paren,
        # and the arithmetic expression parser bails on the comma.
        from lessish.parser import parse_value_text

        with self.assertRaises(ParseError):
            parse_value_text('(1, 2)')

    def test_empty_detached_ruleset_value(self) -> None:
        root = parse_text('@x: {};')
        self.assertIsInstance(root, Ruleset)

    def test_detached_ruleset_with_only_whitespace(self) -> None:
        root = parse_text('@x: { };')
        self.assertIsInstance(root, Ruleset)

    def test_value_position_namespace_mixin_call(self) -> None:
        # `.box.fn(arg)` in value position — exercises the namespace-
        # path parser.
        from lessish.parser import parse_value_text

        v = parse_value_text('.box.fn(arg)')
        self.assertIsNotNone(v)

    def test_parse_guard_text_top_level_and_chain(self) -> None:
        from lessish.parser import parse_guard_text

        cond = parse_guard_text('(1=1) and (2=2)')
        self.assertEqual(cond.op, 'and')

    def test_parse_guard_text_comma_then_and(self) -> None:
        # `(a), (b) and (c)` — `,` is OR, then `and` binds the rhs.
        from lessish.parser import parse_guard_text

        cond = parse_guard_text('(1=1), (2=2) and (3=3)')
        self.assertEqual(cond.op, 'or')

    def test_value_text_paren_with_comparison_unfinished(self) -> None:
        # `(1 < )` — open paren, comparison-like token, then RPAREN
        # with no rhs. The expression parser's break path advances
        # cleanly to the closing `)`.
        from lessish.parser import parse_value_text

        v = parse_value_text('(1 < )')
        self.assertIsNotNone(v)

    def test_custom_property_with_nested_braces(self) -> None:
        # `--x: { foo { nested } };` — block-lookahead bumps depth for
        # nested `{` so the whole expression rides through as a
        # declaration.
        root = parse_text('.a { --x: { foo { nested } }; }')
        self.assertIsInstance(root, Ruleset)

    def test_custom_property_with_unclosed_brace_at_eof(self) -> None:
        # Block-lookahead bails to `'declaration'` when it hits EOF
        # while still inside an open `{` of a custom-property value.
        with self.assertRaises(ParseError):
            parse_text('.a { --x: { foo')

    def test_at_page_with_pseudo_prelude(self) -> None:
        # `@page :left { ... }` — the leading `:` is a pseudo-class
        # marker inside the at-rule's prelude, not a declaration colon.
        root = parse_text('@page :left { margin: 1px; }')
        self.assertIsInstance(root, Ruleset)

    def test_value_with_rparen_inside_bracket(self) -> None:
        # `[1 ) 2]` — RPAREN encountered while bracket counter is open;
        # tracker records the position for the bracket-mismatch error.
        root = parse_text('.a { c: [1 ) 2]; }')
        self.assertIsInstance(root, Ruleset)

    def test_value_with_rbrace_inside_bracket(self) -> None:
        # `[1 } 2]` — same tracking for RBRACE.
        root = parse_text('.a { c: [1 } 2]; }')
        self.assertIsInstance(root, Ruleset)

    def test_value_with_unclosed_bracket_raises(self) -> None:
        # `c: red[a;` — bracket never closes before `;`.
        with self.assertRaises(ParseError) as cm:
            parse_text('.a { c: red[a; }')
        self.assertIn("Expected ']'", str(cm.exception))

    def test_value_with_important_no_semicolon(self) -> None:
        # `red!important }` — `}` terminator, not `;`. The
        # important-prefix-ws path drops to a single space.
        root = parse_text('.a { c: red!important }')
        self.assertIsInstance(root, Ruleset)

    def test_non_ie_filter_value(self) -> None:
        # `filter: blur(5px)` — standard CSS filter (not progid). The
        # IE-filter peek returns False; normal value parsing.
        root = parse_text('.a { filter: blur(5px); }')
        self.assertIsInstance(root, Ruleset)

    def test_value_text_empty_paren_raises(self) -> None:
        # `()` — empty paren expression. less.js's expression parser
        # rejects this as `Expected ')'`.
        from lessish.parser import parse_value_text

        with self.assertRaises(ParseError):
            parse_value_text('()')

    def test_value_text_paren_with_arithmetic(self) -> None:
        # `(1 + 2)` — no top-level colon, so the arithmetic parser
        # builds an Operation tree.
        from lessish.parser import parse_value_text

        v = parse_value_text('(1 + 2)')
        self.assertIsNotNone(v)

    def test_arrow_function_value_in_variable(self) -> None:
        root = parse_text('@x: () => { y: 1; };')
        self.assertIsInstance(root, Ruleset)

    def test_anonymous_mixin_with_params_in_variable(self) -> None:
        root = parse_text('@x: .m(@a, @b) { y: 1; };')
        self.assertIsInstance(root, Ruleset)

    def test_url_with_nested_empty_parens(self) -> None:
        # `url(())` — nested `(` bumps depth, matching `)` doesn't
        # terminate the URL prematurely.
        root = parse_text('.a { background: url(()); }')
        self.assertIsInstance(root, Ruleset)

    def test_extend_with_combinator_selector(self) -> None:
        root = parse_text('.a:extend(.b > .c) { color: red; }')
        self.assertIsInstance(root, Ruleset)

    def test_extend_with_multiple_selectors_and_all(self) -> None:
        root = parse_text('.a:extend(.b, .c all) { color: red; }')
        self.assertIsInstance(root, Ruleset)

    def test_block_comment_skipped_around_string_with_escape(self) -> None:
        # `"abc\""` — the comment stripper walks past backslash-escape
        # pairs inside strings without confusing the closing quote.
        root = parse_text(r'.a { c: "abc\""; /* comment */ }')
        self.assertIsInstance(root, Ruleset)

    def test_parse_extend_args_empty(self) -> None:
        from lessish.parser import _parse_extend_args

        self.assertEqual(_parse_extend_args('', base_index=0), [])

    def test_parse_extend_args_all_alone(self) -> None:
        # `:extend(all)` — `all` with no preceding target is dropped.
        from lessish.parser import _parse_extend_args

        self.assertEqual(_parse_extend_args('all', base_index=0), [])

    def test_parse_extend_args_trailing_comma(self) -> None:
        # `:extend(.foo,)` — trailing empty piece is skipped.
        from lessish.parser import _parse_extend_args

        result = _parse_extend_args('.foo,', base_index=0)
        self.assertEqual(len(result), 1)

    def test_split_top_level_commas_skips_inside_parens(self) -> None:
        from lessish.parser import _split_top_level_commas

        self.assertEqual(
            _split_top_level_commas('foo(a, b), bar'),
            ['foo(a, b)', ' bar'],
        )

    def test_variable_call_with_bracket_lookup(self) -> None:
        # `@var()[key]` — variable used as a callable with a `[lookup]`
        # postfix. The parser emits a Lookup node whose target is the
        # Variable (no MixinCall wrapper), and eval handles the call.
        from lessish.parser import parse_value_text

        v = parse_value_text('@x()[k]')
        self.assertIsNotNone(v)


class TestStructuralIntegrity(unittest.TestCase):
    """Mixed cases that combine several features. Smoke-checks that the
    parser builds something reasonable rather than asserting deep structure.
    """

    def test_nested_with_at_media(self) -> None:
        root = parse_text('.outer { color: red; @media print { padding: 0; } }')
        outer = root.rules[0]
        assert isinstance(outer, Ruleset)
        self.assertEqual(len(outer.rules), 2)
        self.assertIsInstance(outer.rules[0], Declaration)
        self.assertIsInstance(outer.rules[1], AtRule)

    def test_top_level_variables_and_rule(self) -> None:
        root = parse_text('@a: 10px;\n@b: 20px;\n.x { width: @a; height: @b; }\n')
        self.assertEqual(len(root.rules), 3)
        v1, v2, r = root.rules
        assert isinstance(v1, Declaration)
        assert isinstance(v2, Declaration)
        assert isinstance(r, Ruleset)
        self.assertTrue(v1.variable)
        self.assertTrue(v2.variable)


if __name__ == '__main__':
    unittest.main()
