"""Guard-position parser mixin.

`parse_guard_text("(@a > 5) and (@b = red), (@c)")` returns a
`Condition` tree. The grammar:

  guard_list  := clause ("," clause)*       # comma = OR (lowest)
  clause      := "(" cond ")"               # less.js requires parens
  cond        := conj ("and" conj)*         # and = AND
  conj        := "not"? unit
  unit        := "(" cond ")" | comparison
  comparison  := value (cmpOp value)?       # bare value = truthy
  cmpOp       := "<" | "<=" | ">" | ">=" | "=" | "<>"

The implementation reuses the value-level parsers for the operands
(via `self._parse_add_expr`) so variable references, calls, and
operations all resolve at evaluation time.
"""

from __future__ import annotations

from ..ast_nodes import Condition, Keyword
from ..errors import ParseError
from ..lexer import Kind
from ._state import _ParserState


class _GuardsMethods(_ParserState):
    """Guard-clause parsing. Self-contained except for the call to
    `self._parse_add_expr` (provided by `_ValueExprMethods`).
    """

    _CMP_KINDS = frozenset({Kind.LT, Kind.LE, Kind.GT, Kind.GE, Kind.EQUALS, Kind.NE})

    def _parse_guard_root(self) -> Condition:
        first = self._parse_guard_clause()
        # less.js accepts `and` between top-level clauses:
        # `when (default()) and (@x = 3)` is one guard whose top-level
        # op is AND of two clauses, not two separate guards. Bind `and`
        # tighter than `,` (which means OR).
        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.IDENT and tok.text == 'and':
                self.stream.consume()
                rhs = self._parse_guard_clause()
                first = Condition(index=first.index, op='and', lhs=first, rhs=rhs)
                continue
            break
        # Build a left-associative OR-chain.
        while self.stream.match(Kind.COMMA):
            rhs = self._parse_guard_clause()
            # `,` between AND-groups: chain at the OR level so existing
            # AND-binding wins.
            while True:
                ntok = self.stream.peek()
                if ntok.kind is Kind.IDENT and ntok.text == 'and':
                    self.stream.consume()
                    rhs2 = self._parse_guard_clause()
                    rhs = Condition(index=rhs.index, op='and', lhs=rhs, rhs=rhs2)
                    continue
                break
            first = Condition(index=first.index, op='or', lhs=first, rhs=rhs)
        return first

    def _parse_guard_clause(self) -> Condition:
        # less.js's guard grammar: a clause is `(cond)`, `not (cond)`,
        # or an `and`-chain of those (`(a) and (b)`, `not (a) and (b)`).
        # Anything else — notably a bare `@var` without parens — surfaces
        # as `SyntaxError: expected condition` anchored at the offending
        # token.
        tok = self.stream.peek()
        if tok.kind is Kind.LPAREN or (tok.kind is Kind.IDENT and tok.text == 'not'):
            return self._parse_guard_cond()
        anchor = tok.index
        err = ParseError('expected condition')
        err._less_js_name = 'SyntaxError'
        err._base_index = anchor
        raise err

    def _parse_guard_cond(self) -> Condition:
        # less.js binds `and` tighter than `or`: `a or b and c` parses
        # as `a or (b and c)`. Split the loop so `and`-runs collapse
        # into a single AND-clause before any `or` joins them.
        lhs = self._parse_guard_and_run()
        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.IDENT and tok.text == 'or':
                self.stream.consume()
                rhs = self._parse_guard_and_run()
                lhs = Condition(index=lhs.index, op='or', lhs=lhs, rhs=rhs)
                continue
            break
        return lhs

    def _parse_guard_and_run(self) -> Condition:
        lhs = self._parse_guard_and_unit()
        while True:
            tok = self.stream.peek()
            if tok.kind is Kind.IDENT and tok.text == 'and':
                self.stream.consume()
                rhs = self._parse_guard_and_unit()
                lhs = Condition(index=lhs.index, op='and', lhs=lhs, rhs=rhs)
                continue
            break
        return lhs

    def _parse_guard_and_unit(self) -> Condition:
        tok = self.stream.peek()
        negate = False
        if tok.kind is Kind.IDENT and tok.text == 'not':
            self.stream.consume()
            negate = True
        if self.stream.peek_kind() is Kind.LPAREN:
            self.stream.consume()
            inner = self._parse_guard_cond()
            self.stream.match(Kind.RPAREN)
            if negate:
                return Condition(index=inner.index, op='not', lhs=inner)
            return inner
        cmp = self._parse_guard_comparison()
        if negate:
            cmp = Condition(index=cmp.index, op='not', lhs=cmp)
        return cmp

    def _parse_guard_comparison(self) -> Condition:
        start_index = self.stream.peek().index
        lhs = self._parse_add_expr()
        if lhs is None:
            # Empty/garbage condition — model as always-false. less.js
            # rejects this with a ParseError; we lean lenient to avoid
            # rejecting whole files for a single misshaped guard.
            return Condition(
                index=start_index,
                op='=',
                lhs=Keyword(index=start_index, value='false'),
                rhs=Keyword(index=start_index, value='true'),
            )
        op_tok = self.stream.peek()
        if op_tok.kind not in self._CMP_KINDS:
            # Truthy form: `when (@active)` — true if @active resolves
            # to truthy. We represent it as "lhs = true" so the eval
            # path goes through Condition handling rather than special
            # bare-value logic.
            return Condition(
                index=lhs.index,
                op='truthy',
                lhs=lhs,
                rhs=None,
            )
        op = op_tok.text
        self.stream.consume()
        rhs = self._parse_add_expr()
        if rhs is None:
            return Condition(
                index=lhs.index,
                op='=',
                lhs=Keyword(index=lhs.index, value='false'),
                rhs=Keyword(index=lhs.index, value='true'),
            )
        return Condition(index=lhs.index, op=op, lhs=lhs, rhs=rhs)
