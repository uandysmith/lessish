"""Mixin parameter and argument list parsing.

Used by `transform_mixins` (definition signatures) and the call-site
`_parse_mixin_call_text` (invocation argument lists). Implements
less.js's comma/semicolon delimiter rules and the strict mixed-delimiter
rejection.
"""

from __future__ import annotations

import re

from ..ast_nodes import MixinArg, MixinParam
from ..errors import ParseError as _ParseError
from ..parser import parse_value_text


def parse_mixin_params(text: str) -> list[MixinParam]:
    """Parse a comma-separated parameter list (contents of `(...)` in a
    definition). Each piece is one of:

      * `@name`             — variable param, required
      * `@name: default`    — variable param with default value
      * `@rest...`          — variadic tail (collects overflow positionals)
      * `red`, `5px`, ...   — pattern param (literal-equality match)
    """
    if not text.strip():
        return []
    # Strip `//` and `/* */` comments so the comma split doesn't fall
    # into a line-comment tail.
    text = _strip_comments_from_args(text)
    params: list[MixinParam] = []
    for raw in _split_top_level(text):
        piece = raw.strip()
        if not piece:
            continue
        if piece.endswith('...'):
            base = piece[:-3].strip()
            params.append(MixinParam(index=0, name=base, default=None, variadic=True))
            continue
        if piece.startswith('@'):
            if ':' in piece:
                name, default_src = piece.split(':', 1)
                params.append(
                    MixinParam(
                        index=0,
                        name=name.strip(),
                        default=parse_value_text(default_src.strip()),
                    )
                )
            else:
                params.append(MixinParam(index=0, name=piece, default=None))
        else:
            # Pattern (literal) param — doesn't bind, gates match.
            params.append(
                MixinParam(
                    index=0,
                    name='',
                    pattern=parse_value_text(piece),
                )
            )
    return params


# Named-arg form: `@name: value`. Matched conservatively — `@` + ident-ish
# chars, then `:`, then anything.
_NAMED_ARG_RE = re.compile(r'^\s*(@[_a-zA-Z][\w-]*)\s*:\s*(.*)$', re.DOTALL)


def parse_mixin_args(text: str, *, base_index: int = 0) -> list[MixinArg]:
    """Parse a comma-separated argument list (contents of `(...)` in a
    call). Each piece is either positional or named (`@name: value`).

    `base_index` is the source offset of the start of `text`; on a
    strict-error (mixed `;`/`,` delimiters) it's attached to the raised
    `ParseError` so the location lands at the right column.
    """
    if not text.strip():
        return []
    # Strip `//` line comments and `/* */` block comments from the
    # argument text. less.js drops both before structural parsing —
    # without this, `(@a: white, // in\n  @b: 1px)` confuses the
    # named-arg regex and the value parser.
    text = _strip_comments_from_args(text)

    try:
        pieces = _split_top_level(text)
    except _ParseError as e:
        if e.location is None:
            # `_split_top_level` raises with `_base_index` set to an
            # offset *within* the args text; shift it into the outer
            # source so error reporting picks the right line/column.
            in_text = e._base_index or 0
            e._base_index = base_index + in_text
        raise

    args: list[MixinArg] = []
    for raw in pieces:
        piece = raw.strip()
        if not piece:
            continue
        m = _NAMED_ARG_RE.match(piece)
        if m is not None:
            args.append(
                MixinArg(
                    index=0,
                    name=m.group(1),
                    value=parse_value_text(m.group(2).strip()),
                )
            )
        else:
            args.append(MixinArg(index=0, name=None, value=parse_value_text(piece)))
    return args


