"""Value-position call / namespace / lookup / url parser mixin.

Methods that handle the `name(args)` shape at value position
(function calls, namespace invocations, mixin value-calls),
the `[key]` lookup postfix, and the `url(...)` literal.

Depends on `_UtilsMethods` (`_has_ws_before`, `_read_balanced`,
`_error`) and `_ValueExprMethods` (`_parse_expression`) — accessed
via `self`.
"""

from __future__ import annotations

from ..ast_nodes import (
    Anonymous,
    Call,
    Expression,
    Lookup,
    MixinArg,
    MixinCall,
    Node,
    Url,
)
from ..lexer import Kind, Token
from ._state import _ParserState


class _CallsMethods(_ParserState):
    """Function / namespace / lookup / url parsers at value position."""

    def _parse_call_args(self, start_index: int, name: str) -> Call:
        """Consume `(arg1, arg2, ...)` for a function call. The opening
        `(` is still in the stream; this routine handles it through to
        the closing `)`.
        """
        self.stream.consume()  # LPAREN
        args: list[Expression] = []
        first = self._parse_call_arg()
        if first is not None:
            args.append(first)
            while self.stream.match(Kind.COMMA):
                expr = self._parse_call_arg()
                if expr is not None:
                    args.append(expr)
        self.stream.match(Kind.RPAREN)
        return Call(index=start_index, name=name, args=args)

    def _parse_call_arg(self) -> Expression | None:
        """Parse one function-call argument. Mostly delegates to
        `_parse_expression`; the special case is a bare `%` token in
        argument position, which less.js accepts as a literal unit
        keyword (`unit(100, %)`, `replace(str, %)`). Everywhere else
        bare `%` is `Invalid % without number`, so we don't relax the
        general value-atom parser — only the function-arg slot.
        """
        nt = self.stream.peek()
        if nt.kind is Kind.PERCENT and self.stream.peek_kind(1) in (
            Kind.COMMA,
            Kind.RPAREN,
        ):
            self.stream.consume()
            anon = Anonymous(index=nt.index, value='%')
            return Expression(index=nt.index, values=[anon])
        return self._parse_expression()

    def _parse_namespace_path_or_call(self, start_tok: Token) -> MixinCall:
        """Parse a value-position mixin/namespace reference.

        Forms recognized (no whitespace between segments):
          `.foo` / `#foo`            — path with one segment, no call
          `#a.b.c` / `#a#b.c`        — path with multiple segments
          `<path>(args)`             — invocation with arguments

        Returns a MixinCall with `name` set to the path (segments
        joined by `>` so existing `find_mixin_matches` walks correctly)
        and `args` populated when `(...)` is present.
        """
        segments: list[str] = [start_tok.text]
        start_index = start_tok.index
        self.stream.consume()  # the start token

        while True:
            nt = self.stream.peek()
            if self._has_ws_before(nt):
                break
            if nt.kind not in (Kind.DOT_IDENT, Kind.HASH):
                break
            segments.append(nt.text)
            self.stream.consume()

        # Each segment already includes its leading `.` / `#`; concatenate
        # them as-is so the resulting name reads `#theme.dark.navbar` —
        # the same flat form less.js uses. `_split_namespace_path` knows
        # how to split this back into individual segments.
        name = ''.join(segments)

        # Optional `(args)` — value-position invocation
        args: list[MixinArg] = []
        has_parens = False
        nt = self.stream.peek()
        if nt.kind is Kind.LPAREN and not self._has_ws_before(nt):
            has_parens = True
            args = self._parse_mixin_value_args()

        # Optional `!important` — propagates to lookup-derived declarations.
        important = False
        nt = self.stream.peek()
        if nt.kind is Kind.IMPORTANT:
            self.stream.consume()
            important = True

        mc = MixinCall(index=start_index, name=name, args=args, important=important)
        # `#lib.colors` (no parens) is a namespace ALIAS — the path is
        # the value, not an invocation. The bare form survives through to
        # the emitter so `@alias: #lib.colors;` and `@{alias}` round-trip
        # as `#lib.colors` (without `()`). less.js does the same.
        if not has_parens:
            mc._no_parens = True
        return mc

    def _parse_mixin_value_args(self) -> list[MixinArg]:
        """Consume `(...)` after a value-position mixin reference. Reuses
        `mixins.parse_mixin_args` for consistency with statement-form
        mixin calls (handles `;` split, named args, etc.).
        """
        text = self._read_balanced(Kind.LPAREN, Kind.RPAREN)
        # Strip the surrounding parens to match parse_mixin_args contract.
        inner = text[1:-1] if text.startswith('(') and text.endswith(')') else text
        from ..mixins import parse_mixin_args

        return parse_mixin_args(inner)

    def _maybe_attach_lookups(self, node: Node) -> Node:
        """Wrap `node` in zero or more `Lookup` nodes for `[key]` postfixes.

        Only kicks in when `[` follows `node`'s last token with NO
        whitespace — this protects CSS grid `[line-name]` syntax (which
        is always whitespace-separated from prior values).
        """
        while True:
            nt = self.stream.peek()
            if nt.kind is not Kind.LBRACKET or self._has_ws_before(nt):
                break
            lbracket_index = nt.index
            self.stream.consume()  # `[`
            # Empty `[]` — less.js's NamespaceValue treats this as
            # "last declaration of the target" (`rs.lastDeclaration()`),
            # so we mark it with the `'last'` kind for the evaluator.
            if self.stream.peek_kind() is Kind.RBRACKET:
                self.stream.consume()
                node = Lookup(index=lbracket_index, target=node, key_kind='last', key='')
                continue
            key_kind, key = self._parse_lookup_key()
            self.stream.expect(Kind.RBRACKET, "expected ']' for lookup")
            # Anchor the Lookup at the `[` so error messages point there
            # (less.js reports lookup errors at the bracket column).
            node = Lookup(index=lbracket_index, target=node, key_kind=key_kind, key=key)
        return node

    def _parse_lookup_key(self) -> tuple[str, str]:
        """Parse the key inside `[...]`.

        Recognised forms:
          IDENT          → ('name',          text)
          $IDENT         → ('name',          text without `$`)
          @IDENT         → ('var',           '@text')
          @@IDENT        → ('var-indirect',  '@@text')
          $@IDENT        → ('prop-indirect', '@text')
        """
        t = self.stream.peek()
        if t.kind is Kind.IDENT:
            self.stream.consume()
            return 'name', t.text
        if t.kind is Kind.DOLLAR_NAME:
            self.stream.consume()
            return 'name', t.text[1:]
        if t.kind is Kind.AT_AT_NAME:
            self.stream.consume()
            return 'var-indirect', t.text
        if t.kind is Kind.AT_NAME:
            self.stream.consume()
            return 'var', t.text
        if t.kind is Kind.DOLLAR and self.stream.peek_kind(1) is Kind.AT_NAME:
            # `$@name` — two tokens, no ws between them
            nt = self.stream.peek(1)
            if not self._has_ws_before(nt):
                self.stream.consume()  # `$`
                self.stream.consume()  # `@name`
                return 'prop-indirect', nt.text
        self._error('expected name inside [] lookup', t.index)

    def _parse_url(self, start_index: int) -> Url:
        """Consume `url(...)` and capture its raw contents."""
        self.stream.consume()  # LPAREN
        depth = 1
        start = self.stream.peek().index
        end = start
        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.EOF:
                break
            if tok.kind is Kind.LPAREN:
                depth += 1
            elif tok.kind is Kind.RPAREN:
                depth -= 1
                if depth == 0:
                    break
            end = tok.index + len(tok.text)
            self.stream.consume()
        self.stream.match(Kind.RPAREN)
        return Url(index=start_index, value=self.source.text[start:end].strip())
