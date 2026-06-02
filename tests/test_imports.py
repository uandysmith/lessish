from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from lessish import Lessish, LessishSecurityWarning

compile = Lessish().compile

# `file_io` defaults to the secure `'jail'`, which rejects absolute
# `@import` paths and `..`-escapes. Tests asserting the less.js-style
# pass-through / absolute-path behaviour need explicit `'allow'`;
# this helper runs under it and silences the expected
# `LessishSecurityWarning`.
_allow = Lessish(file_io='allow')


def compile_allow(*args: object, **kwargs: object) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=LessishSecurityWarning)
        return _allow.compile(*args, **kwargs)  # type: ignore[arg-type]


class ImportFixture:
    """Context manager that writes a tree of files to a tempdir and
    yields the path of the entry-point file. Each tuple is
    `(relative_path, contents)`.
    """

    def __init__(self, files: dict[str, str], entry: str) -> None:
        self.files = files
        self.entry = entry
        self._tmp: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for rel, content in self.files.items():
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding='utf-8')
        return root / self.entry

    def __exit__(self, *exc: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()


class TestBasicImport(unittest.TestCase):
    def test_imports_relative(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "vars.less"; .x { color: @c; }',
                'vars.less': '@c: red;',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertEqual(css, '.x {\n  color: red;\n}\n')

    def test_imports_extensionless(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "vars"; .x { color: @c; }',
                'vars.less': '@c: blue;',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertEqual(css, '.x {\n  color: blue;\n}\n')

    def test_imports_from_subdirectory(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/helpers.less"; .x { width: @w; }',
                'sub/helpers.less': '@w: 10px;',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertEqual(css, '.x {\n  width: 10px;\n}\n')


class TestImportOptions(unittest.TestCase):
    def test_once_deduplicates(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "a"; @import "a"; .x { color: @c; }',
                'a.less': '@c: red;',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        # `@c: red` only contributes once even though imported twice.
        self.assertEqual(css, '.x {\n  color: red;\n}\n')

    def test_multiple_includes_twice(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import (multiple) "a"; @import (multiple) "a";',
                'a.less': '.row { x: 1; }',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        # `(multiple)` opts out of de-duplication.
        self.assertEqual(css, '.row {\n  x: 1;\n}\n.row {\n  x: 1;\n}\n')

    def test_optional_swallows_missing(self) -> None:
        with ImportFixture(
            {'main.less': '@import (optional) "does-not-exist"; .x { color: red; }'},
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertEqual(css, '.x {\n  color: red;\n}\n')

    def test_missing_raises(self) -> None:
        from lessish.errors import FileError

        with ImportFixture(
            {'main.less': '@import "does-not-exist"; .x { color: red; }'},
            'main.less',
        ) as entry:
            with self.assertRaises(FileError):
                compile(entry.read_text(), filename=str(entry))


class TestCssPassthrough(unittest.TestCase):
    def test_css_file_keeps_import(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "x.css"; .x { color: red; }',
                'x.css': 'irrelevant',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        # `.css` extension keeps the `@import` literal in output.
        self.assertIn('@import "x.css";', css)

    def test_force_less_on_css_extension(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import (less) "lib.css"; .x { color: @c; }',
                'lib.css': '@c: red;',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertEqual(css, '.x {\n  color: red;\n}\n')


class TestMediaSuffix(unittest.TestCase):
    def test_media_wraps_imported_rules(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "x" screen and (max-width: 600px);',
                'x.less': '.a { color: red; }',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertIn('@media screen and (max-width: 600px) {', css)
        self.assertIn('.a {', css)


class TestProcessImportsToggle(unittest.TestCase):
    def test_process_imports_false_drops_less_import(self) -> None:
        # Mirrors less.js's `processImports: false` behavior: a
        # less-shaped @import (`.less` / no extension / `(less)` /
        # `(reference)` / `(inline)`) gets dropped silently, because
        # the option told the importer to skip the fetch+inline step,
        # which would have produced its useful output. The directive
        # itself isn't valid CSS the browser can resolve.
        self.assertEqual(
            compile('@import "x.less";', process_imports=False),
            '',
        )

    def test_process_imports_false_keeps_css_import(self) -> None:
        # CSS-shaped imports (`.css` extension OR explicit `(css)`
        # qualifier OR remote URL without `.less`) pass through as
        # valid CSS the browser handles at render time.
        self.assertEqual(
            compile('@import "x.css";', process_imports=False),
            '@import "x.css";\n',
        )
        self.assertEqual(
            compile('@import (css) "x.less";', process_imports=False),
            '@import "x.less";\n',
        )
        self.assertEqual(
            compile(
                '@import url("https://cdn.example.com/style.css");',
                process_imports=False,
            ),
            '@import url("https://cdn.example.com/style.css");\n',
        )


class TestParseImportPrelude(unittest.TestCase):
    """Direct tests for `parse_import_prelude` + `_strip_quotes`."""

    def test_url_with_nested_parens(self) -> None:
        from lessish.importer import parse_import_prelude

        spec = parse_import_prelude('url(foo(bar)baz)')
        # The outer `)` after the matched inner pair closes the url().
        self.assertEqual(spec.path, 'foo(bar)baz')
        self.assertTrue(spec.is_url_form)
        self.assertEqual(spec.path_quote, '')

    def test_url_with_quoted_inner(self) -> None:
        from lessish.importer import parse_import_prelude

        spec = parse_import_prelude("url('x.css')")
        self.assertEqual(spec.path, 'x.css')
        self.assertEqual(spec.path_quote, "'")
        self.assertTrue(spec.is_url_form)

    def test_unterminated_string_path(self) -> None:
        from lessish.importer import parse_import_prelude

        spec = parse_import_prelude('"unterminated.less')
        # Closing quote missing → take everything after the open quote.
        self.assertEqual(spec.path, 'unterminated.less')

    def test_bare_path_with_media(self) -> None:
        from lessish.importer import parse_import_prelude

        # No quotes, no url() — path runs until whitespace, media is rest.
        spec = parse_import_prelude('bare.less screen and (color)')
        self.assertEqual(spec.path, 'bare.less')
        self.assertEqual(spec.media, 'screen and (color)')

    def test_strip_quotes_helper(self) -> None:
        from lessish.importer import _strip_quotes

        self.assertEqual(_strip_quotes('"foo"'), 'foo')
        self.assertEqual(_strip_quotes("'bar'"), 'bar')
        # Unmatched / missing quotes leave the string untouched.
        self.assertEqual(_strip_quotes('plain'), 'plain')
        self.assertEqual(_strip_quotes('\'mismatch"'), '\'mismatch"')
        self.assertEqual(_strip_quotes(''), '')


class TestRootpathOption(unittest.TestCase):
    def test_rootpath_prefixes_outer_url(self) -> None:
        out = compile(
            '.a { background: url("img.png"); }',
            rootpath='https://cdn.example/',
        )
        self.assertIn('url("https://cdn.example/img.png")', out)

    def test_rootpath_passes_through_absolute_url(self) -> None:
        out = compile(
            '.a { background: url("https://other/x.png"); }',
            rootpath='https://cdn.example/',
        )
        self.assertIn('url("https://other/x.png")', out)

    def test_rootpath_skips_data_uri(self) -> None:
        out = compile(
            '.a { background: url("data:image/png,abc"); }',
            rootpath='https://cdn.example/',
        )
        self.assertIn('url("data:image/png,abc")', out)

    def test_rootpath_skips_fragment_only(self) -> None:
        out = compile(
            '.a { fill: url("#grad"); }',
            rootpath='https://cdn.example/',
        )
        self.assertIn('url("#grad")', out)

    def test_rootpath_prefixes_css_import(self) -> None:
        # CSS-pass-through @import directives also pick up the rootpath.
        out = compile('@import "lib.css";', rootpath='https://cdn/')
        self.assertIn('@import "https://cdn/lib.css";', out)

    def test_rootpath_keeps_absolute_import_unchanged(self) -> None:
        # Absolute filesystem paths skip rootpath rewriting.
        out = compile_allow('@import "/abs/lib.css";', rootpath='https://cdn/')
        self.assertIn('@import "/abs/lib.css";', out)

    def test_rootpath_normalises_dot_segment(self) -> None:
        # `./` collapses inside `_normalize_path` so a path like
        # `./` joined onto a rootpath ends up as just the rootpath.
        out = compile(
            '.a { background: url("./"); }',
            rootpath='https://cdn.example/',
        )
        self.assertIn('url("https://cdn.example/")', out)

    def test_rootpath_escapes_unquoted(self) -> None:
        # Rootpath with parens needs backslash-escaping inside an
        # *unquoted* url(...) value, or the `)` would close the token.
        out = compile(
            '.a { background: url(img.png); }',
            rootpath='dir (1)/',
        )
        # `(` and `)` and ` ` get backslash-escaped.
        self.assertIn('\\', out)

    def test_rootpath_url_with_existing_query_preserved(self) -> None:
        out = compile(
            '.a { background: url("img.png?v=1"); }',
            rootpath='https://cdn.example/',
        )
        self.assertIn('https://cdn.example/img.png?v=1', out)


class TestRewriteUrlsOption(unittest.TestCase):
    def test_rewrite_urls_all_rewrites_imported(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url("./img.png"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('url("./sub/img.png")', css)

    def test_rewrite_urls_local_skips_module_paths(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url("module/path"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='local',
            )
        # `module/path` doesn't start with `.` → kept as-is, only normalised.
        self.assertIn('url("module/path")', css)

    def test_rewrite_urls_local_normalises_module_paths(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url("module/path/../relative/path"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='local',
            )
        self.assertIn('url("module/relative/path")', css)

    def test_rewrite_urls_all_on_outer_file(self) -> None:
        # When rewrite_urls != 'off', the outer file's url()s also go
        # through the normaliser (with sub_dir == base_dir).
        with ImportFixture(
            {'main.less': '.a { background: url("./img/./x.png"); }'},
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('url("./img/x.png")', css)

    def test_rewrite_urls_absolute_url_passes_through(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url("https://cdn/x.png"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('url("https://cdn/x.png")', css)

    def test_rewrite_urls_data_uri_passes_through(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url("data:image/png,abc"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('url("data:image/png,abc")', css)

    def test_rewrite_urls_fragment_passes_through(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { fill: url("#grad"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('url("#grad")', css)

    def test_rewrite_urls_inside_atrule_body(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '@media (a) { .x { background: url("./img.png"); } }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('@media (a)', css)
        self.assertIn('url("./sub/img.png")', css)

    def test_rewrite_urls_passes_through_statement_atrule(self) -> None:
        # `_rewrite_urls_in_rules` falls through to `out.append(r)`
        # for nodes that aren't Declaration / Ruleset / AtRule-with-body
        # — e.g. statement-form `@charset`.
        out = compile(
            '@charset "UTF-8";\n.a { background: url("./img.png"); }',
            rewrite_urls='all',
        )
        self.assertIn('@charset "UTF-8";', out)
        self.assertIn('url("./img.png")', out)

    def test_rewrite_urls_empty_url_passthrough(self) -> None:
        # `_rewrite_url` short-circuits on empty input.
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url(""); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
            )
        self.assertIn('url("")', css)

    def test_rewrite_urls_combined_with_rootpath(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "sub/lib.less";',
                'sub/lib.less': '.a { background: url("./img.png"); }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                rewrite_urls='all',
                rootpath='https://cdn/',
            )
        # rewrite makes path relative to base, then rootpath prepends.
        self.assertIn('https://cdn/', css)
        self.assertIn('sub/img.png', css)


class TestUrlArgsOption(unittest.TestCase):
    def test_basic_append(self) -> None:
        out = compile('.a { background: url("img.png"); }', url_args='v=1')
        self.assertIn('url("img.png?v=1")', out)

    def test_existing_query_uses_ampersand(self) -> None:
        out = compile('.a { background: url("img.png?x=1"); }', url_args='v=2')
        self.assertIn('url("img.png?x=1&v=2")', out)

    def test_preserves_fragment(self) -> None:
        out = compile('.a { background: url("img.png#frag"); }', url_args='v=3')
        self.assertIn('url("img.png?v=3#frag")', out)

    def test_data_uri_exempt(self) -> None:
        out = compile(
            '.a { background: url("data:image/png,abc"); }',
            url_args='v=1',
        )
        self.assertIn('url("data:image/png,abc")', out)

    def test_data_uri_with_leading_whitespace_exempt(self) -> None:
        # `lstrip().startswith('data:')` — leading whitespace tolerated.
        out = compile(
            '.a { background: url("  data:image/png,abc"); }',
            url_args='v=1',
        )
        # Whitespace-prefixed data: also bypasses the suffix.
        self.assertNotIn('?v=1', out)

    def test_empty_url_left_alone(self) -> None:
        # `_append_url_args` no-ops on empty paths.
        out = compile('.a { background: url(); }', url_args='v=1')
        self.assertIn('url()', out)

    def test_applies_inside_nested_ruleset(self) -> None:
        # Walker recurses into nested rulesets.
        out = compile(
            '.outer { .inner { background: url("a.png"); } }',
            url_args='v=1',
        )
        self.assertIn('?v=1', out)

    def test_applies_inside_at_rule_body(self) -> None:
        out = compile(
            '@media (a) { .x { background: url("a.png"); } }',
            url_args='v=1',
        )
        self.assertIn('?v=1', out)

    def test_passthrough_for_non_decl_nodes(self) -> None:
        # `_apply_url_args_in_rules` falls through to `out.append(r)` for
        # statement-form at-rules — they have no url() to rewrite.
        out = compile(
            '@charset "UTF-8";\n.a { background: url("img.png"); }',
            url_args='v=1',
        )
        self.assertIn('@charset "UTF-8";', out)
        self.assertIn('?v=1', out)

    def test_rootpath_applies_inside_atrule_body(self) -> None:
        # `apply_rootpath_to_urls` walks @media bodies too.
        out = compile(
            '@media (a) { .x { background: url("img.png"); } }',
            rootpath='https://cdn/',
        )
        self.assertIn('@media (a)', out)
        self.assertIn('url("https://cdn/img.png")', out)


class TestRemoteImports(unittest.TestCase):
    def test_remote_less_raises_unsupported(self) -> None:
        from lessish.errors import UnsupportedFeatureError

        with self.assertRaises(UnsupportedFeatureError):
            compile('@import "https://example.com/x.less";')

    def test_remote_less_with_optional_silently_drops(self) -> None:
        out = compile('@import (optional) "https://example.com/x.less";\n.a { c: 1; }')
        self.assertIn('.a {', out)
        self.assertNotIn('@import', out)

    def test_remote_css_passes_through(self) -> None:
        out = compile('@import url("https://fonts.googleapis.com/css?family=Roboto");')
        self.assertIn('@import url("https://fonts.googleapis.com/css?family=Roboto")', out)

    def test_remote_less_with_css_qualifier_passes_through(self) -> None:
        out = compile('@import (css) "https://example/x.less";')
        # `(css)` strips the qualifier from the emitted prelude.
        self.assertIn('@import "https://example/x.less";', out)

    def test_remote_with_reference_qualifier_raises(self) -> None:
        # (reference) on a remote .less still needs to fetch.
        from lessish.errors import UnsupportedFeatureError

        with self.assertRaises(UnsupportedFeatureError):
            compile('@import (reference) "https://example.com/x.less";')

    def test_protocol_relative_url(self) -> None:
        # `//host/...` paths are also remote.
        from lessish.errors import UnsupportedFeatureError

        with self.assertRaises(UnsupportedFeatureError):
            compile('@import "//example.com/x.less";')

    def test_data_uri_not_treated_as_remote(self) -> None:
        # `data:` is a special-case in `_is_remote_url` — *not* remote.
        # Also not a remote URL → goes through path resolution (and will
        # fail to resolve as a file). With `(optional)` it's harmless.
        out = compile('@import (optional) "data:text/less,.x{c:1}";')
        self.assertEqual(out.strip(), '')


class TestAbsolutePaths(unittest.TestCase):
    def test_absolute_filesystem_import_passes_through(self) -> None:
        # Absolute-path pass-through is less.js-compatible `allow` behaviour;
        # the secure `jail` default rejects absolute imports.
        out = compile_allow('@import "/usr/local/etc/foo.less";')
        self.assertIn('@import "/usr/local/etc/foo.less";', out)

    def test_absolute_filesystem_url_import(self) -> None:
        out = compile_allow('@import url("/abs/lib.css");')
        self.assertIn('@import url("/abs/lib.css");', out)


class TestCycleDetection(unittest.TestCase):
    def test_two_file_cycle_resolves(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "a";',
                'a.less': '@import "b";\n.x { c: 1; }',
                'b.less': '@import "a";\n.y { c: 2; }',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        # Both rules emit once; the cycle doesn't re-enter.
        self.assertIn('.x {', css)
        self.assertIn('.y {', css)

    def test_self_cycle_with_multiple(self) -> None:
        # `(multiple)` skips dedup but the stack-cycle guard still
        # breaks the recursion.
        with ImportFixture(
            {
                'a.less': '@import (multiple) "a";\n.x { c: 1; }',
            },
            'a.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertIn('.x {', css)


class TestMalformedImports(unittest.TestCase):
    def test_bare_ident_before_path_raises(self) -> None:
        from lessish.errors import ParseError

        with self.assertRaises(ParseError):
            compile('@import garbage "x.less";')

    def test_empty_prelude_passes_through(self) -> None:
        # `parse_import_prelude` returns an empty path; `_handle_import`
        # keeps the directive as-is.
        out = compile('@import "";')
        self.assertIn('@import ""', out)

    def test_options_only_no_path_passes_through(self) -> None:
        out = compile('@import (reference);')
        self.assertIn('@import', out)


class TestImportOptionalEdgeCases(unittest.TestCase):
    def test_inline_optional_missing_silently_drops(self) -> None:
        out = compile('@import (inline, optional) "missing.css";\n.a { c: 1; }')
        self.assertIn('.a {', out)
        self.assertNotIn('@import', out)

    def test_inline_missing_without_optional_raises(self) -> None:
        from lessish.errors import FileError

        with self.assertRaises(FileError):
            compile('@import (inline) "missing.css";\n.a { c: 1; }')


class TestImportInsideContainers(unittest.TestCase):
    def test_import_inside_atrule_body(self) -> None:
        with ImportFixture(
            {
                'main.less': '@media (a) { @import "lib"; }',
                'lib.less': '.x { c: 1; }',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertIn('@media (a)', css)
        self.assertIn('.x', css)

    def test_import_inside_ruleset(self) -> None:
        with ImportFixture(
            {
                'main.less': '.wrap { @import "lib"; }',
                'lib.less': '.x { c: 1; }',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        # Imported rules end up at the wrapper site.
        self.assertIn('.wrap', css)

    def test_import_inside_mixin_definition(self) -> None:
        with ImportFixture(
            {
                'main.less': '.mix() { @import "lib"; }\n.a { .mix(); }',
                'lib.less': '.imported { c: 1; }',
            },
            'main.less',
        ) as entry:
            css = compile(entry.read_text(), filename=str(entry))
        self.assertIn('.imported', css)


class TestBfsPrefetch(unittest.TestCase):
    def test_bfs_swallows_parse_error_in_prefetch(self) -> None:
        # The BFS pre-pass tries to parse imported files ahead of the
        # splice pass; a parse error there is swallowed so the splice
        # path can report it with its full error context. Trigger by
        # importing a file whose first character makes the parser bail.
        from lessish.errors import LessError

        with ImportFixture(
            {
                'main.less': '@import "broken";\n.x { c: 1; }',
                'broken.less': '{ invalid',
            },
            'main.less',
        ) as entry:
            with self.assertRaises(LessError):
                compile(entry.read_text(), filename=str(entry))


class TestSearchPaths(unittest.TestCase):
    def test_paths_option_resolves(self) -> None:
        with ImportFixture(
            {
                'main.less': '@import "lib";',
                'extra/lib.less': '.x { c: 1; }',
            },
            'main.less',
        ) as entry:
            css = compile(
                entry.read_text(),
                filename=str(entry),
                paths=[str(entry.parent / 'extra')],
            )
        self.assertIn('.x', css)

    def test_file_not_found_error_lists_tried_paths(self) -> None:
        from lessish.errors import FileError

        with ImportFixture(
            {'main.less': '@import "missing";'},
            'main.less',
        ) as entry:
            try:
                compile_allow(
                    entry.read_text(),
                    filename=str(entry),
                    paths=[str(entry.parent / 'sometimes')],
                )
                self.fail('expected FileError')
            except FileError as e:
                # Error message lists each tried path (current dir,
                # search dirs, npm://, raw).
                msg = str(e)
                self.assertIn('missing', msg)
                self.assertIn('npm://', msg)

    def test_absolute_import_resolves_against_filesystem(self) -> None:
        # `@import "/abs/foo.less"` passes through as a CSS @import
        # without trying to resolve it on disk — but `_resolve_path` is
        # exercised by the BFS pre-pass which classifies it as a
        # non-participant.
        with ImportFixture({'main.less': '@import "/missing/x.less";'}, 'main.less') as entry:
            out = compile_allow(entry.read_text(), filename=str(entry))
        self.assertIn('@import "/missing/x.less";', out)


class TestRootpathHelperDirect(unittest.TestCase):
    """Direct tests for the rootpath / url helpers that are easier to
    cover one-by-one than through full Less programs."""

    def test_apply_rootpath_to_imports_skips_when_empty(self) -> None:
        from lessish.ast_nodes import AtRule, Node
        from lessish.importer import apply_rootpath_to_imports

        rules: list[Node] = [AtRule(index=0, name='@import', prelude='"x.css"', body=None)]
        # No rootpath → returns same list unchanged.
        self.assertIs(apply_rootpath_to_imports(rules, ''), rules)

    def test_apply_rootpath_to_urls_skips_when_empty(self) -> None:
        from lessish.importer import apply_rootpath_to_urls

        rules: list[object] = []
        self.assertIs(apply_rootpath_to_urls(rules, ''), rules)  # type: ignore[arg-type]

    def test_normalize_path(self) -> None:
        from lessish.importer import _normalize_path

        self.assertEqual(_normalize_path('a/./b/../c'), 'a/c')
        self.assertEqual(_normalize_path('../a'), '../a')
        self.assertEqual(_normalize_path('./'), '')

    def test_path_requires_rewrite(self) -> None:
        from lessish.importer import _path_requires_rewrite

        self.assertTrue(_path_requires_rewrite('rel/path'))
        self.assertFalse(_path_requires_rewrite('/abs'))
        self.assertFalse(_path_requires_rewrite('#frag'))
        self.assertFalse(_path_requires_rewrite('http://host'))
        self.assertFalse(_path_requires_rewrite(''))

    def test_is_path_local_relative(self) -> None:
        from lessish.importer import _is_path_local_relative

        self.assertTrue(_is_path_local_relative('./a'))
        self.assertTrue(_is_path_local_relative('../a'))
        self.assertFalse(_is_path_local_relative('a'))
        self.assertFalse(_is_path_local_relative(''))

    def test_escape_url_path(self) -> None:
        from lessish.importer import _escape_url_path

        self.assertEqual(_escape_url_path('a(b)c'), 'a\\(b\\)c')
        self.assertEqual(_escape_url_path('a b'), 'a\\ b')
        self.assertEqual(_escape_url_path('a"b\'c'), 'a\\"b\\\'c')

    def test_rewrite_path_with_rootpath_preserves_dot_slash(self) -> None:
        from lessish.importer import _rewrite_path_with_rootpath

        # `./folder (1)/x` + `assets/` rootpath would normalise away
        # the leading `.`; the helper re-prefixes `./`.
        result = _rewrite_path_with_rootpath('./folder/x.png', 'assets/')
        self.assertEqual(result, './assets/folder/x.png')

    def test_is_remote_url_data_returns_false(self) -> None:
        from lessish.importer import _is_remote_url

        self.assertFalse(_is_remote_url('data:image/png,abc'))
        self.assertTrue(_is_remote_url('https://x'))
        self.assertTrue(_is_remote_url('//host/x'))
        self.assertFalse(_is_remote_url('relative'))

    def test_atrule_has_inner_rulesets_body_none(self) -> None:
        from lessish.ast_nodes import AtRule
        from lessish.importer import _atrule_has_inner_rulesets

        # body=None short-circuits to False.
        at = AtRule(index=0, name='@charset', prelude='"x"', body=None)
        self.assertFalse(_atrule_has_inner_rulesets(at))

    def test_apply_rootpath_skips_absolute_data_fragment(self) -> None:
        # `_apply_rootpath` is the inner helper used by url-rewriting;
        # it short-circuits on paths that must not get a prefix.
        from lessish.importer import _apply_rootpath

        self.assertEqual(_apply_rootpath('https://x', 'rp/'), 'https://x')
        self.assertEqual(_apply_rootpath('data:abc', 'rp/'), 'data:abc')
        self.assertEqual(_apply_rootpath('#frag', 'rp/'), '#frag')

    def test_apply_rootpath_pure_dot_slash_collapses(self) -> None:
        from lessish.importer import _apply_rootpath

        # `./` against a rootpath collapses to the rootpath sans slash.
        self.assertEqual(_apply_rootpath('./', 'rp/'), 'rp')
        # Leading `./` is stripped before urljoin.
        self.assertEqual(_apply_rootpath('./x', 'rp/'), 'rp/x')

    def test_normalise_url_path_collapses_to_dot_slash(self) -> None:
        from lessish.importer import _normalise_url_path

        # `posixpath.normpath('a/..')` → `'.'`; the helper restores
        # `./` so the path stays a directory-shaped relative URL.
        self.assertEqual(_normalise_url_path('a/..'), './')
        # `./x/..` → `'.'` → `'./'` (we report it as a directory).
        self.assertEqual(_normalise_url_path('./x/..'), './')

    def test_is_path_relative_empty_returns_false(self) -> None:
        from lessish.importer import _is_path_relative

        self.assertFalse(_is_path_relative(''))

    def test_resolve_path_absolute_path_branches(self) -> None:
        # Absolute path that doesn't exist → returns None; absolute
        # path that does exist → returns it as-is. Bypasses the
        # base_dir / paths search.
        import tempfile
        from pathlib import Path

        from lessish.importer import Importer

        importer = Importer(
            source_path='<input>',
            paths=[],
            file_io='allow',  # absolute-path resolution is allow-mode behaviour
        )
        # Non-existent absolute path → None.
        self.assertIsNone(importer._resolve_path('/definitely/not/a/file.less', Path('.')))

        # Existing absolute path → returned.
        with tempfile.NamedTemporaryFile(suffix='.less', delete=False) as f:
            f.write(b'.x { c: 1; }')
            fname = f.name
        try:
            resolved = importer._resolve_path(fname, Path('.'))
            self.assertEqual(str(resolved), fname)
        finally:
            import os

            os.unlink(fname)


if __name__ == '__main__':
    unittest.main()
