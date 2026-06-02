from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum, auto

from .errors import ParseError
from .source import Source


class Kind(StrEnum):
    LBRACE = auto()
    RBRACE = auto()
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    SEMICOLON = auto()
    COMMA = auto()

    COLON = auto()
    DOUBLECOLON = auto()
    AMPERSAND = auto()
    EQUALS = auto()
    LT = auto()
    GT = auto()
    LE = auto()
    GE = auto()
    NE = auto()

    PLUS = auto()
    PLUS_COLON = auto()
    PLUS_UNDER_COLON = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    TILDE = auto()
    BANG = auto()
    PIPE = auto()

    DOT = auto()
    HASH_BARE = auto()
    DOLLAR = auto()
    ELLIPSIS = auto()
    CARET = auto()
    QUESTION = auto()

    IMPORTANT = auto()
    EXTEND_KEYWORD = auto()
    INTERP_OPEN = auto()
    DOLLAR_INTERP_OPEN = auto()

    IDENT = auto()
    AT_NAME = auto()
    AT_AT_NAME = auto()
    DOLLAR_NAME = auto()
    DOT_IDENT = auto()
    HASH = auto()
    UNICODE_RANGE = auto()

    NUMBER = auto()
    PERCENT = auto()
    STRING = auto()
    TILDE_STRING = auto()
    BACKTICK_STRING = auto()
    TILDE_BACKTICK_STRING = auto()

    COMMENT_BLOCK = auto()
    COMMENT_LINE = auto()
    WS = auto()

    EOF = auto()


_TRIVIA: frozenset[Kind] = frozenset({Kind.WS, Kind.COMMENT_BLOCK, Kind.COMMENT_LINE})


@dataclass(frozen=True, slots=True)
class Token:
    kind: Kind
    text: str
    index: int
    leading_trivia: tuple[Token, ...] = field(default=())

    def __repr__(self) -> str:
        return f'Token({self.kind.name}, {self.text!r}, @{self.index})'


