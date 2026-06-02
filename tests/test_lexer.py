from __future__ import annotations

import unittest

from lessish.errors import ParseError
from lessish.lexer import Kind, Token, tokenize
from lessish.source import Source


def lex(text: str) -> list[Token]:
    return list(tokenize(Source(text=text)).tokens)


def kinds(text: str) -> list[Kind]:
    return [t.kind for t in lex(text) if t.kind is not Kind.EOF]


def texts(text: str) -> list[str]:
    return [t.text for t in lex(text) if t.kind is not Kind.EOF]


class TestBasicTokens(unittest.TestCase):
    def test_empty(self) -> None:
        toks = lex('')
        self.assertEqual(len(toks), 1)
        self.assertIs(toks[0].kind, Kind.EOF)

    def test_only_whitespace(self) -> None:
        toks = lex('   \n\t')
        self.assertEqual(len(toks), 1)
        self.assertIs(toks[0].kind, Kind.EOF)
        self.assertEqual(len(toks[0].leading_trivia), 1)
        self.assertIs(toks[0].leading_trivia[0].kind, Kind.WS)

    def test_braces_and_punctuation(self) -> None:
        self.assertEqual(
            kinds('{}();,:'),
            [
                Kind.LBRACE,
                Kind.RBRACE,
                Kind.LPAREN,
                Kind.RPAREN,
                Kind.SEMICOLON,
                Kind.COMMA,
                Kind.COLON,
            ],
        )

    def test_brackets(self) -> None:
        self.assertEqual(kinds('[]'), [Kind.LBRACKET, Kind.RBRACKET])

    def test_double_colon(self) -> None:
        self.assertEqual(kinds('::before'), [Kind.DOUBLECOLON, Kind.IDENT])

    def test_single_colon(self) -> None:
        self.assertEqual(kinds(':hover'), [Kind.COLON, Kind.IDENT])


class TestIdentifiers(unittest.TestCase):
    def test_simple_ident(self) -> None:
        self.assertEqual(kinds('foo'), [Kind.IDENT])
        self.assertEqual(texts('foo'), ['foo'])

    def test_kebab_ident(self) -> None:
        self.assertEqual(texts('font-size'), ['font-size'])

    def test_leading_dash_ident(self) -> None:
        self.assertEqual(texts('-webkit-foo'), ['-webkit-foo'])

    def test_double_dash_ident(self) -> None:
        self.assertEqual(texts('--my-var'), ['--my-var'])

    def test_underscore_ident(self) -> None:
        self.assertEqual(texts('_foo_bar'), ['_foo_bar'])

    def test_minus_then_ident_after_word(self) -> None:
        self.assertEqual(
            kinds('a-b - c'),
            [Kind.IDENT, Kind.MINUS, Kind.IDENT],
        )

    def test_number_minus_ident(self) -> None:
        self.assertEqual(
            kinds('1-foo'),
            [Kind.NUMBER, Kind.MINUS, Kind.IDENT],
        )


class TestAtAndDollarAndDotAndHash(unittest.TestCase):
    def test_at_variable(self) -> None:
        self.assertEqual(kinds('@foo'), [Kind.AT_NAME])
        self.assertEqual(texts('@foo'), ['@foo'])

    def test_at_at_variable(self) -> None:
        self.assertEqual(kinds('@@foo'), [Kind.AT_AT_NAME])

    def test_interp_open(self) -> None:
        self.assertEqual(
            kinds('@{name}'),
            [Kind.INTERP_OPEN, Kind.IDENT, Kind.RBRACE],
        )

    def test_dollar_property(self) -> None:
        self.assertEqual(kinds('$prop'), [Kind.DOLLAR_NAME])

    def test_dot_ident(self) -> None:
        self.assertEqual(kinds('.foo'), [Kind.DOT_IDENT])

    def test_hash_color_and_id(self) -> None:
        self.assertEqual(kinds('#fff'), [Kind.HASH])
        self.assertEqual(kinds('#abcdef'), [Kind.HASH])
        self.assertEqual(kinds('#my-id'), [Kind.HASH])

    def test_at_with_leading_dash(self) -> None:
        self.assertEqual(texts('@-webkit-thing'), ['@-webkit-thing'])


