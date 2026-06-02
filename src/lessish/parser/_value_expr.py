"""Value-position expression parser mixin.

Implements the precedence-climbing core of the value grammar:

    value      := expression ("," expression)*
    expression := add_expr (WS add_expr)*       # space-separated
    add_expr   := mul_expr (("+" | "-") mul_expr)*
    mul_expr   := unary (("*" | "/") unary)*
    unary      := "-" primary | primary

`primary` itself is huge and lives in `_value_primary.py`; this
mixin defers to `self._parse_primary` for any atom.

Also hosts the IE-filter detector + verbatim capture, which jumps
out of the structured grammar for `progid:...` / `alpha(...)`
shapes that don't follow normal CSS punctuation rules.
"""

from __future__ import annotations

import re

from ..ast_nodes import Anonymous, Expression, Negative, Node, Operation, Value
from ..lexer import Kind
from ._state import _ParserState


class _ValueExprMethods(_ParserState):
    """Top of the value-position parser: comma-list, space-list,
    +/-/*/ / unary minus, IE-filter capture, and a lookahead helper
    that the primary parser uses to disambiguate `(min-width: 640px)`
    from `(a + b)`.
    """

    def _value_terminators(self) -> frozenset[Kind]:
        return frozenset({Kind.COMMA, Kind.SEMICOLON, Kind.RBRACE, Kind.EOF, Kind.IMPORTANT})

    def _parse_value_root(self) -> Value:
        start = self.stream.peek().index
        # Special-case IE filters: `progid:DXImageTransform.Microsoft.X(...)`
        # has its own punctuation rules (no spaces around `:`, between
        # idents, or around `=` inside the call) that the structured
        # value parser otherwise space-joins back. Capture the whole
        # value text verbatim instead.
        if self._peek_is_ie_filter():
            return self._parse_ie_filter_as_anonymous(start)
        expressions: list[Expression] = []
        first = self._parse_expression()
        if first is not None:
            expressions.append(first)
            while self.stream.match(Kind.COMMA):
                expr = self._parse_expression()
                if expr is not None:
                    expressions.append(expr)
        return Value(index=start, expressions=expressions)

    def _peek_is_ie_filter(self) -> bool:
        t0 = self.stream.peek()
        if t0.kind is Kind.IDENT and t0.text.lower() == 'progid':
            t1 = self.stream.peek(1)
            return t1.kind is Kind.COLON
        # `alpha(opacity=N)` is the IE filter shorthand. The bare `=`
        # inside the call has no operator meaning here — capture the
        # whole call as Anonymous so the `_ie_filter` path can strip
        # the spaces around `=` and substitute any `@var` operand.
        # Only trigger on `IDENT '=' …` inside the parens so this
        # doesn't shadow the Less `alpha(color)` function.
        if t0.kind is Kind.IDENT and t0.text.lower() == 'alpha':
            if self.stream.peek(1).kind is not Kind.LPAREN:
                return False
            if self.stream.peek(2).kind is not Kind.IDENT:
                return False
            return self.stream.peek(3).kind is Kind.EQUALS
        return False

    def _parse_ie_filter_as_anonymous(self, start: int) -> Value:
        end = start
        depth_paren = 0
        terminators = self._value_terminators()
        while True:
            tok = self.stream.peek()
            if depth_paren == 0 and tok.kind in terminators:
                break
            if tok.kind is Kind.LPAREN:
                depth_paren += 1
            elif tok.kind is Kind.RPAREN:
                if depth_paren == 0:
                    break
                depth_paren -= 1
            end = tok.index + len(tok.text)
            self.stream.consume()
        text = self.source.text[start:end]
        # less.js strips whitespace around `=` in ie-filter arg lists
        # (`opacity = 20` → `opacity=20`) and normalises `,` → `, ` so
        # `gradient(GradientType=0,startColorstr=...)` round-trips with
        # a space after the comma. Matches less.js's `toCSS` output.
        text = re.sub(r'\s*=\s*', '=', text)
        text = re.sub(r',\s*', ', ', text)
        anon = Anonymous(index=start, value=text)
        # `_ie_filter` forces `_is_literal_only` off so the fast path
        # can't emit the raw source (`opacity = 20`) over this
        # normalised text, and it gates var substitution.
        anon._ie_filter = True
        return Value(index=start, expressions=[Expression(index=start, values=[anon])])

    def _parse_expression(self) -> Expression | None:
        start = self.stream.peek().index
        values: list[Node] = []
        end_kinds = self._value_terminators() | {Kind.RPAREN}
        first = True
        while True:
            tok = self.stream.peek()
            if tok.kind in end_kinds:
                break
            # Capture the next entity's leading-whitespace state *before*
            # parsing — once we descend into `_parse_add_expr` the head
            # token gets consumed and we lose that information. Used at
            # emit time so `@{x}_bar` (no space between interpolation and
            # the next IDENT) round-trips as `foo_bar`, not `foo _bar`.
            has_ws = self._has_ws_before(tok)
            entity = self._parse_add_expr()
            if entity is None:
                break
            if not first and not has_ws:
                entity._glue_to_prev = True
            values.append(entity)
            first = False
        if not values:
            return None
        return Expression(index=start, values=values)

    def _parse_add_expr(self) -> Node | None:
        lhs = self._parse_mul_expr()
        if lhs is None:
            return None
        while True:
            op_tok = self.stream.peek()
            if op_tok.kind not in (Kind.PLUS, Kind.MINUS):
                break
            # less.js rule: `+`/`-` is binary when whitespace is symmetric
            # around it. `1px + 2px` and `1px+2px` are both operations;
            # `2px -1px` is "2px, then -1px" (two space-separated
            # entities) because the asymmetric spacing flags the second
            # `-` as a unary sign.
            ws_before = self._has_ws_before(op_tok)
            next_tok = self.stream.peek(1)
            ws_after = self._has_ws_before(next_tok)
            if ws_before != ws_after:
                break
            op = op_tok.text
            self.stream.consume()
            rhs = self._parse_mul_expr()
            if rhs is None:
                break
            lhs = Operation(index=lhs.index, op=op, lhs=lhs, rhs=rhs)
        return lhs

    def _parse_mul_expr(self) -> Node | None:
        lhs = self._parse_unary()
        if lhs is None:
            return None
        while True:
            op_tok = self.stream.peek()
            # less.js: `./` is the explicit-division operator (v3-era,
            # `Operation.eval`) — always folds regardless of `math` mode.
            # Surfaces as DOT immediately followed by SLASH with no
            # whitespace; consume both as a single division.
            is_dot_slash = (
                op_tok.kind is Kind.DOT
                and self.stream.peek(1).kind is Kind.SLASH
                and not self._has_ws_before(self.stream.peek(1))
            )
            if op_tok.kind not in (Kind.STAR, Kind.SLASH) and not is_dot_slash:
                break
            # `is_spaced`: whether the operator carried whitespace on
            # *either* side in source. Preserved through emit when the
            # operation doesn't fold (e.g. `font: 12px/14px`).
            ws_before = self._has_ws_before(op_tok)
            if is_dot_slash:
                next_tok = self.stream.peek(2)
                ws_after = self._has_ws_before(next_tok)
                is_spaced = ws_before or ws_after
                op = './'
                self.stream.consume()  # DOT
                self.stream.consume()  # SLASH
            else:
                next_tok = self.stream.peek(1)
                ws_after = self._has_ws_before(next_tok)
                is_spaced = ws_before or ws_after
                op = op_tok.text
                self.stream.consume()
            rhs = self._parse_unary()
            if rhs is None:
                break
            lhs = Operation(index=lhs.index, op=op, lhs=lhs, rhs=rhs, is_spaced=is_spaced)
        return lhs

    def _parse_unary(self) -> Node | None:
        tok = self.stream.peek()
        if tok.kind is Kind.MINUS:
            nt = self.stream.peek(1)
            if nt.kind in (Kind.NUMBER, Kind.LPAREN, Kind.AT_NAME, Kind.AT_AT_NAME):
                self.stream.consume()
                inner = self._parse_primary()
                if inner is not None:
                    return Negative(index=tok.index, value=inner)
                return None
        return self._parse_primary()

    def _paren_contents_have_top_colon(self) -> bool:
        """Peek the tokens after the current `(` and return True iff a
        `:` appears at the top level of the balanced `(...)` group.
        Used to disambiguate `(min-width: 640px)` (media feature, keep
        as text) from `(a + b)` (arithmetic, parse as expression).
        """
        depth = 0
        i = 0
        while True:
            tok = self.stream.peek(i)
            if tok.kind is Kind.EOF:
                return False
            if tok.kind is Kind.LPAREN:
                depth += 1
            elif tok.kind is Kind.RPAREN:
                depth -= 1
                if depth == 0:
                    return False
            elif tok.kind is Kind.COLON and depth == 1:
                return True
            i += 1
