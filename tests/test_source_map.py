"""Tests for source-map generation (`source_map.py` + public API)."""

from __future__ import annotations

import base64
import json
import unittest
from typing import cast

from lessish import Lessish
from lessish.source_map import (
    VLQ,
    SourceMapBuilder,
    SourceMapResult,
    _apply_basepath_rootpath,
    _resolve_opts,
    _SourceMapper,
)
from lessish.visitors.emit import CssEmitter


def _map_emitter(builder: SourceMapBuilder, src: object) -> CssEmitter:
    """A `CssEmitter` wired to record into `builder` — the structural
    branches these tests exercise live on the one unified walker now."""
    return CssEmitter(mapper=_SourceMapper(builder, src))  # type: ignore[arg-type]


_default = Lessish()
compile = _default.compile
compile_with_source_map = _default.compile_with_source_map


class TestVLQ(unittest.TestCase):
    """Spot-checks against the canonical Source Maps v3 VLQ examples."""

    def test_zero(self) -> None:
        self.assertEqual(VLQ.encode_int(0), 'A')

    def test_small_positive(self) -> None:
        self.assertEqual(VLQ.encode_int(1), 'C')
        self.assertEqual(VLQ.encode_int(15), 'e')

    def test_small_negative(self) -> None:
        self.assertEqual(VLQ.encode_int(-1), 'D')
        self.assertEqual(VLQ.encode_int(-15), 'f')

    def test_multibyte(self) -> None:
        # 16 needs a second base64 char.
        self.assertEqual(VLQ.encode_int(16), 'gB')
        self.assertEqual(VLQ.encode_int(-16), 'hB')

    def test_three_bytes(self) -> None:
        self.assertEqual(VLQ.encode_int(1024), 'ggC')

    def test_segment_concat(self) -> None:
        # A four-field segment (0, 0, 0, 0) → 'AAAA'.
        self.assertEqual(VLQ.encode_segment([0, 0, 0, 0]), 'AAAA')


class TestSourceMapBuilder(unittest.TestCase):
    def test_empty(self) -> None:
        b = SourceMapBuilder(file='x.css')
        self.assertEqual(b.build_mappings(), '')
        d = b.build(include_sources_content=False)
        self.assertEqual(d['version'], 3)
        self.assertEqual(d['sources'], [])
        self.assertEqual(d['mappings'], '')

    def test_single_segment(self) -> None:
        b = SourceMapBuilder(file='x.css')
        src_idx = b.add_source('x.less', None)
        b.add_segment(out_line=0, out_col=0, src_idx=src_idx, src_line=0, src_col=0)
        self.assertEqual(b.build_mappings(), 'AAAA')

    def test_multi_line(self) -> None:
        b = SourceMapBuilder(file='x.css')
        b.add_source('x.less', None)
        b.add_segment(0, 0, 0, 0, 0)
        b.add_segment(1, 2, 0, 0, 5)
        self.assertEqual(b.build_mappings(), 'AAAA;EAAK')

    def test_sources_content_alignment(self) -> None:
        b = SourceMapBuilder(file='x.css')
        b.add_source('a.less', 'A')
        b.add_source('b.less', None)
        d = b.build(include_sources_content=True)
        self.assertEqual(d['sourcesContent'], ['A', None])

    def test_duplicate_source_promotes_content(self) -> None:
        b = SourceMapBuilder(file='x.css')
        i1 = b.add_source('a.less', None)
        i2 = b.add_source('a.less', 'BODY')
        self.assertEqual(i1, i2)
        self.assertEqual(b._sources_content, ['BODY'])