def _strip_comments_from_args(text: str) -> str:
    """Remove `//` line and `/* */` block comments from raw mixin-args
    text. Strings (`"..."`, `'...'`, `~"..."`) are preserved.
    """
    if '//' not in text and '/*' not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'") or (ch == '~' and i + 1 < n and text[i + 1] in ('"', "'")):
            start_q = i if ch in ('"', "'") else i + 1
            quote = text[start_q]
            out.append(text[i:start_q])
            out.append(text[start_q])
            j = start_q + 1
            while j < n:
                out.append(text[j])
                if text[j] == '\\' and j + 1 < n:
                    out.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            i = j
            continue
        if ch == '/' and i + 1 < n:
            if text[i + 1] == '*':
                end = text.find('*/', i + 2)
                if end == -1:
                    break
                i = end + 2
                continue
            if text[i + 1] == '/':
                # Line comment — skip to end of line (or end of text).
                nl = text.find('\n', i + 2)
                i = n if nl == -1 else nl + 1
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _split_top_level(text: str) -> list[str]:
    """Split `text` on `,` at paren depth zero. Brackets/braces are also
    counted so values like `rgba(0, 0, 0, 0.5)` and detached rulesets
    aren't accidentally split. Strings (single/double-quoted, also the
    `~"..."` escaped form) are skipped over so a comma inside a string
    literal doesn't terminate the argument.

    less.js's rule: `;` takes precedence over `,` when both appear at
    top level. The `,` then becomes part of the inner list of the
    `;`-separated args (so `each(a, b; { … })` is two args, the first
    being the comma-list `a, b`).

    Strict rejection: when `;` and `,` are both used as delimiters AND
    the arg list contains *named* arguments (`@name: value`), less.js
    raises `Cannot mix ; and , as delimiter types` — we mirror that
    here, with the anchor offset matching less.js's parser position.
    """
    if not text:
        return []
    mix_pos = _detect_mixed_delim_pos(text)
    if mix_pos is not None:
        err = _ParseError('Cannot mix ; and , as delimiter types')
        err._less_js_name = 'SyntaxError'
        err._base_index = mix_pos
        raise err
    if _has_top_level_separator(text, ';'):
        return _split_on(text, ';')
    return _split_on(text, ',')


def _detect_mixed_delim_pos(text: str) -> int | None:
    """Simulate less.js's named-arg-mixing detection over an arg-list
    text. Returns the in-text offset (0-indexed) where less.js anchors
    the `Cannot mix ; and , as delimiter types` error, or None.

    Mirrors `parsers.entities.arguments` in less.js's parser.js:
    * Iteration over `;`-separated groups.
    * Inside each group, comma-separated args.
    * Error fires at one of two points:
      - Inside a named-arg `:` consumption when the group already has
        expressions AND `;` was previously seen (Case A — `parser.js`
        line 1097).
      - At the end of a group when `;` is hit AND the group's
        expressions contain a named arg `@name:` not at position 0
        (Case B — `parser.js` line 1147).
    Position equals less.js's `parserInput.i` right after consuming
    the offending `:` / `;` AND its trailing whitespace.
    """
    n = len(text)

    def skip_ws(j: int) -> int:
        while j < n and text[j] in ' \t\n':
            j += 1
        return j

    def scan_to_sep(j: int) -> int:
        """Advance past one arg's value text — stop at top-level `,`
        or `;`, or EOF. Tracks paren/bracket/brace nesting and
        single/double-quoted strings."""
        depth_p = depth_b = depth_c = 0
        while j < n:
            c = text[j]
            if c in ('"', "'"):
                j = _skip_string(text, j)
                continue
            if c == '~' and j + 1 < n and text[j + 1] in ('"', "'"):
                j = _skip_string(text, j + 1)
                continue
            if c == '(':
                depth_p += 1
            elif c == ')':
                depth_p = max(0, depth_p - 1)
            elif c == '[':
                depth_b += 1
            elif c == ']':
                depth_b = max(0, depth_b - 1)
            elif c == '{':
                depth_c += 1
            elif c == '}':
                depth_c = max(0, depth_c - 1)
            elif depth_p == 0 and depth_b == 0 and depth_c == 0 and c in (',', ';'):
                return j
            j += 1
        return j

    is_semi_sep = False
    i = 0
    while i < n:
        i = skip_ws(i)
        if i >= n:
            break

        # New `;`-group.
        expressions_in_group = 0
        group_has_named_after_first = False

        while True:
            i = skip_ws(i)
            if i >= n:
                return None

            # Probe for `@name:` (or `$name:` — less.js's `Property`).
            colon_pos: int | None = None
            if i < n and text[i] in ('@', '$'):
                j = i + 1
                # Optional second `@` for `@@name` (variable variable).
                if j < n and text[j] == '@' and text[i] == '@':
                    j += 1
                while j < n and (text[j].isalnum() or text[j] in '-_'):
                    j += 1
                after_name = skip_ws(j)
                if after_name < n and text[after_name] == ':':
                    colon_pos = after_name

            if colon_pos is not None:
                # Case A: named arg encountered with `;` already in
                # play and prior expressions in this group.
                pos_after_colon = skip_ws(colon_pos + 1)
                if expressions_in_group > 0 and is_semi_sep:
                    return pos_after_colon
                if expressions_in_group > 0:
                    group_has_named_after_first = True
                i = pos_after_colon

            # Consume the value text up to the next top-level
            # `,` / `;` or EOF.
            i = scan_to_sep(i)
            expressions_in_group += 1

            if i >= n:
                return None
            if text[i] == ',':
                i += 1
                continue  # next arg in same `;`-group
            # text[i] == ';'
            # Case B: `;` ends a group whose args mixed `,`-separated
            # named entries.
            pos_after_semi = skip_ws(i + 1)
            if group_has_named_after_first:
                return pos_after_semi
            is_semi_sep = True
            i += 1
            break  # close group, start next
    return None