class TestNumbersAndDots(unittest.TestCase):
    def test_int(self) -> None:
        self.assertEqual(kinds('42'), [Kind.NUMBER])

    def test_float(self) -> None:
        self.assertEqual(texts('3.14'), ['3.14'])

    def test_dot_number_is_number_not_class(self) -> None:
        """Critical: .5 must lex as NUMBER, not DOT_IDENT."""
        self.assertEqual(kinds('.5'), [Kind.NUMBER])
        self.assertEqual(texts('.5'), ['.5'])

    def test_dot_class_with_letter_is_class(self) -> None:
        self.assertEqual(kinds('.class'), [Kind.DOT_IDENT])

    def test_number_with_unit(self) -> None:
        self.assertEqual(kinds('12px'), [Kind.NUMBER, Kind.IDENT])
        self.assertEqual(texts('12px'), ['12', 'px'])

    def test_dot_number_with_unit(self) -> None:
        self.assertEqual(kinds('.5em'), [Kind.NUMBER, Kind.IDENT])

    def test_percentage(self) -> None:
        self.assertEqual(kinds('50%'), [Kind.NUMBER, Kind.PERCENT])

    def test_scientific(self) -> None:
        self.assertEqual(texts('1e3'), ['1e3'])
        self.assertEqual(texts('1.5e-3'), ['1.5e-3'])


class TestStrings(unittest.TestCase):
    def test_double_quoted(self) -> None:
        self.assertEqual(kinds('"hello"'), [Kind.STRING])
        self.assertEqual(texts('"hello"'), ['"hello"'])

    def test_single_quoted(self) -> None:
        self.assertEqual(kinds("'hello'"), [Kind.STRING])

    def test_string_with_escape(self) -> None:
        self.assertEqual(texts(r'"a\"b"'), [r'"a\"b"'])

    def test_tilde_string(self) -> None:
        self.assertEqual(kinds('~"@{x}"'), [Kind.TILDE_STRING])
        self.assertEqual(texts('~"@{x}"'), ['~"@{x}"'])

    def test_tilde_string_single(self) -> None:
        self.assertEqual(kinds("~'foo'"), [Kind.TILDE_STRING])

    def test_string_with_newline(self) -> None:
        self.assertEqual(kinds('"line1\nline2"'), [Kind.STRING])


class TestComposites(unittest.TestCase):
    def test_important(self) -> None:
        self.assertEqual(kinds('!important'), [Kind.IMPORTANT])

    def test_important_with_spaces(self) -> None:
        self.assertEqual(kinds('! important'), [Kind.IMPORTANT])

    def test_extend_keyword(self) -> None:
        self.assertEqual(kinds(':extend'), [Kind.EXTEND_KEYWORD])

    def test_pseudo_class_is_not_extend(self) -> None:
        self.assertEqual(kinds(':hover'), [Kind.COLON, Kind.IDENT])

    def test_plus_colon(self) -> None:
        self.assertEqual(kinds('+:'), [Kind.PLUS_COLON])

    def test_plus_under_colon(self) -> None:
        self.assertEqual(kinds('+_:'), [Kind.PLUS_UNDER_COLON])

    def test_comparison_ops(self) -> None:
        self.assertEqual(
            kinds('<= >= <> != =< =>'),
            [
                Kind.LE,
                Kind.GE,
                Kind.NE,
                Kind.NE,
                Kind.LE,
                Kind.GE,
            ],
        )

    def test_simple_ops(self) -> None:
        self.assertEqual(
            kinds('+ - * / ~ | & = < >'),
            [
                Kind.PLUS,
                Kind.MINUS,
                Kind.STAR,
                Kind.SLASH,
                Kind.TILDE,
                Kind.PIPE,
                Kind.AMPERSAND,
                Kind.EQUALS,
                Kind.LT,
                Kind.GT,
            ],
        )


class TestCommentsAndTrivia(unittest.TestCase):
    def test_block_comment_is_trivia(self) -> None:
        toks = lex('/* hi */ foo')
        non_eof = [t for t in toks if t.kind is not Kind.EOF]
        self.assertEqual([t.kind for t in non_eof], [Kind.IDENT])
        self.assertEqual(non_eof[0].text, 'foo')
        trivia_kinds = [t.kind for t in non_eof[0].leading_trivia]
        self.assertIn(Kind.COMMENT_BLOCK, trivia_kinds)

    def test_block_comment_multiline(self) -> None:
        toks = lex('/* line1\nline2 */foo')
        idents = [t for t in toks if t.kind is Kind.IDENT]
        self.assertEqual(idents[0].text, 'foo')
        self.assertEqual(idents[0].leading_trivia[0].kind, Kind.COMMENT_BLOCK)

    def test_line_comment_is_trivia(self) -> None:
        toks = lex('// hi\nfoo')
        idents = [t for t in toks if t.kind is Kind.IDENT]
        self.assertEqual(len(idents), 1)
        self.assertEqual(idents[0].text, 'foo')

    def test_whitespace_is_trivia(self) -> None:
        toks = lex('  foo')
        idents = [t for t in toks if t.kind is Kind.IDENT]
        self.assertEqual(idents[0].leading_trivia[0].kind, Kind.WS)


