"""At-rule evaluation + prelude post-processing.

`eval_atrule` is the main entry. It pushes a synthetic frame around the
body so sibling rulesets see the at-rule's locally-declared variables,
splices MixinCall / VariableCall outputs at statement position, and
runs the prelude through `_eval_atrule_prelude` which resolves
`@var[key]` lookups, strips tilde-quoting, and folds arithmetic inside
media-feature parens.
"""

from __future__ import annotations

import re

from ..ast_nodes import AtRule, MixinCall, Node, Ruleset, VariableCall
from ..context import EvalContext
from ..errors import LessError, UndefinedNameError, UnsupportedFeatureError
from ..parser import parse_value_text
from ..visitors import value_to_css
from .control_flow import _invoke_variable_call
from .dispatch import eval_node
from .helpers import _extract_var_name, _locate, _strip_tilde_quotes
from .mixins import _invoke_mixin_call
from .values import eval_value

# Pre-compiled at module load: the bracket-lookup pattern fires for
# every at-rule prelude, the previous inline `import re as _re` +
# `re.compile(...)` did both per call.
_BRACKET_LOOKUP_RE = re.compile(r'(@[_a-zA-Z][\w-]*|#[\w-]+|\.[\w-]+)((?:\[[^\]]*\])+)')


def eval_atrule(at: AtRule, ctx: EvalContext) -> AtRule:

    if at.name == '@plugin':
        # `@plugin "…"` loads a JavaScript plugin at compile time. We
        # don't implement JS evaluation by design (see README out-of-
        # scope policy) — surface it as an explicit error rather than
        # silently dropping or emitting an invalid `@plugin` line.
        err = UnsupportedFeatureError(f'@plugin {at.prelude} requires JavaScript evaluation; not supported by lessish')
        _locate(err, at.index, ctx)
        raise err
    try:
        new_body: list[Node] | None = None
        if at.body is not None:
            # Push a synthetic frame whose rules ARE the at-rule body so
            # `@var: …;` declared inside `@media { … }` are visible to
            # sibling rulesets in the same at-rule block. less.js gives
            # the at-rule body its own scope; without this push, the
            # frame stack jumps over the body and inner declarations
            # can't see vars declared above them in the same block.
            atrule_frame = Ruleset(
                index=at.index,
                selectors=[],
                rules=list(at.body),
                root=False,
            )
            ctx.push_frame(atrule_frame)
            try:
                # MixinCall / VariableCall at statement position inside
                # an at-rule body splice their result rules into the
                # body, same as inside a Ruleset (each() generators,
                # 0-arg mixin invocations, detached-ruleset variable
                # calls). Without this, `@starting-style { each(…) }`
                # would emit empty because the MixinCall passes through
                # eval_node unchanged and the emitter drops it.
                new_body = []
                for r in at.body:
                    if isinstance(r, MixinCall):
                        try:
                            new_body.extend(_invoke_mixin_call(r, ctx))
                        except LessError as e:
                            _locate(e, r.index, ctx)
                            raise
                        continue
                    if isinstance(r, VariableCall):
                        try:
                            new_body.extend(_invoke_variable_call(r, ctx))
                        except LessError as e:
                            _locate(e, r.index, ctx)
                            raise
                        continue
                    new_body.append(eval_node(r, ctx))
            finally:
                ctx.pop_frame()
        new_at = AtRule(
            index=at.index,
            name=at.name,
            prelude=_eval_atrule_prelude(at.prelude, ctx),
            body=new_body,
            trailing_comments=at.trailing_comments,
        )
        # Carry over the import-time `_reference` skip tag (set by the
        # importer on `@import (reference) ...`-sourced at-rules) so
        # the post-eval emit gate still suppresses them. Without this,
        # eval_atrule's fresh AtRule object would lose the flag and
        # the at-rule would unconditionally render.
        if at._reference:
            new_at._reference = True
        return new_at
    except LessError as e:
        # Pinpoint the offending variable inside the prelude when
        # possible — less.js error reporting expects the column of
        # the bad reference, not the start of the at-rule.
        if isinstance(e, UndefinedNameError) and ctx.source is not None:
            name = _extract_var_name(e.message)
            if name:
                pos = ctx.source.text.find(name, at.index)
                if pos != -1:
                    _locate(e, pos, ctx)
                    raise
        _locate(e, at.index, ctx)
        raise


