"""`CssEmitter` — the final CSS serialization walker.

Takes a fully-evaluated tree (post `apply_extends`, `merge_rules`,
`bubble_atrules`, …) with `paths` populated by `join_selectors`, and
writes out the CSS text.

One walker drives both plain CSS and source-map output: `write()`
accumulates the text (and, when a `mapper` is present, tracks the output
`(line, col)`), and `record(node)` stamps a position→source mapping via
the `mapper`. With no mapper the walk is a plain serializer; the
`emit_with_map` path (in `lessish.source_map`) passes a mapper that
records Source Maps v3 segments. `emit_css` is the thin no-mapper
wrapper.
"""

from __future__ import annotations

from typing import Protocol

from ..ast_nodes import AtRule, Comment, Declaration, Node, Ruleset
from ..errors import EvalError
from .atrules import _MEDIA_LIKE_AT_RULES, _is_inline_atrule, _normalize_media_prelude
from .checks import (
    _atrule_has_emittable_content,
    _has_extend_added_descendant,
    _ruleset_has_emittable_content,
)
from .compress import _compress_atrule_prelude, _compress_selector, _compress_value
from .emitter import Emitter, _important_suffix, neutralize_style_breakout


class EmitMapper(Protocol):
    """Sink for source-map segment recording. `record` is called at the
    output position of each mappable node (selector head, declaration,
    at-rule head, comment). The plain CSS path passes no mapper."""

    def record(self, node: Node, out_line: int, out_col: int) -> None: ...