class TestRealSnippets(unittest.TestCase):
    def test_simple_variable(self) -> None:
        self.assertEqual(
            kinds('@c: red;'),
            [Kind.AT_NAME, Kind.COLON, Kind.IDENT, Kind.SEMICOLON],
        )

    def test_simple_rule(self) -> None:
        self.assertEqual(
            kinds('.a { color: red; }'),
            [
                Kind.DOT_IDENT,
                Kind.LBRACE,
                Kind.IDENT,
                Kind.COLON,
                Kind.IDENT,
                Kind.SEMICOLON,
                Kind.RBRACE,
            ],
        )

    def test_nested_with_amp(self) -> None:
        self.assertEqual(
            kinds('.a { &:hover { color: red; } }'),
            [
                Kind.DOT_IDENT,
                Kind.LBRACE,
                Kind.AMPERSAND,
                Kind.COLON,
                Kind.IDENT,
                Kind.LBRACE,
                Kind.IDENT,
                Kind.COLON,
                Kind.IDENT,
                Kind.SEMICOLON,
                Kind.RBRACE,
                Kind.RBRACE,
            ],
        )

    def test_mixin_call_with_args(self) -> None:
        self.assertEqual(
            kinds('.mixin(10px, red);'),
            [
                Kind.DOT_IDENT,
                Kind.LPAREN,
                Kind.NUMBER,
                Kind.IDENT,
                Kind.COMMA,
                Kind.IDENT,
                Kind.RPAREN,
                Kind.SEMICOLON,
            ],
        )

    def test_interpolation_in_selector(self) -> None:
        # DOT_IDENT greedily consumes trailing `-`, so `.cls-@{name}` is one
        # DOT_IDENT (`.cls-`) followed by the interpolation. This matches less.js.
        self.assertEqual(
            kinds('.cls-@{name} { }'),
            [
                Kind.DOT_IDENT,
                Kind.INTERP_OPEN,
                Kind.IDENT,
                Kind.RBRACE,
                Kind.LBRACE,
                Kind.RBRACE,
            ],
        )
        self.assertEqual(texts('.cls-@{name}')[0], '.cls-')

    def test_bare_dot_before_interp(self) -> None:
        # `.@{x}` is a bare DOT (no leading ident chars) + interpolation.
        self.assertEqual(
            kinds('.@{x}'),
            [Kind.DOT, Kind.INTERP_OPEN, Kind.IDENT, Kind.RBRACE],
        )

    def test_bare_hash_before_paren(self) -> None:
        # `#(...)` — bare HASH followed by paren — destructuring form.
        self.assertEqual(
            kinds('#(a)'),
            [Kind.HASH_BARE, Kind.LPAREN, Kind.IDENT, Kind.RPAREN],
        )

    def test_bare_hash_before_interp(self) -> None:
        self.assertEqual(
            kinds('#@{x}'),
            [Kind.HASH_BARE, Kind.INTERP_OPEN, Kind.IDENT, Kind.RBRACE],
        )

    def test_dollar_interp(self) -> None:
        self.assertEqual(
            kinds('${prop}'),
            [Kind.DOLLAR_INTERP_OPEN, Kind.IDENT, Kind.RBRACE],
        )

    def test_bare_dollar(self) -> None:
        self.assertEqual(kinds('$@x'), [Kind.DOLLAR, Kind.AT_NAME])

    def test_ellipsis(self) -> None:
        self.assertEqual(kinds('(...)'), [Kind.LPAREN, Kind.ELLIPSIS, Kind.RPAREN])

    def test_variadic_after_var(self) -> None:
        self.assertEqual(kinds('@args...'), [Kind.AT_NAME, Kind.ELLIPSIS])

    def test_unicode_range(self) -> None:
        self.assertEqual(kinds('U+??????'), [Kind.UNICODE_RANGE])
        self.assertEqual(kinds('U+0-7F'), [Kind.UNICODE_RANGE])
        self.assertEqual(kinds('U+A5'), [Kind.UNICODE_RANGE])

    def test_css_escape_in_ident(self) -> None:
        self.assertEqual(kinds(r'.escape\|random'), [Kind.DOT_IDENT])
        self.assertEqual(texts(r'.escape\|random'), [r'.escape\|random'])

    def test_hex_escape_value(self) -> None:
        # @a0: \123; — \123 is a hex escape value, lexed as IDENT-like.
        self.assertEqual(kinds(r'\123'), [Kind.IDENT])

    def test_numeric_at_name(self) -> None:
        # Less.js fixtures use @1: ...; for special at-rule numbering.
        self.assertEqual(kinds('@1'), [Kind.AT_NAME])
        self.assertEqual(texts('@1'), ['@1'])

    def test_dash_at_name(self) -> None:
        # @-: x; — variable named "-"
        self.assertEqual(kinds('@-'), [Kind.AT_NAME])

    def test_backtick_string_tokenizes(self) -> None:
        # `` `42` `` is a less.js JavaScript-eval expression. lessish
        # has no JS runtime — but the *lexer* tokenizes it normally so
        # a custom parser / language-server / linter can intercept the
        # token. The default Parser raises `UnsupportedFeatureError`
        # when it reaches a backtick token (see
        # `test_parser.py::test_backtick_string_rejected_by_parser`).
        self.assertEqual(kinds('`42`'), [Kind.BACKTICK_STRING])

    def test_tilde_backtick_string_tokenizes(self) -> None:
        self.assertEqual(kinds('~`x + 1`'), [Kind.TILDE_BACKTICK_STRING])

    def test_caret_for_attribute_op(self) -> None:
        self.assertEqual(
            kinds('[a^="x"]'),
            [
                Kind.LBRACKET,
                Kind.IDENT,
                Kind.CARET,
                Kind.EQUALS,
                Kind.STRING,
                Kind.RBRACKET,
            ],
        )

    def test_extend(self) -> None:
        self.assertEqual(
            kinds('.a:extend(.b all)'),
            [
                Kind.DOT_IDENT,
                Kind.EXTEND_KEYWORD,
                Kind.LPAREN,
                Kind.DOT_IDENT,
                Kind.IDENT,
                Kind.RPAREN,
            ],
        )