class TestIntegrationBasic(unittest.TestCase):
    def test_simple_rule_has_mapping(self) -> None:
        result = compile_with_source_map('.a { color: red; }', filename='x.less')
        self.assertIsInstance(result, SourceMapResult)
        self.assertEqual(result.css, '.a {\n  color: red;\n}\n/*# sourceMappingURL=x.css.map */')
        self.assertEqual(result.annotation_url, 'x.css.map')
        m = json.loads(result.map_json)
        self.assertEqual(m['version'], 3)
        self.assertEqual(m['file'], 'x.css')
        self.assertEqual(m['sources'], ['x.less'])
        # Two segments: selector at (0,0) and declaration at (1,2).
        self.assertEqual(m['mappings'], 'AAAA;EAAK')

    def test_nested_selectors_join(self) -> None:
        # `.a { .b { color: red; } }` joins to selector `.a .b`. Segment
        # records `.b`'s original index — the parent doesn't bleed through.
        result = compile_with_source_map('.a { .b { color: red; } }', filename='x.less')
        # Sanity: the joined selector appears in the rendered CSS.
        self.assertIn('.a .b', result.css)
        m = json.loads(result.map_json)
        # First segment is at out(0,0) → src(0,?) — pointing at `.a` (the
        # Ruleset that owns the joined path).
        self.assertTrue(m['mappings'])


class TestAnnotationOptions(unittest.TestCase):
    def test_default_url(self) -> None:
        result = compile_with_source_map('.a { c: 1px; }', filename='foo/bar.less')
        self.assertEqual(result.annotation_url, 'bar.css.map')
        self.assertTrue(result.css.endswith('/*# sourceMappingURL=bar.css.map */'))

    def test_source_map_url_override(self) -> None:
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='x.less',
            source_map={'sourceMapURL': '../custom-path/x.map'},
        )
        self.assertEqual(result.annotation_url, '../custom-path/x.map')
        self.assertTrue(result.css.endswith('sourceMappingURL=../custom-path/x.map */'))

    def test_disable_annotation(self) -> None:
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='x.less',
            source_map={'disableSourcemapAnnotation': True},
        )
        self.assertNotIn('sourceMappingURL', result.css)
        self.assertIsNone(result.annotation_url)
        # Map JSON is still produced and valid.
        m = json.loads(result.map_json)
        self.assertEqual(m['version'], 3)

    def test_inline_data_uri(self) -> None:
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='x.less',
            source_map={'sourceMapFileInline': True},
        )
        self.assertIsNotNone(result.annotation_url)
        ann = cast(str, result.annotation_url)
        self.assertTrue(ann.startswith('data:application/json;base64,'))
        # The embedded base64 must decode to the same JSON as result.map_json.
        b64 = ann[len('data:application/json;base64,') :]
        decoded = base64.b64decode(b64).decode('utf-8')
        self.assertEqual(json.loads(decoded), json.loads(result.map_json))


class TestSourceContent(unittest.TestCase):
    def test_output_source_files_embeds_entry(self) -> None:
        src = '.a { color: red; }'
        result = compile_with_source_map(
            src,
            filename='x.less',
            source_map={'outputSourceFiles': True},
        )
        m = json.loads(result.map_json)
        self.assertEqual(m['sourcesContent'], [src])

    def test_without_output_source_files_no_content(self) -> None:
        result = compile_with_source_map('.a { color: red; }', filename='x.less', source_map=True)
        m = json.loads(result.map_json)
        self.assertNotIn('sourcesContent', m)


class TestBasepathRootpath(unittest.TestCase):
    def test_basepath_strips_prefix(self) -> None:
        # Source filename starts with the basepath → stripped.
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='/abs/proj/styles/x.less',
            source_map={'sourceMapBasepath': '/abs/proj'},
        )
        m = json.loads(result.map_json)
        self.assertEqual(m['sources'], ['styles/x.less'])

    def test_rootpath_prepends(self) -> None:
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='x.less',
            source_map={'sourceMapRootpath': 'https://example.com/less/'},
        )
        m = json.loads(result.map_json)
        self.assertEqual(m['sources'], ['https://example.com/less/x.less'])

    def test_basepath_then_rootpath(self) -> None:
        # Strip basepath, then prepend rootpath.
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='/abs/proj/styles/x.less',
            source_map={
                'sourceMapBasepath': '/abs/proj',
                'sourceMapRootpath': 'testweb/',
            },
        )
        m = json.loads(result.map_json)
        self.assertEqual(m['sources'], ['testweb/styles/x.less'])

    def test_input_filename_overrides_first(self) -> None:
        result = compile_with_source_map(
            '.a { c: 1px; }',
            filename='x.less',
            source_map={'sourceMapInputFilename': 'override.less'},
        )
        m = json.loads(result.map_json)
        self.assertEqual(m['sources'][0], 'override.less')