class CssEmitter:
    """Serializes an evaluated tree (with `paths` populated) to CSS.

    Top-level variable declarations are dropped. Each nested Ruleset
    emits its own block at top level (its `paths` already encode the
    ancestor chain). Empty rulesets (no non-variable declarations) emit
    no block but still drive their children. `compress=True` collapses
    to single-line, no-extra-whitespace output (less.js `compress`).

    URL rewriting / rootpath prefixing is handled inside the import pass
    (see `_rewrite_urls_in_rules`), not here — by emit time the
    `url(...)` values are already final.
    """

    def __init__(
        self,
        *,
        indent: str = '  ',
        newline: str = '\n',
        compress: bool = False,
        strict_units: bool = False,
        neutralize_escape: bool = False,
        max_output_size: int | None = None,
        mapper: EmitMapper | None = None,
    ) -> None:
        if compress:
            indent = ''
            newline = ''
        self._indent = indent
        self._newline = newline
        self._compress = compress
        self._neutralize = neutralize_escape
        self._max_output_size = max_output_size
        # When set, `write` tracks `(self._line, self._col)` and `record`
        # forwards mappable node positions to it. None → plain CSS, and
        # position tracking is skipped entirely (the hot path).
        self._mapper = mapper
        self._colon = ':' if compress else ': '
        self._selector_open = '{' if compress else ' {'
        # `num_precision=8` triggers `fround` rounding in
        # `_format_dimension`; `strict_units` makes compound units raise.
        self._emitter = Emitter(num_precision=8, strict_units=strict_units)
        self._chunks: list[str] = []
        self._output_total = 0
        self._line = 0
        self._col = 0

    def write(self, s: str) -> None:
        if not s:
            return
        # `</`-neutralisation runs per chunk, before length accounting,
        # so the six-digit escape's extra bytes are reflected in
        # `self._col` and source-map segments stay aligned. `</` can't
        # split across two writes (the structural chunk after a value is
        # always `;`/`}`/newline, never `/`), so per-chunk is equivalent
        # to whole-output neutralisation here.
        if self._neutralize:
            s = neutralize_style_breakout(s)
        if self._max_output_size is not None:
            self._output_total += len(s)
            if self._output_total > self._max_output_size:
                raise EvalError(
                    f'CSS output exceeded the {self._max_output_size}-byte limit '
                    '(max_output_size) — probable runaway expansion'
                )
        self._chunks.append(s)
        if self._mapper is not None:
            nl = s.rfind('\n')
            if nl == -1:
                self._col += len(s)
            else:
                self._line += s.count('\n')
                self._col = len(s) - nl - 1

    def record(self, node: Node) -> None:
        if self._mapper is not None:
            self._mapper.record(node, self._line, self._col)

    def render(self) -> str:
        return ''.join(self._chunks)

    def _decl_value(self, d: Declaration) -> str:
        return self._emitter.emit_decl_value(d)

    def emit_root(self, root: Ruleset) -> None:
        assert root.root, 'CssEmitter.emit_root expects the root Ruleset'
        for r in root.rules:
            if isinstance(r, Ruleset):
                self.emit_ruleset(r)
            elif isinstance(r, AtRule):
                self.emit_atrule(r)
            elif isinstance(r, Comment) and not r.silent:
                if self._compress:
                    # In compress mode less.js preserves only `/*!`-
                    # prefixed comments. Skip the rest.
                    if r.text.startswith('/*!'):
                        self.write(r.text)
                    continue
                self.record(r)
                self.write(r.text)
                self.write(self._newline)
            # Variable / stray declarations at root level produce no CSS.

    def emit_ruleset(self, rs: Ruleset, outer: str = '') -> None:
        # `outer` is the indentation prefix prepended to every emitted
        # line — non-empty when this ruleset lives inside an at-rule body
        # (e.g. `@media { .x { … } }` indents `.x { … }` by one level).
        # In compress mode `outer` is always empty.
        if rs.reference:
            # `@import (reference) ...` — the imported subtree is kept
            # alive for mixin/extend resolution but never emits its own
            # CSS. Reference rulesets do emit any extend-added paths
            # though: `.x:extend(.ref-only)` (extender outside the
            # reference) clones the target's body under `.x`'s selector,
            # and that clone IS visible CSS. We render the body once with
            # `paths` temporarily replaced by the extend-added list.
            added_paths = rs._extend_added_paths or []
            if not added_paths:
                # No extend hit on THIS ruleset, but a deeper descendant
                # might (e.g. `.nestedToo { .class { … } }` inside a
                # `@supports` reference subtree, with `:extend(.class
                # all)` from outside). Only recurse when the subtree
                # actually contains an extend-added path — otherwise
                # non-reference content spliced inside a reference parent
                # (mixin invocation from a reference call site) would leak
                # out.
                if not _has_extend_added_descendant(rs):
                    return
                for r in rs.rules:
                    if isinstance(r, Ruleset):
                        self.emit_ruleset(r, outer)
                    elif isinstance(r, AtRule) and not _is_inline_atrule(r):
                        self.emit_atrule(r, outer)
                return
            saved = rs.paths
            saved_ref = rs.reference
            rs.paths = added_paths
            rs.reference = False
            try:
                self.emit_ruleset(rs, outer)
            finally:
                rs.paths = saved
                rs.reference = saved_ref
            return
        # Non-Ruleset body content: declarations, inline at-rules, and
        # block comments stay in-block (in source order). Nested Rulesets
        # and bubbleable at-rules emit at top level.
        in_block_entries: list[Node] = [
            r
            for r in rs.rules
            if (isinstance(r, Declaration) and not r.variable)
            or (isinstance(r, AtRule) and _is_inline_atrule(r))
            or (isinstance(r, AtRule) and r.name == '@__inline__')
            or (isinstance(r, Comment) and not r.silent)
        ]
        # less.js emits a block whenever there's content to put inside —
        # declarations, inline at-rules, or a comment (even alone). An
        # empty rule emits nothing.
        if rs.paths and in_block_entries:
            # Multi-selector rules emit one selector per line — matches
            # less.js's default output style. Single-selector rules remain
            # on one line. The outer indent is repeated for each
            # continuation line when emitting comma-joined selectors.
            paths_to_emit = rs.paths if not self._compress else [_compress_selector(p) for p in rs.paths]
            sel_joined = (
                (',' + self._newline + outer).join(paths_to_emit) if not self._compress else ','.join(paths_to_emit)
            )
            self.write(outer)
            # One segment at the head of a multi-selector rule;
            # per-selector segments would need a `paths_origin` field.
            self.record(rs)
            self.write(sel_joined)
            self.write(self._selector_open)
            self.write(self._newline)
            inner = outer + self._indent
            for i, entry in enumerate(in_block_entries):
                is_last = i == len(in_block_entries) - 1
                if isinstance(entry, Comment):
                    if self._compress:
                        if entry.text.startswith('/*!'):
                            self.write(entry.text)
                        continue
                    self.write(inner)
                    self.record(entry)
                    self.write(entry.text)
                    self.write(self._newline)
                    continue
                if isinstance(entry, AtRule) and entry.body is not None:
                    # Block-form nested at-rule emits its own indent (we
                    # pass it as `outer`); skip the declaration indent.
                    self.emit_atrule(entry, inner)
                    continue
                if isinstance(entry, AtRule) and entry.name == '@__inline__':
                    # `@import (inline)` inside a Ruleset body: dump the
                    # verbatim text indented at `inner`, no `@name` prefix,
                    # no trailing `;`. The `_inline_no_blank` tag tells the
                    # inline handler to skip the trailing blank line
                    # (less.js's Ruleset-wrap behaviour).
                    entry._inline_no_blank = True
                    try:
                        self.emit_atrule(entry, inner)
                    finally:
                        entry._inline_no_blank = False
                    continue
                self.write(inner)
                if isinstance(entry, Declaration):
                    self.record(entry)
                    self.write(entry.name)
                    self.write(self._colon)
                    self.write(_compress_value(self._decl_value(entry), self._compress))
                    if entry.important:
                        self.write(_important_suffix(entry, self._compress))
                else:
                    assert isinstance(entry, AtRule)
                    self.record(entry)
                    self.write(entry.name)
                    if entry.prelude:
                        self.write(' ')
                        self.write(entry.prelude)
                # In compress mode, omit `;` on the LAST declaration.
                if self._compress and is_last:
                    pass
                else:
                    self.write(';')
                self.write(self._newline)
            self.write(outer)
            self.write('}')
            self.write(self._newline)
        for r in rs.rules:
            if isinstance(r, Ruleset):
                self.emit_ruleset(r, outer)
            elif isinstance(r, AtRule) and not _is_inline_atrule(r) and r.name != '@__inline__':
                self.emit_atrule(r, outer)

    def emit_atrule(self, at: AtRule, outer: str = '') -> None:
        # At-rules bubbled out of a `reference=True` Ruleset inherit a
        # synthetic `_reference` flag (see `bubble_atrules`). Skip the
        # at-rule UNLESS one of its inner Rulesets has extend-added paths
        # — in that case the at-rule must wrap the emittable extender
        # clones.
        if at._reference and not _atrule_has_emittable_content(at):
            return
        # `@__inline__` is the sentinel the @import resolver uses for
        # `(inline)` imports — its prelude is the file's verbatim text,
        # dumped as-is without a leading name or trailing semicolon. No
        # segment is recorded because the imported text is opaque.
        if at.name == '@__inline__':
            if outer:
                # Inside a wrapping block (e.g. `@media (...) { @import
                # (inline) ...; }` or `div { @import (inline) ...; }`):
                # prefix every non-empty line of the inlined text with
                # `outer` so the content visually nests, matching less.js.
                for line in at.prelude.splitlines(keepends=True):
                    if line.strip():
                        self.write(outer)
                    self.write(line)
                # Ensure the block ends on a newline. less.js adds an
                # extra blank line before the wrapper's `}` for `@media {
                # @import (inline) … }` (its block formatter treats inline
                # imports as standalone paragraphs), but NOT for `.foo {
                # @import (inline) … }` Rulesets — those collapse content
                # directly against the closing brace. The flag is set by
                # `emit_ruleset` when it routes a `@__inline__` entry here.
                if not at.prelude.endswith('\n'):
                    self.write(self._newline)
                if not at._inline_no_blank:
                    self.write(self._newline)
            else:
                self.write(at.prelude)
                if at.prelude and not at.prelude.endswith('\n'):
                    self.write(self._newline)
                elif at.prelude:
                    # File content already terminated with `\n` — add one
                    # more so the next top-level rule is visually separated
                    # from the verbatim block. Files without a trailing
                    # newline leave the cursor right after their last char,
                    # matching less.js's "no synthetic separator".
                    self.write(self._newline)
            return
        prelude_text = at.prelude
        # `@supports` preludes round-trip verbatim in less.js — spaces
        # inside the boolean-expression parens (`( foo: bar )`) are
        # preserved, unlike `@media`'s aggressive query normalisation.
        if prelude_text and at.name in _MEDIA_LIKE_AT_RULES and at.name != '@supports':
            prelude_text = _normalize_media_prelude(prelude_text)
        # Compress mode: collapse top-level `, ` → `,` in any at-rule
        # prelude. The collapse only touches commas *outside* parens /
        # strings, so `@supports (foo: bar)` and quoted literals stay
        # intact.
        if self._compress and prelude_text:
            prelude_text = _compress_atrule_prelude(prelude_text)
        head = at.name + (' ' + prelude_text if prelude_text else '')
        if at.body is None:
            self.write(outer)
            self.record(at)
            self.write(head)
            self.write(';')
            self.write(self._newline)
            # Comments extracted from the prelude (e.g. `@import "x" /* c
            # */;`) emit after the at-rule statement at the same indent.
            for c in at.trailing_comments:
                if c.silent:
                    continue
                if self._compress:
                    if c.text.startswith('/*!'):
                        self.write(c.text)
                    continue
                self.write(outer)
                self.record(c)
                self.write(c.text)
                self.write(self._newline)
            return
        # less.js: an at-rule block whose body has no actual rules
        # (declarations / rulesets / non-inline at-rules) emits nothing —
        # even prelude-extracted comments are dropped with it. Only
        # `@-keyframes` keeps an empty body since its mere presence is
        # CSS-meaningful. A nested Ruleset only counts as content if it
        # would itself emit — empty `.x {}` shells don't.
        has_rules = any(
            (isinstance(r, Ruleset) and _ruleset_has_emittable_content(r))
            or isinstance(r, Declaration)
            or (isinstance(r, AtRule) and _atrule_has_emittable_content(r))
            or (isinstance(r, Comment) and not r.silent and not r._from_prelude)
            for r in at.body
        )
        if not has_rules and not at.name.endswith('keyframes'):
            return
        self.write(outer)
        self.record(at)
        self.write(head)
        self.write(self._selector_open)
        self.write(self._newline)
        # Nested content inside the at-rule body emits with one extra
        # indent level.
        inner = outer + self._indent
        body_decls = [r for r in at.body if isinstance(r, Declaration) and not r.variable]
        for i, d in enumerate(body_decls):
            is_last_decl = i == len(body_decls) - 1 and not any(isinstance(r, (Ruleset, AtRule)) for r in at.body)
            self.write(inner)
            self.record(d)
            self.write(d.name)
            self.write(self._colon)
            self.write(_compress_value(self._decl_value(d), self._compress))
            if d.important:
                self.write(_important_suffix(d, self._compress))
            if self._compress and is_last_decl:
                pass
            else:
                self.write(';')
            self.write(self._newline)
        for r in at.body:
            if isinstance(r, Ruleset):
                self.emit_ruleset(r, inner)
            elif isinstance(r, AtRule):
                self.emit_atrule(r, inner)
            elif isinstance(r, Comment) and not r.silent:
                if self._compress:
                    if r.text.startswith('/*!'):
                        self.write(r.text)
                    continue
                self.write(inner)
                self.record(r)
                self.write(r.text)
                self.write(self._newline)
        self.write(outer)
        self.write('}')
        self.write(self._newline)


def emit_css(
    root: Ruleset,
    *,
    indent: str = '  ',
    newline: str = '\n',
    compress: bool = False,
    strict_units: bool = False,
    max_output_size: int | None = None,
    neutralize_escape: bool = False,
) -> str:
    """Serialize an evaluated tree to CSS (no source map). Thin wrapper
    over `CssEmitter` with no mapper."""
    emitter = CssEmitter(
        indent=indent,
        newline=newline,
        compress=compress,
        strict_units=strict_units,
        max_output_size=max_output_size,
        neutralize_escape=neutralize_escape,
    )
    emitter.emit_root(root)
    return emitter.render()