class TestStreamApi(unittest.TestCase):
    def test_peek_does_not_advance(self) -> None:
        stream = tokenize(Source(text='.a'))
        first = stream.peek()
        self.assertIs(first.kind, Kind.DOT_IDENT)
        again = stream.peek()
        self.assertIs(again, first)

    def test_consume_advances(self) -> None:
        stream = tokenize(Source(text='.a .b'))
        first = stream.consume()
        self.assertEqual(first.text, '.a')
        second = stream.consume()
        self.assertEqual(second.text, '.b')

    def test_consume_at_eof_stays(self) -> None:
        stream = tokenize(Source(text=''))
        a = stream.consume()
        b = stream.consume()
        self.assertIs(a.kind, Kind.EOF)
        self.assertIs(b.kind, Kind.EOF)

    def test_match_succeeds_and_advances(self) -> None:
        stream = tokenize(Source(text='.a'))
        tok = stream.match(Kind.DOT_IDENT)
        self.assertIsNotNone(tok)
        self.assertIs(stream.peek().kind, Kind.EOF)

    def test_match_fails_no_advance(self) -> None:
        stream = tokenize(Source(text='.a'))
        tok = stream.match(Kind.IDENT)
        self.assertIsNone(tok)
        self.assertIs(stream.peek().kind, Kind.DOT_IDENT)

    def test_save_and_restore(self) -> None:
        stream = tokenize(Source(text='.a .b .c'))
        snap = stream.save()
        stream.consume()
        stream.consume()
        self.assertEqual(stream.peek().text, '.c')
        stream.restore(snap)
        self.assertEqual(stream.peek().text, '.a')

    def test_expect_raises_on_mismatch(self) -> None:
        stream = tokenize(Source(text='.a'))
        with self.assertRaises(ParseError):
            stream.expect(Kind.IDENT)

    def test_expect_succeeds(self) -> None:
        stream = tokenize(Source(text='.a'))
        tok = stream.expect(Kind.DOT_IDENT)
        self.assertEqual(tok.text, '.a')


class TestErrors(unittest.TestCase):
    def test_unrecognized_character_raises(self) -> None:
        with self.assertRaises(ParseError) as ctx:
            tokenize(Source(text='\x00'))
        err = ctx.exception
        assert err.location is not None
        self.assertEqual(err.location.line, 1)
        self.assertEqual(err.location.column, 1)

    def test_error_carries_snippet(self) -> None:
        with self.assertRaises(ParseError) as ctx:
            tokenize(Source(text='foo\n\x00', filename='x.less'))
        err = ctx.exception
        assert err.snippet is not None
        self.assertIn('^', err.snippet)


if __name__ == '__main__':
    unittest.main()