class TestOffPathRegression(unittest.TestCase):
    """Calling `compile()` without `source_map` must be byte-identical
    to the pre-feature behavior on every sample we care about."""

    SAMPLES: list[str] = [
        '.a { color: red; }',
        '@c: red; .a { color: @c; padding: 10px; }',
        '.a { .b { color: red; } }',
        '@media (min-width: 600px) { .a { color: red; } }',
        '.a, .b { color: red; padding: 10px !important; }',
    ]

    EXPECTED: list[str] = [
        '.a {\n  color: red;\n}\n',
        '.a {\n  color: red;\n  padding: 10px;\n}\n',
        '.a .b {\n  color: red;\n}\n',
        '@media (min-width: 600px) {\n  .a {\n    color: red;\n  }\n}\n',
        '.a,\n.b {\n  color: red;\n  padding: 10px !important;\n}\n',
    ]

    def test_off_path_unchanged(self) -> None:
        for src, expected in zip(self.SAMPLES, self.EXPECTED, strict=True):
            self.assertEqual(compile(src), expected, msg=f'source: {src!r}')

    def test_explicit_none_same_as_default(self) -> None:
        for src in self.SAMPLES:
            self.assertEqual(compile(src), compile(src, source_map=None))

    def test_explicit_false_same_as_default(self) -> None:
        # `source_map=False` should also be off; we coerce by checking
        # `if source_map:` in _compile_with_limits so 0/False/None all skip.
        for src in self.SAMPLES:
            self.assertEqual(compile(src), compile(src, source_map=False))


class TestCompileShortcut(unittest.TestCase):
    """`lessish.compile(src, source_map=True)` returns a plain string
    with the annotation appended — useful for callers who don't need
    the .map JSON (build pipelines that only want the URL pointer)."""

    def test_returns_string(self) -> None:
        out = compile('.a { c: 1px; }', filename='x.less', source_map=True)
        self.assertIsInstance(out, str)
        self.assertTrue(out.endswith('/*# sourceMappingURL=x.css.map */'))


class TestSourceMapBuilderInternals(unittest.TestCase):
    """Builder details the integration walks don't reach."""

    def test_add_name_dedups(self) -> None:
        b = SourceMapBuilder(file='x.css')
        self.assertEqual(b.add_name('foo'), 0)
        self.assertEqual(b.add_name('bar'), 1)
        # Same name returns the existing index.
        self.assertEqual(b.add_name('foo'), 0)
        self.assertEqual(b._names, ['foo', 'bar'])

    def test_same_column_segment_is_last_write_wins(self) -> None:
        b = SourceMapBuilder(file='x.css')
        b.add_source('x.less', None)
        b.add_segment(out_line=0, out_col=0, src_idx=0, src_line=0, src_col=0)
        # Same gen_col → overwrite, not append.
        b.add_segment(out_line=0, out_col=0, src_idx=0, src_line=5, src_col=2)
        self.assertEqual(len(b._lines[0]), 1)
        self.assertEqual(b._lines[0][0].src_line, 5)
        self.assertEqual(b._lines[0][0].src_col, 2)

    def test_segment_with_name_idx_emits_fifth_field(self) -> None:
        b = SourceMapBuilder(file='x.css')
        b.add_source('x.less', None)
        b.add_name('color')
        b.add_segment(out_line=0, out_col=0, src_idx=0, src_line=0, src_col=0, name_idx=0)
        # name delta = 0 → 'A' appended as the fifth VLQ field.
        self.assertEqual(b.build_mappings(), 'AAAAA')

    def test_source_root_serialized_when_set(self) -> None:
        b = SourceMapBuilder(file='x.css', source_root='https://cdn.example/styles/')
        d = b.build(include_sources_content=False)
        self.assertEqual(d['sourceRoot'], 'https://cdn.example/styles/')


