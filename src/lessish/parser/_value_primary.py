"""Value-position primary parser mixin.

The atom-level dispatch — every value-position token kind has a
branch here. This is the largest single piece of grammar in the
parser, broken out into its own mixin both for file-size reasons
and because the value-primary cluster is the cross-cutting consumer:
it depends on every other value mixin (`_ValueExprMethods` for
`_parse_add_expr`, `_CallsMethods` for `_parse_call_args` /
`_parse_namespace_path_or_call` / `_parse_url` /
`_maybe_attach_lookups`, `_GuardsMethods` for `_CMP_KINDS`,
`_UtilsMethods` for the token-reading helpers).

Hosts `_VALUE_TOKENIZATION_BREAKERS` (the set of token kinds that
break the catch-all "consume adjacent unrecognized tokens" loop)
and the small `_parse_anonymous_mixin` helper for `each()`-style
`.(params) { body }` lambdas.
"""

from __future__ import annotations

import re as _re

from ..ast_nodes import (
    Anonymous,
    Color,
    DetachedRuleset,
    Dimension,
    Expression,
    Keyword,
    Node,
    Paren,
    PropertyAccess,
    Quoted,
    Variable,
)
from ..colors import parse_hex
from ..errors import DeclAnchor, ParseError
from ..lexer import Kind, Token
from ._state import _ParserState
from .helpers import _retokenize_inline

# Tokens that interrupt the "glue adjacent unknown tokens into one
# Anonymous" loop in the value parser. Anything structurally meaningful
# at value position must stay parseable.
_VALUE_TOKENIZATION_BREAKERS: frozenset[Kind] = frozenset(
    {
        Kind.LPAREN,
        Kind.RPAREN,
        Kind.LBRACE,
        Kind.RBRACE,
        Kind.LBRACKET,
        Kind.RBRACKET,
        Kind.AT_NAME,
        Kind.AT_AT_NAME,
        Kind.HASH,
        Kind.HASH_BARE,
        Kind.STRING,
        Kind.TILDE_STRING,
        Kind.NUMBER,
        Kind.IDENT,
        Kind.INTERP_OPEN,
        Kind.DOLLAR_INTERP_OPEN,
    }
)