def _eval_namespace_call_lookups(text: str, ctx: EvalContext) -> str:
    """Scan `text` for namespace-call-with-lookup constructs
    (`#ns.breakpoint(args)[@max]`) and replace each with the evaluated
    value. Pure paren/bracket matching — no regex — so nested calls
    inside the `(args)` (e.g. `.valToGet[]`) survive intact and the
    whole construct round-trips through `parse_value_text` +
    `eval_value`. Leaves the text untouched when nothing matches.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Only `#name` / `.name` start a namespace path here. `@var`
        # bracket lookups already get handled by the regex pass below.
        if ch not in ('#', '.'):
            out.append(ch)
            i += 1
            continue
        # Try to extend through ident chars (incl. `-`), additional
        # dotted/hash segments, an optional `(args)`, then a chain of
        # `[lookup]`. If none of `[…]` follows, this isn't a lookup
        # construct — fall through to verbatim.
        head_start = i
        j = i + 1
        # Initial ident body.
        while j < n and (text[j].isalnum() or text[j] in '-_'):
            j += 1
        # Additional `.name` / `#name` segments — no whitespace between.
        while j < n and text[j] in ('.', '#'):
            nxt = j + 1
            if nxt >= n or not (text[nxt].isalnum() or text[nxt] in '-_'):
                break
            j = nxt + 1
            while j < n and (text[j].isalnum() or text[j] in '-_'):
                j += 1
        # Optional `(args)` — bracket-balanced.
        if j < n and text[j] == '(':
            depth = 1
            k = j + 1
            while k < n and depth > 0:
                c = text[k]
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
                k += 1
            if depth == 0:
                j = k
            else:
                out.append(ch)
                i += 1
                continue
        # Require at least one `[...]` to commit — otherwise this is
        # plain text like `.foo` or `#nav` that shouldn't get evaluated.
        if j >= n or text[j] != '[':
            out.append(ch)
            i += 1
            continue
        # Chain `[...]` lookups, bracket-balanced.
        while j < n and text[j] == '[':
            depth = 1
            k = j + 1
            while k < n and depth > 0:
                c = text[k]
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                k += 1
            if depth != 0:
                break
            j = k
        construct = text[head_start:j]
        try:
            evaled = eval_value(parse_value_text(construct), ctx)
            out.append(value_to_css(evaled))
            i = j
        except LessError:
            raise
        except Exception:
            out.append(ch)
            i += 1
    return ''.join(out)


def _eval_atrule_prelude(prelude: str, ctx: EvalContext) -> str:
    """Resolve `@var`, `@{var}`, and `@var[key]` references inside an
    at-rule prelude.

    The simple cases (`@name`, `@{name}`, `${name}`) are handled by
    `ctx.substitute_text`. For `@var[key]` / `#ns[key]` we run a regex
    pass that finds these patterns, evaluates each via the value parser,
    and splices the resolved text in. A pre-pass also handles the
    extended namespace-call shape `#ns.path(args)[lookup]...` (used
    for `@media #ns.breakpoint(.x[])[@max]` patterns) — paren-/bracket-
    matching to find the full span.
    """
    prelude = _eval_namespace_call_lookups(prelude, ctx)

    # `@var[key]` (also `#ns[key]`, possibly chained) — find the longest
    # adjacent bracket-lookup form and resolve it. Skip cases preceded by
    # whitespace inside a brackets/braces — the simple ones handled by
    # substitute_text won't be affected.

    def replace_lookup(m: re.Match[str]) -> str:
        text = m.group(0)
        try:
            parsed = parse_value_text(text)
            evaled = eval_value(parsed, ctx)
            return value_to_css(evaled)
        except LessError:
            raise
        except Exception:
            return text  # leave verbatim if structural parse fails

    out = _BRACKET_LOOKUP_RE.sub(replace_lookup, prelude)
    out = ctx.substitute_text(out)
    # Less.js's tilde-string strips quotes at emit. In at-rule preludes,
    # `~'@{r1} / @{r2}'` after interpolation is `~'16 / 9'` and the
    # tilde-quote wrapper should be peeled. Do it as a post-pass — any
    # `~'…'` / `~"…"` runs become just the body.
    out = _strip_tilde_quotes(out)
    # Fold arithmetic inside media-feature values: `(min-width: (60px + 1))`
    # → `(min-width: 61px)`. We scan for `(<ident>: <value>)` patterns
    # where `<value>` contains a top-level paren (the arithmetic group)
    # and re-evaluate that group through the value parser.
    out = _fold_atrule_prelude_arithmetic(out, ctx)
    return out


def _fold_atrule_prelude_arithmetic(prelude: str, ctx: EvalContext) -> str:
    """Look for `(<feature>: <value>)` groups in an at-rule prelude and
    evaluate the `<value>` part as a Less expression so arithmetic
    folds (`(min-width: (60px + 1))` → `(min-width: 61px)`). Idempotent
    when no arithmetic is present; bails out on parse errors.
    """

    def find_groups(text: str) -> list[tuple[int, int]]:
        groups: list[tuple[int, int]] = []
        depth = 0
        start = -1
        in_str: str | None = None
        for i, ch in enumerate(text):
            if in_str:
                if ch == in_str:
                    in_str = None
                continue
            if ch in ('"', "'"):
                in_str = ch
                continue
            if ch == '(':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0 and start >= 0:
                    groups.append((start, i + 1))
                    start = -1
        return groups

    out = prelude
    # Process outer-to-inner by repeated scans until no further folding.
    for _ in range(8):
        groups = find_groups(out)
        if not groups:
            return out
        changed = False
        new = []
        last = 0
        for s, e in groups:
            chunk = out[s + 1 : e - 1]
            colon_pos = chunk.find(':')
            if colon_pos == -1:
                continue
            key = chunk[:colon_pos].strip()
            # Feature names are simple idents; skip anything weirder.
            if not key or not all(c.isalnum() or c in '-' for c in key):
                continue
            val = chunk[colon_pos + 1 :].strip()
            if '(' not in val:
                continue  # nothing to fold
            try:
                parsed = parse_value_text(val)
                evaled = eval_value(parsed, ctx)
                folded = value_to_css(evaled)
            except (LessError, Exception):
                continue
            if folded == val:
                continue
            new.append(out[last:s])
            new.append(f'({key}: {folded})')
            last = e
            changed = True
        if not changed:
            return out
        new.append(out[last:])
        out = ''.join(new)
    return out


def _split_top_level_commas(text: str) -> list[str]:
    """Split `text` on commas that aren't nested inside `(...)`, `[...]`,
    `{...}`, or a quoted string. Trims each piece.
    """
    pieces: list[str] = []
    depth = 0
    in_string: str | None = None
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string is not None:
            if ch == '\\' and i + 1 < len(text):
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in ('"', "'"):
            in_string = ch
        elif ch in '([{':
            depth += 1
        elif ch in ')]}' and depth > 0:
            depth -= 1
        elif ch == ',' and depth == 0:
            pieces.append(text[start:i])
            start = i + 1
        i += 1
    pieces.append(text[start:])
    return [p.strip() for p in pieces if p.strip()]