class TestEmitterStructuralCoverage(unittest.TestCase):
    """One test per uncovered structural branch in `CssEmitter`."""

    def _csm(self, src: str, **opts: object) -> SourceMapResult:
        return compile_with_source_map(src, filename='x.less', **opts)

    def test_top_level_atrule_recorded(self) -> None:
        # Top-level at-rule with body — `emit_root` dispatches to
        # `emit_atrule(r)` (no `outer`).
        result = self._csm('@media (a) { .x { c: 1px; } }')
        self.assertIn('@media (a) {', result.css)
        m = json.loads(result.map_json)
        self.assertTrue(m['mappings'])

    def test_top_level_loud_comment_recorded(self) -> None:
        result = self._csm('/* keep */\n.a { c: 1px; }')
        self.assertTrue(result.css.startswith('/* keep */\n.a {'))

    def test_top_level_silent_comment_omitted(self) -> None:
        # `// silent` → no output, no segment.
        result = self._csm('// silent\n.a { c: 1px; }')
        self.assertNotIn('silent', result.css)

    def test_inblock_loud_comment_recorded(self) -> None:
        result = self._csm('.a { /* hi */ c: 1px; }')
        self.assertIn('/* hi */', result.css)

    def test_inblock_inline_atrule_with_prelude(self) -> None:
        # `@apply` is in `_INLINE_ATRULES` and has a prelude — covers
        # the `entry.prelude` branch of the AtRule arm.
        result = self._csm('.a { @apply utility-foo; c: 1px; }')
        self.assertIn('@apply utility-foo;', result.css)

    def test_inblock_inline_atrule_no_prelude(self) -> None:
        # @charset has no prelude when bare — exercises the
        # `if entry.prelude:` False path.
        result = self._csm('.a { @charset; c: 1px; }')
        self.assertIn('@charset', result.css)

    def test_nested_block_atrule_starting_style(self) -> None:
        # @starting-style is in `_NESTED_BLOCK_ATRULES` — stays inline
        # AND has a body, so it recurses through `emit_atrule(entry, inner)`.
        result = self._csm('.a { @starting-style { c: 1px; } d: e; }')
        self.assertIn('@starting-style {', result.css)
        self.assertIn('d: e;', result.css)

    def test_bubbled_atrule_after_ruleset_block(self) -> None:
        # `@media` inside `.a { ... }` bubbles out — emitted by the
        # post-block recursion (`elif AtRule and not _is_inline_atrule`).
        result = self._csm('.a { @media (b) { c: 1px; } d: e; }')
        self.assertIn('.a {\n  d: e;\n}', result.css)
        self.assertIn('@media (b) {\n  .a {\n    c: 1px;', result.css)

    def test_atrule_statement_form(self) -> None:
        result = self._csm('@charset "UTF-8";\n.a { c: 1px; }')
        self.assertTrue(result.css.startswith('@charset "UTF-8";'))

    def test_atrule_statement_with_trailing_loud_comment(self) -> None:
        result = self._csm('@charset "x";\n/* trailing */\n.a { c: 1px; }')
        # The /* trailing */ comment is attached to the @charset and
        # emitted right after it.
        self.assertIn('@charset "x";', result.css)
        self.assertIn('/* trailing */', result.css)

    def test_atrule_statement_silent_trailing_dropped(self) -> None:
        # Silent trailing comment is filtered.
        result = self._csm('@charset "x";\n// silent\n.a { c: 1px; }')
        self.assertNotIn('silent', result.css)

    def test_atrule_body_decls_only_with_important(self) -> None:
        result = self._csm('@font-face { font-family: x; src: url(a.woff) !important; }')
        self.assertIn('src: url(a.woff) !important;', result.css)

    def test_atrule_body_with_nested_ruleset_and_loud_comment(self) -> None:
        result = self._csm('@media (a) { .x { c: 1px; } /* loud */ }')
        self.assertIn('@media (a) {', result.css)
        self.assertIn('  .x {', result.css)
        self.assertIn('/* loud */', result.css)

    def test_atrule_body_silent_comment_dropped(self) -> None:
        # `// ...` runs to end-of-line, so use a real newline before the
        # closing brace.
        result = self._csm('@media (a) { .x { c: 1px; }\n// dropped\n}')
        self.assertNotIn('dropped', result.css)

    def test_media_prelude_normalized(self) -> None:
        # @media + non-empty prelude → `_normalize_media_prelude` runs.
        result = self._csm('@media (min-width: 100px) { .a { c: 1px; } }')
        self.assertIn('@media (min-width: 100px)', result.css)

    def test_supports_prelude_not_renormalized(self) -> None:
        # @supports is in the media-like set but explicitly skipped by
        # the normalizer.
        result = self._csm('@supports (display: grid) { .a { c: 1px; } }')
        self.assertIn('@supports (display: grid)', result.css)

    def test_keyframes_empty_body_still_emits(self) -> None:
        # Empty body bypasses the "no rules" drop because the name
        # ends with `keyframes`.
        result = self._csm('@keyframes spin { }')
        self.assertIn('@keyframes spin {', result.css)

    def test_supports_empty_body_dropped(self) -> None:
        # No emittable content AND name doesn't end in `keyframes` →
        # the at-rule disappears entirely.
        result = self._csm('@supports (a: b) { // only silent\n}')
        self.assertNotIn('@supports', result.css)

    def test_import_inline_at_root(self) -> None:
        # `@import (inline)` produces an `@__inline__` node whose body
        # text is dumped verbatim by `emit_atrule`.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            raw_path = os.path.join(d, 'raw.css')
            with open(raw_path, 'w') as f:
                f.write('.imported { c: red; }')
            src = '@import (inline) "raw.css";\n.a { c: 1px; }'
            result = compile_with_source_map(src, filename=os.path.join(d, 'x.less'))
        self.assertIn('.imported { c: red; }', result.css)
        self.assertIn('.a {', result.css)

    def test_import_inline_inside_ruleset(self) -> None:
        # Nested `@import (inline)` — `@__inline__` indent branch.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'raw.css'), 'w') as f:
                f.write('color: red;\npadding: 10px;')
            src = '.a { @import (inline) "raw.css"; }'
            result = compile_with_source_map(src, filename=os.path.join(d, 'x.less'))
        self.assertIn('color: red;', result.css)

    def test_import_inline_inside_ruleset_no_trailing_newline(self) -> None:
        # Imported text without a final newline triggers the
        # `not at.prelude.endswith('\\n')` branch.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'raw.css'), 'w') as f:
                f.write('color: red;')  # no trailing newline
            src = '.a { @import (inline) "raw.css"; }'
            result = compile_with_source_map(src, filename=os.path.join(d, 'x.less'))
        self.assertIn('color: red;', result.css)

    def test_compress_top_level_loud_comment_dropped(self) -> None:
        result = Lessish(compress=True).compile_with_source_map('/* drop */ .a { c: 1px; }', filename='x.less')
        self.assertNotIn('drop', result.css)
        self.assertIn('.a{c:1px}', result.css)

    def test_compress_top_level_bang_comment_kept(self) -> None:
        result = Lessish(compress=True).compile_with_source_map('/*! keep */ .a { c: 1px; }', filename='x.less')
        self.assertIn('/*! keep */', result.css)

    def test_compress_inblock_loud_comment_dropped(self) -> None:
        result = Lessish(compress=True).compile_with_source_map('.a { /* hi */ c: 1px; }', filename='x.less')
        self.assertNotIn('hi', result.css)

    def test_compress_inblock_bang_comment_kept(self) -> None:
        result = Lessish(compress=True).compile_with_source_map('.a { /*! keep */ c: 1px; }', filename='x.less')
        self.assertIn('/*! keep */', result.css)

    def test_compress_atrule_body_loud_comment_dropped(self) -> None:
        result = Lessish(compress=True).compile_with_source_map(
            '@media (a) { .x { c: 1px; } /* drop */ }', filename='x.less'
        )
        self.assertNotIn('drop', result.css)

    def test_compress_atrule_body_bang_comment_kept(self) -> None:
        result = Lessish(compress=True).compile_with_source_map(
            '@media (a) { .x { c: 1px; } /*! keep */ }', filename='x.less'
        )
        self.assertIn('/*! keep */', result.css)

    def test_compress_atrule_trailing_loud_comment_dropped(self) -> None:
        # Trailing-comments branch on body=None at-rules.
        result = Lessish(compress=True).compile_with_source_map('@charset "x";\n/* drop */', filename='x.less')
        self.assertNotIn('drop', result.css)

    def test_compress_atrule_trailing_bang_comment_kept(self) -> None:
        result = Lessish(compress=True).compile_with_source_map('@charset "x";\n/*! keep */', filename='x.less')
        self.assertIn('/*! keep */', result.css)

    def test_compress_atrule_prelude_compressed(self) -> None:
        # `_compress_atrule_prelude` only runs in compress mode when
        # there's actually a prelude.
        result = Lessish(compress=True).compile_with_source_map(
            '@media   (min-width:  100px)   { .a { c: 1px; } }', filename='x.less'
        )
        self.assertIn('@media', result.css)
        self.assertIn('.a{c:1px}', result.css)

    def test_inblock_declaration_with_important(self) -> None:
        # `entry.important` True inside in_block_entries → exercises
        # the `_important_suffix` call in the Declaration arm.
        result = self._csm('.a { c: 1px !important; }')
        self.assertIn('c: 1px !important;', result.css)

    def test_compress_atrule_body_last_decl_no_semicolon(self) -> None:
        # In compress mode the final decl in an at-rule body skips the
        # trailing `;` — exercises the `pass` arm of `is_last_decl`.
        result = Lessish(compress=True).compile_with_source_map(
            '@font-face { font-family: x; src: url(a.woff); }', filename='x.less'
        )
        # No semicolon after `url(a.woff)` before the closing brace.
        self.assertIn('src:url(a.woff)}', result.css)

    def test_atrule_body_recurses_into_nested_atrule(self) -> None:
        # Body of `@media` contains another at-rule (`@page`) → the
        # body-walk recurses through `emit_atrule(r, inner)`.
        result = self._csm('@media (a) { @page { c: 1px; } }')
        self.assertIn('@media (a) {', result.css)
        self.assertIn('  @page {', result.css)

    def test_atrule_statement_with_inprelude_loud_comment(self) -> None:
        # Block comments inside a body=None at-rule's prelude are
        # extracted into `trailing_comments`; the emitter writes them
        # on their own lines after the directive.
        result = self._csm('@charset "x" /* trailing */;')
        self.assertIn('@charset "x";', result.css)
        self.assertIn('/* trailing */', result.css)

    def test_compress_atrule_statement_trailing_bang_kept_loud_dropped(self) -> None:
        # In compress mode the trailing-comments loop keeps `/*!…*/`
        # and silently drops plain block comments.
        result = Lessish(compress=True).compile_with_source_map(
            '@charset "x" /*! bang */ /* drop */;', filename='x.less'
        )
        self.assertIn('/*! bang */', result.css)
        self.assertNotIn('drop', result.css)

    def test_atrule_statement_inprelude_silent_comment_dropped(self) -> None:
        # `//` silent comments survive prelude extraction but are
        # filtered by the trailing-comments loop (`if c.silent: continue`).
        result = self._csm('@charset "x" /* keep */ /* keep2 */;')
        self.assertIn('/* keep */', result.css)
        self.assertIn('/* keep2 */', result.css)

    def test_inline_import_at_root_with_trailing_newline(self) -> None:
        # When the imported file ends with `\n`, `at.prelude` is
        # newline-terminated → the second `elif at.prelude:` branch
        # fires (one extra newline written, no double-up).
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'raw.css'), 'w') as f:
                f.write('color: red;\n')
            result = compile_with_source_map('@import (inline) "raw.css";', filename=os.path.join(d, 'x.less'))
        self.assertIn('color: red;', result.css)