class _ValuePrimaryMethods(_ParserState):
    """Value-position atom dispatch + `each()` lambda helper."""

    def _parse_primary(self) -> Node | None:
        tok = self.stream.peek()

        # `~(...)` — Less list constructor. `;` (if present at top level)
        # is the outer separator and forces a comma-list; otherwise the
        # inner text is parsed as a normal value (`~(a, b, c)` and the
        # space-separated `~(a b c)` both fall through to the value
        # parser unchanged). The leading `~` is purely structural here
        # — by the time the inner is parsed it carries no escape meaning.
        if tok.kind is Kind.TILDE and self.stream.peek(1).kind is Kind.LPAREN:
            start_idx = tok.index
            self.stream.consume()  # TILDE
            paren_text = self._read_balanced(Kind.LPAREN, Kind.RPAREN)
            inner_src = paren_text[1:-1] if paren_text.startswith('(') and paren_text.endswith(')') else paren_text
            from .values import _build_tilde_paren_list

            return _build_tilde_paren_list(inner_src, start_idx)

        if tok.kind is Kind.NUMBER:
            self.stream.consume()
            num = float(tok.text)
            nt = self.stream.peek()
            unit = ''
            if not self._has_ws_before(nt):
                if nt.kind is Kind.PERCENT:
                    unit = '%'
                    self.stream.consume()
                elif nt.kind is Kind.IDENT:
                    # CSS units are alphabetic (or `%`). The IDENT may
                    # be a full Less identifier including `-digit-...`
                    # — e.g. `17px-1px` lexes as `17`, IDENT('px-1px').
                    # Peel off only the leading alphabetic prefix as
                    # the unit; if there's leftover text, replace the
                    # IDENT in the stream so subsequent parsing sees
                    # the remainder (typically a `-` operator and
                    # another number).
                    full = nt.text
                    m = _re.match(r'^[a-zA-Z]+', full)
                    if m and m.end() == len(full):
                        unit = full
                        self.stream.consume()
                    elif m:
                        unit = m.group(0)
                        rest_text = full[m.end() :]
                        rest_index = nt.index + m.end()
                        rest_tokens = _retokenize_inline(rest_text, rest_index)
                        # Replace nt at the stream's current position
                        # with the retokenized tail.
                        pos = self.stream.pos
                        self.stream.tokens[pos : pos + 1] = rest_tokens
                        # Length changed in place — resync the cache peek() reads.
                        self.stream._refresh_len()
                    # else: IDENT starts with non-letter (rare); leave alone.
            return Dimension(index=tok.index, value=num, unit=unit)

        if tok.kind in (Kind.AT_NAME, Kind.AT_AT_NAME):
            self.stream.consume()
            nt = self.stream.peek()
            if nt.kind is Kind.LPAREN and not self._has_ws_before(nt):
                # `@name(...)` at value position — a "variable call".
                # less.js's parser only accepts this when followed by a
                # `[...]` lookup (`@a()[key]`); the bare form is allowed
                # only at statement position. Consume the `(...)` then
                # require `[`; otherwise raise.
                paren_start = nt.index
                paren_text = self._read_balanced(Kind.LPAREN, Kind.RPAREN)
                after = self.stream.peek()
                if after.kind is not Kind.LBRACKET or self._has_ws_before(after):
                    # Anchor at the position immediately following the
                    # closing `)` — matches less.js's reported column.
                    anchor = paren_start + len(paren_text)
                    err = ParseError("Missing '[...]' lookup in variable call")
                    err._less_js_name = 'ParseError'
                    err._base_index = anchor
                    raise err
                # `[...]` follows — fall through to attach the lookup
                # against a Variable node anchored at `@name`. The
                # actual call invocation happens at eval time via the
                # Lookup's target Variable.
                return self._maybe_attach_lookups(Variable(index=tok.index, name=tok.text))
            return self._maybe_attach_lookups(Variable(index=tok.index, name=tok.text))

        if tok.kind is Kind.DOLLAR_NAME:
            self.stream.consume()
            # `$prop` → look up declaration `prop` in scope. The token
            # text includes the leading `$`; the AST node stores the
            # name without it.
            return self._maybe_attach_lookups(PropertyAccess(index=tok.index, name=tok.text[1:]))

        # Anonymous mixin / `each()` lambda shorthand: `.(params) { body }`
        # (or `#(params) { body }`). Must precede the generic DOT/HASH
        # fallthrough and the namespace-path branches.
        if tok.kind in (Kind.DOT, Kind.HASH_BARE):
            nt = self.stream.peek(1)
            if not self._has_ws_before(nt) and nt.kind is Kind.LPAREN:
                return self._parse_anonymous_mixin(tok)

        # `.name(...)` or `.name[...]` at value position — namespace/mixin
        # invocation or path reference. Must precede the generic fallback.
        if tok.kind is Kind.DOT_IDENT:
            nt = self.stream.peek(1)
            if not self._has_ws_before(nt) and nt.kind in (Kind.LPAREN, Kind.LBRACKET, Kind.DOT_IDENT, Kind.HASH):
                node = self._parse_namespace_path_or_call(tok)
                return self._maybe_attach_lookups(node)

        if tok.kind is Kind.HASH:
            # Disambiguate hex literal from namespace reference. If the
            # next token (no whitespace) is `(`, `[`, `.name`, or another
            # `#name`, treat as a namespace path; otherwise try hex.
            nt = self.stream.peek(1)
            if not self._has_ws_before(nt) and nt.kind in (Kind.LPAREN, Kind.LBRACKET, Kind.DOT_IDENT, Kind.HASH):
                node = self._parse_namespace_path_or_call(tok)
                return self._maybe_attach_lookups(node)
            self.stream.consume()
            parsed = parse_hex(tok.text)
            if parsed is None:
                # At value position a `#xxx` HASH must be a valid hex
                # color (3, 4, 6, or 8 digits). less.js rejects other
                # shapes — `#fffff` here — with `Unrecognised input`,
                # anchored at the END of the declaration value (past
                # any trailing comment/whitespace).
                hex_body = tok.text[1:]  # strip leading '#'
                if hex_body and all(c in '0123456789abcdefABCDEF' for c in hex_body):
                    err = ParseError('Unrecognised input')
                    err._less_js_name = 'ParseError'
                    err._propagate = DeclAnchor.VALUE_END
                    raise err
                # Non-hex-shape HASH (`#my-id`) reaches the value parser
                # only via odd nesting — round-trip as text.
                return Anonymous(index=tok.index, value=tok.text)
            rgb, alpha = parsed
            return Color(index=tok.index, value=tok.text, rgb=rgb, alpha=alpha)

        if tok.kind in (Kind.STRING, Kind.TILDE_STRING):
            self.stream.consume()
            text = tok.text
            if text.startswith('~'):
                return Quoted(index=tok.index, quote=text[1], value=text[2:-1], escaped=True)
            return Quoted(index=tok.index, quote=text[0], value=text[1:-1], escaped=False)

        if tok.kind in (Kind.BACKTICK_STRING, Kind.TILDE_BACKTICK_STRING):
            # Backticks are JS-eval in less.js. lessish doesn't run JS,
            # but the lexer still emits the tokens so a custom parser
            # subclass can intercept them; this default surfaces the
            # unsupported-feature diagnostic.
            self._reject_backtick(tok)

        if tok.kind is Kind.IDENT:
            self.stream.consume()
            name = tok.text
            nt = self.stream.peek()
            if nt.kind is Kind.LPAREN and not self._has_ws_before(nt):
                if name == 'url':
                    return self._parse_url(tok.index)
                return self._parse_call_args(tok.index, name)
            return Keyword(index=tok.index, value=name)

        if tok.kind is Kind.PERCENT and self.stream.peek_kind(1) is Kind.LPAREN:
            # `%("template", arg1, ...)` — string-format function. The
            # name lives in the function registry under '%'.
            self.stream.consume()
            return self._parse_call_args(tok.index, '%')

        if tok.kind is Kind.PERCENT:
            # Bare `%` not attached to a NUMBER and not part of `%(...)`
            # — less.js rejects with `Invalid % without number`,
            # anchored at the surrounding declaration's column. The
            # outer `eval_declaration` supplies that index via the
            # `_propagate = DeclAnchor.DECL_START` re-anchor.
            err = ParseError('Invalid % without number')
            err._less_js_name = 'SyntaxError'
            err._propagate = DeclAnchor.DECL_START
            raise err

        if tok.kind is Kind.LPAREN:
            # Lookahead for a top-level `:` inside the balanced `(...)`:
            # that's CSS media-query feature syntax (`(min-width: 640px)`)
            # which the arithmetic expression parser can't represent.
            # Capture the full inner text as an Anonymous and wrap it
            # in a Paren so substitution into an at-rule prelude keeps
            # the parens (`@tablet: (min-width: 640px); @media @tablet`
            # round-trips as `@media (min-width: 640px)`).
            if self._paren_contents_have_top_colon():
                start_idx = tok.index
                text = self._read_balanced(Kind.LPAREN, Kind.RPAREN)
                # Strip the wrapping `(...)` from the captured text;
                # `value_to_css(Paren)` adds them back.
                inner_text = text[1:-1] if text.startswith('(') and text.endswith(')') else text
                return Paren(index=start_idx, value=Anonymous(index=start_idx + 1, value=inner_text))
            self.stream.consume()
            # Each `(` is a recursion level — bound it so deeply nested
            # parens raise a clean ParseError instead of a RecursionError.
            with self._descend():
                inner = self._parse_add_expr()
                # `(2 > 1)` / `(@a = 5)` — a comparison-wrapped condition is
                # a value-position expression when fed to `if()`/`boolean()`.
                # The arithmetic expression parser doesn't know `>`/`<`/`=`;
                # extend the parse to gather the trailing comparison + RHS
                # as a flat Expression so eval-time `_arg_to_condition` can
                # rebuild a Condition from it.
                if inner is not None and self.stream.peek_kind() in self._CMP_KINDS:
                    values: list[Node] = [inner]
                    while True:
                        nt = self.stream.peek()
                        if nt.kind is Kind.RPAREN or nt.kind is Kind.EOF:
                            break
                        if nt.kind in self._CMP_KINDS:
                            values.append(Anonymous(index=nt.index, value=nt.text))
                            self.stream.consume()
                            continue
                        nxt = self._parse_add_expr()
                        if nxt is None:
                            break
                        values.append(nxt)
                    inner = Expression(index=inner.index, values=values)
                if self.stream.peek_kind() is not Kind.RPAREN:
                    # less.js's expression parser rejects anything other
                    # than `)` here, anchored at the offending token.
                    anchor_tok = self.stream.peek()
                    err = ParseError("Expected ')'")
                    err._less_js_name = 'ParseError'
                    err._base_index = anchor_tok.index
                    raise err
                self.stream.consume()  # RPAREN
                if inner is None:
                    return None
                return Paren(index=tok.index, value=inner)

        if tok.kind is Kind.LBRACE:
            # `{ ... }` at value position — a detached ruleset. Reuse the
            # block parser to consume body rules, then run them through
            # the mixin transform so nested MixinDefs/MixinCalls in the
            # detached body are recognized too.
            self.stream.consume()
            body_rules = self._primary()
            self.stream.expect(Kind.RBRACE, "missing '}' for detached ruleset")
            from ..mixins import transform_mixins

            transformed = [transform_mixins(r) for r in body_rules]
            return DetachedRuleset(index=tok.index, rules=transformed)

        if tok.kind in (Kind.INTERP_OPEN, Kind.DOLLAR_INTERP_OPEN):
            raw = self._read_interpolation()
            # `@{x}` at value position resolves to the variable named `x`.
            # Strip the leading `@{` / `${` and trailing `}`.
            inner_name = raw[2:-1] if raw.endswith('}') else raw[2:]
            return Variable(index=tok.index, name='@' + inner_name)

        if tok.kind is Kind.LBRACKET:
            # `[name]` at value position (e.g. CSS grid-template-columns
            # line names). Consume the whole balanced `[...]` as opaque
            # text so internal punctuation doesn't get space-joined.
            text = self._read_balanced(Kind.LBRACKET, Kind.RBRACKET)
            return Anonymous(index=tok.index, value=text)

        # Fallthrough: unknown token. Consume it together with any
        # whitespace-less adjacent unrecognized tokens so things like
        # `progid:DXImageTransform.X(=20)` round-trip as one chunk
        # rather than being space-joined back together.
        start = tok.index
        end = start + len(tok.text)
        self.stream.consume()
        while True:
            nt = self.stream.peek()
            if self._has_ws_before(nt) or nt.kind in self._value_terminators():
                break
            if nt.kind in _VALUE_TOKENIZATION_BREAKERS:
                break
            end = nt.index + len(nt.text)
            self.stream.consume()
        return Anonymous(index=start, value=self.source.text[start:end])

    def _parse_anonymous_mixin(self, lead_tok: Token) -> DetachedRuleset:
        """Parse `.(params) { body }` / `#(params) { body }` — an
        anonymous mixin used as an `each()` callback. Returns a
        DetachedRuleset with `_lambda_params` (list of MixinParam)
        attached on `__dict__` so `_invoke_each` can bind each iteration
        to user-named slots (value/key/index in declaration order).
        """
        from ..mixins import parse_mixin_params

        self.stream.consume()  # DOT or HASH_BARE
        # Capture the balanced `(...)` text and parse as mixin params.
        params_text = self._read_balanced(Kind.LPAREN, Kind.RPAREN)
        # Strip the outer parens to feed parse_mixin_params just the body.
        inner = params_text[1:-1] if params_text.startswith('(') and params_text.endswith(')') else params_text
        params = parse_mixin_params(inner)
        # Body must immediately follow (possibly after whitespace).
        if self.stream.peek_kind() is not Kind.LBRACE:
            # Lambda must have a body; if missing, return an empty DR
            # so the caller can surface a useful error.
            dr_empty = DetachedRuleset(index=lead_tok.index, rules=[])
            dr_empty._lambda_params = params
            return dr_empty
        self.stream.consume()  # LBRACE
        body_rules = self._primary()
        self.stream.expect(Kind.RBRACE, "missing '}' for anonymous mixin")
        from ..mixins import transform_mixins

        transformed = [transform_mixins(r) for r in body_rules]
        dr = DetachedRuleset(index=lead_tok.index, rules=transformed)
        dr._lambda_params = params
        return dr