# Patterns are combined into a single master regex (`_MASTER_RE`) with
# named groups so the tokenize loop needs ONE regex.match call per
# position. DOTALL (for COMMENT_BLOCK / strings) and MULTILINE (for
# COMMENT_LINE) are applied globally — safe because no other entry uses
# unescaped `.` or `^`/`$` anchors. Order matters: multi-char ops
# (`::`, `<=`, …) must precede single-char variants so alternation
# picks the longer form.
_PATTERN_SOURCES: tuple[tuple[Kind, str], ...] = (
    (Kind.WS, r'[ \t\r\n\f]+'),
    (Kind.COMMENT_BLOCK, r'/\*.*?\*/'),
    # `//` is a line comment ONLY at a statement boundary: start of source,
    # or directly after whitespace / `;` / `{` / `}`. Anywhere else (inside
    # `url(http://...)`, after `=`, within `a/b//c`, etc.) the slashes are
    # not comment markers. Without this guard the scanner would eat the
    # rest of the line in many URL and path contexts.
    (Kind.COMMENT_LINE, r'(?:^|(?<=[\s;{}]))//[^\n]*'),
    (Kind.IMPORTANT, r'!\s*important\b'),
    (Kind.EXTEND_KEYWORD, r':extend\b'),
    (Kind.INTERP_OPEN, r'@\{'),
    (Kind.DOLLAR_INTERP_OPEN, r'\$\{'),
    (Kind.ELLIPSIS, r'\.{3}'),
    (Kind.PLUS_UNDER_COLON, r'\+_:'),
    (Kind.PLUS_COLON, r'\+:'),
    (Kind.DOUBLECOLON, r'::'),
    # Two-char comparison ops first — order matters so `<=` doesn't lex
    # as LT followed by EQUALS. less.js also accepts `=<` / `=>` as
    # legacy spellings and `<>` for "not equal"; keep all three.
    (Kind.LE, r'<=|=<'),
    (Kind.GE, r'>=|=>'),
    (Kind.NE, r'<>|!='),
    (Kind.TILDE_BACKTICK_STRING, r'~`(?:\\.|[^`\\])*`'),
    (Kind.TILDE_STRING, r"""~"(?:\\.|[^"\\])*"|~'(?:\\.|[^'\\])*'"""),
    (Kind.BACKTICK_STRING, r'`(?:\\.|[^`\\])*`'),
    (Kind.STRING, r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*\'"""),
    (Kind.UNICODE_RANGE, r'[Uu]\+[0-9a-fA-F?]+(?:-[0-9a-fA-F]+)?'),
    (Kind.NUMBER, r'(?:\d+\.\d+|\.\d+|\d+)(?:[eE][+-]?\d+)?'),
    # CSS allows non-ASCII (>= 0x80) anywhere in idents and `\\X`
    # escapes for special chars. The alternation body covers both so
    # selectors like `.escape\|foo` and values like `symbols: ‣` lex
    # cleanly. `[\w-]` is Unicode-aware in Python re; `[^\x00-\x7f]`
    # picks up the rest.
    (Kind.AT_AT_NAME, r'@@(?:[\w-]|[^\x00-\x7f]|\\.)+'),
    (Kind.AT_NAME, r'@(?:[\w-]|[^\x00-\x7f]|\\.)+'),
    (Kind.DOLLAR_NAME, r'\$(?:[\w-]|[^\x00-\x7f]|\\.)+'),
    (Kind.HASH, r'#(?:[\w-]|[^\x00-\x7f]|\\.)+'),
    (Kind.DOT_IDENT, r'\.-{0,2}(?:[_a-zA-Z]|[^\x00-\x7f]|\\.)(?:[\w-]|[^\x00-\x7f]|\\.)*'),
    (
        Kind.IDENT,
        r'(?:(?<![\w-])-{1,2})?(?:[_a-zA-Z]|[^\x00-\x7f]|\\.)'
        r'(?:[\w-]|[^\x00-\x7f]|\\.)*',
    ),
    (Kind.PERCENT, r'%'),
    (Kind.LBRACE, r'\{'),
    (Kind.RBRACE, r'\}'),
    (Kind.LPAREN, r'\('),
    (Kind.RPAREN, r'\)'),
    (Kind.LBRACKET, r'\['),
    (Kind.RBRACKET, r'\]'),
    (Kind.SEMICOLON, r';'),
    (Kind.COMMA, r','),
    (Kind.COLON, r':'),
    (Kind.AMPERSAND, r'&'),
    (Kind.STAR, r'\*'),
    (Kind.PLUS, r'\+'),
    (Kind.MINUS, r'-'),
    (Kind.SLASH, r'/'),
    (Kind.TILDE, r'~'),
    (Kind.PIPE, r'\|'),
    (Kind.EQUALS, r'='),
    (Kind.LT, r'<'),
    (Kind.GT, r'>'),
    (Kind.BANG, r'!'),
    (Kind.DOT, r'\.'),
    (Kind.HASH_BARE, r'#'),
    (Kind.DOLLAR, r'\$'),
    (Kind.CARET, r'\^'),
    (Kind.QUESTION, r'\?'),
)


_NAME_TO_KIND: dict[str, Kind] = {kind.name: kind for kind, _ in _PATTERN_SOURCES}
_MASTER_RE: re.Pattern[str] = re.compile(
    '|'.join(f'(?P<{kind.name}>{src})' for kind, src in _PATTERN_SOURCES),
    re.DOTALL | re.MULTILINE,
)
_MASTER_MATCH = _MASTER_RE.match


class TokenStream:
    """Cursor over a tokenised source.

    Carries the originating `Source` (for error attribution) and an
    `pos` cursor the recursive-descent parser advances. Also supports
    list-style iteration / indexing / `len()` so callers that want to
    treat the tokenisation result as a flat sequence (`for tok in
    tokens:`, `tokens[0]`, `list(tokens)`) work unchanged — that's
    how `Lessish.tokenize()` ships it to tooling that doesn't care
    about the cursor.
    """

    __slots__ = ('tokens', 'source', 'pos', '_n')

    def __init__(self, tokens: list[Token], source: Source) -> None:
        self.tokens = tokens
        self.source = source
        self.pos = 0
        # Cached `len(tokens)` — `peek` runs millions of times on real
        # stylesheets and the `len()` call dominates its cost. The token
        # list is immutable after construction except for one in-place
        # splice (`_value_primary`'s inline retokenise), which calls
        # `_refresh_len()` to keep this in sync.
        self._n = len(tokens)

    def _refresh_len(self) -> None:
        """Re-sync the cached length after an in-place `tokens` edit."""
        self._n = len(self.tokens)

    def __iter__(self) -> Iterator[Token]:
        return iter(self.tokens)

    def __len__(self) -> int:
        return len(self.tokens)

    def __getitem__(self, idx: int) -> Token:
        return self.tokens[idx]

    def __contains__(self, item: object) -> bool:
        return item in self.tokens

    def peek(self, k: int = 0) -> Token:
        idx = self.pos + k
        if idx >= self._n:
            return self.tokens[-1]
        return self.tokens[idx]

    def peek_kind(self, k: int = 0) -> Kind:
        return self.peek(k).kind

    def consume(self) -> Token:
        tok = self.peek()
        if tok.kind is not Kind.EOF:
            self.pos += 1
        return tok

    def match(self, kind: Kind) -> Token | None:
        if self.peek().kind is kind:
            return self.consume()
        return None

    def expect(self, kind: Kind, message: str | None = None) -> Token:
        tok = self.peek()
        if tok.kind is not kind:
            msg = message or f'expected {kind.name} but got {tok.kind.name} ({tok.text!r})'
            raise ParseError(
                msg,
                location=self.source.location_at(tok.index),
                snippet=self.source.snippet_around(tok.index),
            )
        return self.consume()

    def save(self) -> int:
        return self.pos

    def restore(self, snap: int) -> None:
        self.pos = snap

    def at_end(self) -> bool:
        return self.peek().kind is Kind.EOF


def tokenize(source: Source) -> TokenStream:
    text = source.text
    pos = 0
    end = len(text)
    tokens: list[Token] = []
    trivia: list[Token] = []

    # Local aliases — every name lookup in the hot loop is paid per byte
    # of source. Pulling these into locals (CPython LOAD_FAST) gives a
    # measurable speedup on large stylesheets.
    master_match = _MASTER_MATCH
    name_to_kind = _NAME_TO_KIND
    trivia_kinds = _TRIVIA
    Token_ctor = Token
    eof_kind = Kind.EOF

    while pos < end:
        m = master_match(text, pos)
        if m is None:
            # An isolated `"`/`'` reaches this branch when the STRING
            # regex finds no closing quote — surface less.js's
            # `Expected '"'` / `Expected "'"` wording instead of the
            # generic "Unrecognized character".
            if text[pos] in ('"', "'"):
                err = ParseError(
                    f'Expected {text[pos]!r}',
                    location=source.location_at(pos),
                    snippet=source.snippet_around(pos),
                )
                raise err
            raise ParseError(
                f'Unrecognized character {text[pos]!r}',
                location=source.location_at(pos),
                snippet=source.snippet_around(pos),
            )

        kind = name_to_kind[m.lastgroup]  # type: ignore[index]
        tok_index = pos
        new_pos = m.end()
        # The combined regex has no zero-width alternatives, so a match
        # always advances. Guard anyway to avoid an infinite loop if a
        # future pattern is added that *could* match empty.
        if new_pos == pos:
            raise ParseError(
                f'Zero-width token at {pos}',
                location=source.location_at(pos),
                snippet=source.snippet_around(pos),
            )
        pos = new_pos

        # Backtick strings are less.js JS-eval expressions; lessish
        # doesn't run JS but the lexer still emits the tokens so a
        # subclass could intercept them. The "unsupported" diagnostic
        # lives in the parser (`_disallow_backtick`).

        if kind in trivia_kinds:
            trivia.append(Token_ctor(kind=kind, text=m.group(), index=tok_index))
            continue

        if trivia:
            tok = Token_ctor(
                kind=kind,
                text=m.group(),
                index=tok_index,
                leading_trivia=tuple(trivia),
            )
            trivia = []
        else:
            tok = Token_ctor(kind=kind, text=m.group(), index=tok_index)
        tokens.append(tok)

    tokens.append(
        Token_ctor(
            kind=eof_kind,
            text='',
            index=end,
            leading_trivia=tuple(trivia),
        )
    )
    return TokenStream(tokens, source)
