"""String functions: e (escape), escape (URL), replace, % (format)."""

from __future__ import annotations

import re
from urllib.parse import quote

from ..ast_nodes import Anonymous, Node, Quoted
from ..context import EvalContext
from ..errors import ArgumentError
from ..visitors import value_to_css
from . import register
from ._helpers import quoted_value, unwrap


@register('e')
def fn_e(args: list[Node], ctx: EvalContext) -> Node:
    """Return an escaped (`~"..."`-style) string. less.js converts any
    input to its string value and wraps in a `~"..."` quoted node.
    """
    if not args:
        raise ArgumentError('e() expects 1 argument')
    a = unwrap(args[0])
    if isinstance(a, Quoted):
        text = a.value
    else:
        text = value_to_css(a)
    return Quoted(index=a.index, quote='"', value=text, escaped=True)


# `safe` argument for `urllib.parse.quote` — JS `encodeURI`'s
# unreserved/reserved ASCII set. Only ASCII matters: `quote()` does
# `safe.encode('ascii', 'ignore')` internally, so any non-ASCII code
# point in `safe` is silently dropped. Non-ASCII input is therefore
# percent-encoded (UTF-8 bytes), matching `encodeURI` and the less.js
# corpus.
_ESCAPE_SAFE_CHARS = "-_.!~*'();/?:@&=+$,#"


@register('escape')
def fn_escape(args: list[Node], ctx: EvalContext) -> Node:
    """URL-style escape. less.js uses encodeURI plus a hand-rolled
    replacement of a few reserved chars; this mirrors that set exactly.
    """
    if not args:
        raise ArgumentError('escape() expects 1 argument')
    a = unwrap(args[0])
    text = a.value if isinstance(a, Quoted) else value_to_css(a)
    # encodeURI leaves these unescaped:
    #   A-Z a-z 0-9 - _ . ~ ! * ' ( ) ; , / ? : @ & = + $ #
    # then less.js further encodes =,:,#,;,(,)
    encoded = quote(text, safe=_ESCAPE_SAFE_CHARS, encoding='utf-8')
    # Match less.js's secondary replacements.
    for src, dst in (('=', '%3D'), (':', '%3A'), ('#', '%23'), (';', '%3B'), ('(', '%28'), (')', '%29')):
        encoded = encoded.replace(src, dst)
    return Anonymous(index=a.index, value=encoded)


_JS_BACKREF_RE = re.compile(r'\$(\$|\d{1,2}|&|\{[^}]+\})')


def _js_to_py_replacement(repl: str) -> str:
    """Translate JS-regex replacement syntax (`$1`, `$$`, `${name}`) to
    Python's `re.sub` syntax (`\\1`, `$`, `\\g<name>`). less.js feeds the
    user's replacement string straight into `String.replace`, so user
    Less code expects JS-style back-references — `replace("…world…",
    "(world)", "new $1")` → `"new world"`. Python's `re.sub` would treat
    the literal `$1` as text without this rewrite.
    """
    if '$' not in repl:
        return repl
    # Escape Python's own `\1`-style back-references so the user's
    # literal `\1` in the replacement isn't interpreted as a group ref.
    out: list[str] = []
    i = 0
    n = len(repl)
    while i < n:
        ch = repl[i]
        if ch == '\\':
            # Escape the backslash so re.sub treats it literally.
            out.append('\\\\')
            i += 1
            continue
        if ch == '$':
            if i + 1 < n:
                nxt = repl[i + 1]
                if nxt == '$':
                    out.append('$')
                    i += 2
                    continue
                if nxt.isdigit():
                    # `$1`..`$99` — JS supports two-digit refs too.
                    j = i + 1
                    while j < n and repl[j].isdigit() and j - i <= 2:
                        j += 1
                    out.append('\\g<' + repl[i + 1 : j] + '>')
                    i = j
                    continue
                if nxt == '&':
                    out.append('\\g<0>')
                    i += 2
                    continue
                if nxt == '{':
                    end = repl.find('}', i + 2)
                    if end != -1:
                        out.append('\\g<' + repl[i + 2 : end] + '>')
                        i = end + 1
                        continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _coerce_string_arg(node: Node, fn_name: str) -> tuple[str, str, bool]:
    """Coerce `node` to (text, quote-char, escaped-flag).

    less.js's `replace()` accepts any node as the haystack: Quoted
    contributes its value + quote, everything else is rendered via
    toCSS and wrapped in an empty-quote Quoted (so the result emits
    unquoted). Mirror that — Keyword (`baz-1`), Anonymous, even
    Dimensions are valid inputs.
    """
    node = unwrap(node)
    if isinstance(node, Quoted):
        return node.value, node.quote, node.escaped
    return value_to_css(node), '', True