class TestEmitterReferenceAndExtend(unittest.TestCase):
    def test_import_reference_with_extend_clones_into_selector(self) -> None:
        # `@import (reference)` + `:extend(.thing all)` populates
        # `_extend_added_paths` on the reference ruleset — emitter
        # then re-enters with `reference=False` and emits.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'ref.less'), 'w') as f:
                f.write('.thing { color: red; padding: 10px; }')
            src = '@import (reference) "ref.less";\n.extender:extend(.thing all) {}'
            result = compile_with_source_map(src, filename=os.path.join(d, 'x.less'))
        self.assertIn('.extender {', result.css)
        self.assertIn('color: red;', result.css)
        # Map carries segments for the extender's selector.
        m = json.loads(result.map_json)
        self.assertTrue(m['mappings'])

    def test_record_node_without_index_is_noop(self) -> None:
        # `record` short-circuits when `node.index is None` — there's
        # no segment to attribute, just nothing to do.
        from lessish.ast_nodes import Comment
        from lessish.source import Source

        src = Source(text='', filename='x.less')
        builder = SourceMapBuilder(file='x.css')
        emitter = _map_emitter(builder, src)
        c = Comment(index=None, text='/* unused */', silent=False)  # type: ignore[arg-type]
        emitter.record(c)
        # No segment recorded → mappings stays empty.
        self.assertEqual(builder.build_mappings(), '')

    def test_emit_atrule_with_reference_flag_is_skipped(self) -> None:
        # `_reference=True` is set by the importer on at-rules pulled in
        # via `@import (reference)`. The emitter is expected to skip
        # them entirely.
        from lessish.ast_nodes import AtRule
        from lessish.source import Source

        src = Source(text='', filename='x.less')
        builder = SourceMapBuilder(file='x.css')
        emitter = _map_emitter(builder, src)
        at = AtRule(index=0, name='@media', prelude='(a)', body=None, trailing_comments=[])
        at._reference = True
        emitter.emit_atrule(at)
        # Nothing written.
        self.assertEqual(emitter.render(), '')

    def test_emit_reference_ruleset_without_extend_returns(self) -> None:
        # The eval pipeline normally prunes empty `(reference)` rulesets
        # before they reach the emitter, but the guard at the top of
        # `emit_ruleset` exists for defense in depth.
        from lessish.ast_nodes import Element, Ruleset, Selector
        from lessish.source import Source

        src = Source(text='', filename='x.less')
        builder = SourceMapBuilder(file='x.css')
        emitter = _map_emitter(builder, src)
        rs = Ruleset(
            index=0,
            selectors=[Selector(index=0, elements=[Element(index=0, combinator='', value='.x')])],
            rules=[],
            root=False,
            reference=True,
        )
        rs.paths = []
        emitter.emit_ruleset(rs)
        self.assertEqual(emitter.render(), '')

    def test_emit_inline_atrule_with_outer_indent(self) -> None:
        # `@__inline__` always bubbles to the root via the importer, so
        # the `if outer:` branch is unreachable from compile() — but
        # it's there for any caller that constructs the AST by hand.
        from lessish.ast_nodes import AtRule
        from lessish.source import Source

        src = Source(text='', filename='x.less')
        builder = SourceMapBuilder(file='x.css')
        emitter = _map_emitter(builder, src)
        # Multi-line prelude with a blank line — blank lines skip the
        # indent prefix (`if line.strip():` is False).
        at = AtRule(
            index=0,
            name='@__inline__',
            prelude='line1\n\nline2',
            body=None,
            trailing_comments=[],
        )
        emitter.emit_atrule(at, outer='  ')
        out = emitter.render()
        self.assertIn('  line1\n', out)
        self.assertIn('  line2', out)
        # Trailing newline written because the prelude didn't end with one.

    def test_emit_atrule_trailing_silent_comment_filtered(self) -> None:
        # The parser only ever puts loud `/* */` comments into
        # `trailing_comments`; a silent `//` one would never survive
        # prelude extraction. The `if c.silent: continue` guard exists
        # for callers that synthesize the tree directly.
        from lessish.ast_nodes import AtRule, Comment
        from lessish.source import Source

        src = Source(text='', filename='x.less')
        builder = SourceMapBuilder(file='x.css')
        emitter = _map_emitter(builder, src)
        at = AtRule(
            index=0,
            name='@charset',
            prelude='"x"',
            body=None,
            trailing_comments=[Comment(index=0, text='// drop', silent=True)],
        )
        emitter.emit_atrule(at)
        out = emitter.render()
        self.assertIn('@charset "x";', out)
        self.assertNotIn('drop', out)

    def test_emit_atrule_empty_body_non_keyframes_returns(self) -> None:
        # has_rules=False AND name != keyframes → emit_atrule bails
        # before writing anything. Pre-emit passes normally strip empty
        # bodies, but the guard is a defensive last line.
        from lessish.ast_nodes import AtRule
        from lessish.source import Source

        src = Source(text='', filename='x.less')
        builder = SourceMapBuilder(file='x.css')
        emitter = _map_emitter(builder, src)
        at = AtRule(index=0, name='@supports', prelude='(a)', body=[], trailing_comments=[])
        emitter.emit_atrule(at)
        self.assertEqual(emitter.render(), '')