def _has_top_level_separator(text: str, sep: str) -> bool:
    depth_paren = 0
    depth_bracket = 0
    depth_brace = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ('"', "'"):
            i = _skip_string(text, i)
            continue
        if ch == '~' and i + 1 < len(text) and text[i + 1] in ('"', "'"):
            i = _skip_string(text, i + 1)
            continue
        if ch == '(':
            depth_paren += 1
        elif ch == ')':
            depth_paren = max(0, depth_paren - 1)
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace = max(0, depth_brace - 1)
        elif ch == sep and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
            return True
        i += 1
    return False


def _split_on(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth_paren = 0
    depth_bracket = 0
    depth_brace = 0
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ('"', "'"):
            end = _skip_string(text, i)
            current.append(text[i:end])
            i = end
            continue
        if ch == '~' and i + 1 < len(text) and text[i + 1] in ('"', "'"):
            end = _skip_string(text, i + 1)
            current.append(text[i:end])
            i = end
            continue
        if ch == '(':
            depth_paren += 1
        elif ch == ')':
            depth_paren = max(0, depth_paren - 1)
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace = max(0, depth_brace - 1)
        elif ch == sep and depth_paren == 0 and depth_bracket == 0 and depth_brace == 0:
            parts.append(''.join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current:
        parts.append(''.join(current))
    return parts


def _skip_string(text: str, start: int) -> int:
    """Return the index just past the closing quote of the string starting
    at `text[start]`. Handles `\\.` escapes. Falls back to end-of-text if
    unterminated.
    """
    quote = text[start]
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == '\\' and i + 1 < len(text):
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return len(text)


def _parse_mixin_call_text(text: str, *, base_index: int = 0) -> tuple[str, list[MixinArg], bool]:
    """Split `.name(args)!important` (or paren-less `.name`) into its
    pieces. Returns (name, args, important).

    `base_index` is the source offset of `text[0]`; it's forwarded to
    `parse_mixin_args` so a strict-error inside the arg list (mixed
    `;`/`,` delimiters) carries an absolute-source anchor.
    """
    text_stripped = text.lstrip()
    leading_ws = len(text) - len(text_stripped)
    text = text_stripped.rstrip()
    important = False
    if text.endswith('!important'):
        text = text[: -len('!important')].rstrip()
        important = True
    paren_idx = text.find('(')
    if paren_idx == -1:
        return text, [], important
    name = text[:paren_idx].rstrip()
    rest = text[paren_idx + 1 :]
    depth = 1
    end = -1
    for i, ch in enumerate(rest):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return name, [], important
    args_base = base_index + leading_ws + paren_idx + 1
    args = parse_mixin_args(rest[:end], base_index=args_base)
    return name, args, important