def _scan_unbounded_quantifier(s: str, i: int) -> int:
    """If `s[i:]` opens an UNBOUNDED quantifier (`*`, `+`, or `{n,}`
    with no upper bound), return the index just past it (consuming a
    trailing lazy/possessive `?`/`+`); otherwise -1. Bounded forms
    (`?`, `{n,m}`, `{n}`) are not unbounded and return -1.
    """
    if i >= len(s):
        return -1
    c = s[i]
    if c in '*+':
        j = i + 1
        if j < len(s) and s[j] in '?+':  # lazy or possessive suffix
            j += 1
        return j
    if c == '{':
        m = re.match(r'\{\d*,(\d*)\}', s[i:])
        if m and m.group(1) == '':  # `{n,}` — no upper bound
            return i + m.end()
    return -1


def _scan_repeating_quantifier(s: str, i: int) -> int:
    """If `s[i:]` opens a quantifier that permits **two or more**
    repetitions (`*`, `+`, `{n,}`, `{n,m}` with `m>=2`, `{n}` with
    `n>=2`), return the index just past it; otherwise -1.

    Broader than `_scan_unbounded_quantifier`: a *bounded* outer count
    is still catastrophic when it wraps a variable-length group —
    `(a+){25}` on a non-match makes `re` explore the ways to partition
    the run of `a`s across 25 groups, a degree-24 polynomial that is
    unusable at the 100k input cap. `?`/`{0,1}`/`{1}` (at most one rep)
    are safe and return -1.
    """
    if i >= len(s):
        return -1
    c = s[i]
    if c in '*+':
        j = i + 1
        if j < len(s) and s[j] in '?+':  # lazy or possessive suffix
            j += 1
        return j
    if c == '{':
        m = re.match(r'\{(\d*)(,(\d*))?\}', s[i:])
        if m:
            low = int(m.group(1)) if m.group(1) else 0
            if m.group(2) is None:  # `{n}` — exactly n
                upper: int | None = low
            elif m.group(3) == '':  # `{n,}` — unbounded
                upper = None
            else:  # `{n,m}`
                upper = int(m.group(3))
            if upper is None or upper >= 2:
                return i + m.end()
    return -1


def _skip_char_class(s: str, i: int) -> int:
    """Given `s[i] == '['`, return the index just past the closing `]`
    of the character class (so quantifier chars inside it — which are
    literal — are not mistaken for repetition operators).
    """
    j = i + 1
    if j < len(s) and s[j] == '^':
        j += 1
    if j < len(s) and s[j] == ']':  # a `]` as the first member is literal
        j += 1
    while j < len(s) and s[j] != ']':
        j += 2 if s[j] == '\\' else 1
    return j + 1


def _body_has_unbounded_quantifier(body: str) -> bool:
    """True if `body` contains any unbounded repetition operator at any
    nesting level (escapes and character classes skipped)."""
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == '\\':
            i += 2
            continue
        if c == '[':
            i = _skip_char_class(body, i)
            continue
        if _scan_unbounded_quantifier(body, i) != -1:
            return True
        i += 1
    return False


def _split_top_level_alternation(body: str) -> list[str]:
    """Split a group body on its top-level `|`, skipping nested groups,
    character classes, and escapes. `'a|ab'` → `['a', 'ab']`."""
    parts: list[str] = []
    depth = 0
    start = 0
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == '\\':
            i += 2
            continue
        if c == '[':
            i = _skip_char_class(body, i)
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif c == '|' and depth == 0:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    parts.append(body[start:])
    return parts


def _alt_first_signature(alt: str) -> str:
    """A coarse signature of what `alt` can match at its first position.
    `''` (an empty alternative) and `'*'` (a leading metachar that can
    match many things) both mean "overlaps with anything"."""
    if not alt:
        return ''
    c = alt[0]
    if c == '\\':
        return alt[:2]
    if c in '.[(^$':
        return '*'
    return c


def _alternation_overlaps(body: str) -> bool:
    """True if `body`'s top-level alternatives can match a common prefix
    — `(a|a)`, `(a|ab)`, `(a|.)`, `(a|)`. Such overlap under an unbounded
    quantifier is the catastrophic-backtracking signature alternation
    produces (`(a|a)*` on a long non-match). Disjoint alternations
    (`(px|em|rem)`) have distinct first characters and are linear."""
    alts = _split_top_level_alternation(body)
    if len(alts) < 2:
        return False
    seen: set[str] = set()
    for alt in alts:
        sig = _alt_first_signature(alt)
        if sig in ('', '*') or sig in seen:
            return True
        seen.add(sig)
    return False