class TestEmitWithMapHelpers(unittest.TestCase):
    def test_resolve_opts_rejects_non_true_non_dict(self) -> None:
        # The public `compile_with_source_map` always normalizes to
        # `source_map=True`, but `_resolve_opts(False)` is a real path
        # because `emit_with_map` is also a public entry.
        with self.assertRaises(TypeError):
            _resolve_opts(False)
        with self.assertRaises(TypeError):
            _resolve_opts(42)  # type: ignore[arg-type]

    def test_apply_basepath_strips_full_path_to_empty(self) -> None:
        # When the source path equals the basepath verbatim, the result
        # is the empty string (later combined with rootpath if any).
        self.assertEqual(_apply_basepath_rootpath('/abs/proj', '/abs/proj', ''), '')
        # And with a rootpath the rootpath's trailing slash is trimmed.
        self.assertEqual(
            _apply_basepath_rootpath('/abs/proj', '/abs/proj', 'https://cdn/'),
            'https://cdn',
        )

    def test_output_source_files_basename_fallback(self) -> None:
        # `sourceMapBasepath` renames `sources[]` entries; the
        # `sourcesContent` lookup falls back to basename matching.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'mod.less'), 'w') as f:
                f.write('.imp { c: red; }')
            src = '@import "mod.less";\n.a { c: 1px; }'
            result = compile_with_source_map(
                src,
                filename=os.path.join(d, 'x.less'),
                source_map={
                    'outputSourceFiles': True,
                    'sourceMapBasepath': d,
                },
            )
        m = json.loads(result.map_json)
        self.assertEqual(sorted(m['sources']), ['mod.less', 'x.less'])
        # Both entries got their content via basename lookup.
        self.assertEqual(len(m['sourcesContent']), 2)
        self.assertTrue(all(s is not None for s in m['sourcesContent']))

    def test_collect_source_texts_walks_atrule_body(self) -> None:
        # An imported file that contains an at-rule-with-body → the
        # walker recurses through `at.body` to discover further tagged
        # sources.
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'mod.less'), 'w') as f:
                f.write('@media (a) { .x { c: 1px; } }')
            src = '@import "mod.less";\n.a { c: 1px; }'
            result = compile_with_source_map(
                src,
                filename=os.path.join(d, 'x.less'),
                source_map={'outputSourceFiles': True},
            )
        m = json.loads(result.map_json)
        # Both files end up in sourcesContent with non-None bodies.
        self.assertEqual(len(m['sourcesContent']), 2)
        self.assertTrue(all(s is not None for s in m['sourcesContent']))


if __name__ == '__main__':
    unittest.main()