def _has_nested_unbounded_quantifier(pattern: str) -> bool:
    """Detect catastrophic-backtracking signatures: a **repeating**
    quantifier (two-or-more reps: `*`, `+`, `{n,}`, and bounded
    `{n,m}`/`{n}` with count >= 2) applied to a group whose body either
    contains its own unbounded quantifier (`(a+)+`, `(\\d*)*`, `(.+){25}`)
    or holds overlapping top-level alternatives (`(a|a)*`, `(a|ab){9}`).

    Python's `re` has no backtracking budget, so a single such pattern on
    a non-matching input pins a CPU for seconds-to-hours. The check is
    conservative: it leaves ordinary single-quantifier groups (`(\\w+)`,
    `(l+)`), at-most-one outer reps (`(a+)?`, `(a+){1}`), and disjoint
    alternations (`(px|em)*`) alone because none can backtrack
    super-linearly. It can over-reject a benign repeating group; that is
    deliberate.

    It is a best-effort backstop, NOT a guarantee — e.g. variable
    bounded nesting without an unbounded inner (`(a{1,3}){25}`) is not
    caught. Untrusted-input embedders must disable `replace` entirely
    (see `RESTRICTED_FUNCTIONS` / `Lessish.hardened()`); the residual
    ReDoS lives in C-level regex that no Python-level deadline can
    interrupt."""
    stack: list[int] = []  # body-start index of each open group
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == '\\':
            i += 2
            continue
        if c == '[':
            i = _skip_char_class(pattern, i)
            continue
        if c == '(':
            stack.append(i + 1)
            i += 1
            continue
        if c == ')':
            body_start = stack.pop() if stack else None
            after = _scan_repeating_quantifier(pattern, i + 1)
            if after != -1 and body_start is not None:
                body = pattern[body_start:i]
                if _body_has_unbounded_quantifier(body) or _alternation_overlaps(body):
                    return True
            i += 1
            continue
        i += 1
    return False


@register('replace')
def fn_replace(args: list[Node], ctx: EvalContext) -> Node:
    """`replace(string, pattern, replacement[, flags])` — regex-based."""
    if len(args) < 3:
        raise ArgumentError('replace() expects 3 or 4 arguments')
    s_text, s_quote, s_escaped = _coerce_string_arg(args[0], 'replace')
    pat = unwrap(args[1])
    pattern_text = pat.value if isinstance(pat, Quoted) else value_to_css(pat)
    repl = unwrap(args[2])
    repl_text = repl.value if isinstance(repl, Quoted) else value_to_css(repl)
    # DoS guards. `re` exposes no step/time budget, so untrusted Less
    # could otherwise hand `replace()` a catastrophic-backtracking
    # pattern (e.g. `(a+)+$`) and pin a CPU. Bound the input size and
    # reject the classic nested-quantifier signature. See `EvalContext`
    # `replace_input_limit` and `_has_nested_unbounded_quantifier`.
    # `getattr` fallback keeps the function usable when called directly
    # with a bare/None ctx (unit tests, tooling) — the pipeline always
    # supplies a real `EvalContext`.
    limit = getattr(ctx, 'replace_input_limit', 100_000)
    if len(pattern_text) > limit or len(s_text) > limit:
        err = ArgumentError(
            f'replace(): pattern/subject exceeds the {limit}-char limit '
            '(raise the `replace_input_limit` compile option if intended)'
        )
        err._fatal = True
        raise err
    if _has_nested_unbounded_quantifier(pattern_text):
        err = ArgumentError(
            f'replace(): pattern {pattern_text!r} risks catastrophic backtracking '
            '(a quantifier applied to a quantified group, or overlapping alternatives '
            'under a quantifier) and was rejected; rewrite the pattern, or disable '
            '`replace` for untrusted input (see RESTRICTED_FUNCTIONS)'
        )
        err._fatal = True
        raise err
    flags_str = ''
    if len(args) >= 4:
        f = unwrap(args[3])
        flags_str = f.value if isinstance(f, Quoted) else value_to_css(f)
    py_flags = 0
    if 'i' in flags_str:
        py_flags |= re.IGNORECASE
    if 'm' in flags_str:
        py_flags |= re.MULTILINE
    count = 0 if 'g' in flags_str else 1
    result = re.sub(pattern_text, _js_to_py_replacement(repl_text), s_text, count=count, flags=py_flags)
    return Quoted(index=args[0].index, quote=s_quote, value=result, escaped=s_escaped)


_FMT_RE = re.compile(r'%[sdaSDA]')


@register('%')
def fn_format(args: list[Node], ctx: EvalContext) -> Node:
    """`%("template %s %d", arg1, arg2)` — printf-ish. Uppercase tokens
    URL-encode the substituted value (less.js parity).
    """
    if not args:
        raise ArgumentError('%() expects at least 1 argument')
    s = quoted_value(args[0], fn_name='%')
    template = s.value
    rest = args[1:]
    idx = 0
    result: list[str] = []
    last = 0
    for m in _FMT_RE.finditer(template):
        result.append(template[last : m.start()])
        last = m.end()
        if idx >= len(rest):
            result.append(m.group(0))
            continue
        a = unwrap(rest[idx])
        idx += 1
        token = m.group(0)
        if token.lower() == '%s' and isinstance(a, Quoted):
            substituted = a.value
        else:
            substituted = value_to_css(a)
        if token[-1].isupper():
            substituted = quote(substituted, safe='')
        result.append(substituted)
    result.append(template[last:])
    text = ''.join(result).replace('%%', '%')
    return Quoted(index=s.index, quote=s.quote, value=text, escaped=s.escaped)
