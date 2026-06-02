"""Tests for the `file_io` sandbox option.

`jail` (the default) confines reads to `base_dir` + `paths`; absolute
paths and `..`-escapes are rejected. `allow` mirrors less.js — Less
source can read any file the process can (opt-in, never the default).
`deny` blocks every file-touching feature with `SecurityError`.

We cover three sinks: `@import`, `@import (inline)`, and `data-uri()`
(plus the `image-*` family which shares the same plumbing).
"""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from lessish import Lessish, SecurityError
from lessish.errors import FileError, LessishSecurityWarning


class _Tree:
    """Tiny disk fixture: write a {relpath: content} map under a fresh
    tempdir and hand back the root + a compiled-entry helper."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self._tmp: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for rel, content in self.files.items():
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding='utf-8')
        return root

    def __exit__(self, *_exc: object) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()


class FileIoAllowTests(unittest.TestCase):
    """`allow` mode: file reads behave exactly as in less.js."""

    def test_inline_traversal_reads_in_allow_mode(self) -> None:
        # `..`-traversal is permitted under explicit `allow` (matches
        # less.js). Explicit `allow` emits `LessishSecurityWarning`
        # (silenced here; asserted in AllowFileIoWarningTests).
        with (
            _Tree(
                {
                    'secret.txt': '/* leak */\n',
                    'sub/entry.less': '@import (inline) "../secret.txt";\n',
                }
            ) as root,
            warnings.catch_warnings(),
        ):
            warnings.filterwarnings('ignore', category=LessishSecurityWarning)
            css = Lessish(file_io='allow').compile(
                (root / 'sub/entry.less').read_text(),
                filename=str(root / 'sub/entry.less'),
            )
            self.assertIn('/* leak */', css)


class FileIoJailTests(unittest.TestCase):
    """`jail` mode confines reads to `base_dir` + `paths`."""

    def test_inline_traversal_rejected_silently(self) -> None:
        # `_resolve_path`'s jail filter returns None for the resolved
        # candidate, so the import surfaces as a missing-file error
        # rather than a SecurityError — matches less.js's "missing file"
        # diagnostic shape for `..`-escapes.
        with _Tree(
            {
                'secret.txt': '/* leak */\n',
                'sub/entry.less': '@import (inline) "../secret.txt";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail')
            with self.assertRaises(Exception) as cm:
                ls.compile(
                    (root / 'sub/entry.less').read_text(),
                    filename=str(root / 'sub/entry.less'),
                )
            # FileError, not SecurityError — the path was relative.
            self.assertIn("wasn't found", str(cm.exception))

    def test_absolute_inline_rejected_with_security_error(self) -> None:
        with _Tree(
            {
                'secret.txt': '/* leak */\n',
                'entry.less': '@import (inline) "/etc/hostname";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_absolute_less_import_rejected_with_security_error(self) -> None:
        # In `allow` an absolute @import pass-through; in `jail` it's
        # SecurityError so the source can't smuggle one past unnoticed.
        with _Tree(
            {
                'entry.less': '@import "/etc/passwd.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_in_jail_import_still_works(self) -> None:
        # The point of `jail` is to stay usable for legitimate imports.
        with _Tree(
            {
                'colors.less': '.c { color: red; }\n',
                'entry.less': '@import "colors.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail')
            css = ls.compile(
                (root / 'entry.less').read_text(),
                filename=str(root / 'entry.less'),
            )
            self.assertIn('color: red', css)

    def test_paths_search_dir_is_part_of_jail(self) -> None:
        with _Tree(
            {
                'mixins/colors.less': '.c { color: red; }\n',
                'entry.less': '@import "colors.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail', paths=[str(root / 'mixins')])
            css = ls.compile(
                (root / 'entry.less').read_text(),
                filename=str(root / 'entry.less'),
            )
            self.assertIn('color: red', css)

    def test_data_uri_absolute_rejected(self) -> None:
        with _Tree(
            {
                'entry.less': ".a { background: data-uri('text/plain', '/etc/hostname'); }\n",
            }
        ) as root:
            ls = Lessish(file_io='jail')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_data_uri_relative_inside_jail_works(self) -> None:
        with _Tree(
            {
                'logo.svg': '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>\n',
                'entry.less': ".a { background: data-uri('logo.svg'); }\n",
            }
        ) as root:
            ls = Lessish(file_io='jail')
            css = ls.compile(
                (root / 'entry.less').read_text(),
                filename=str(root / 'entry.less'),
            )
            self.assertIn('data:image/svg+xml,', css)

    def test_data_uri_relative_traversal_falls_back_to_url(self) -> None:
        # less.js: when a data-uri target can't be loaded, it degrades
        # to a plain `url(<path>)`. lessish does the same — the jail
        # filter must NOT raise here, only suppress the read. So we get
        # the fallback URL with the path verbatim instead of base64.
        with _Tree(
            {
                'secret.txt': 'leak\n',
                'sub/entry.less': ".a { background: data-uri('text/plain', '../secret.txt'); }\n",
            }
        ) as root:
            ls = Lessish(file_io='jail')
            css = ls.compile(
                (root / 'sub/entry.less').read_text(),
                filename=str(root / 'sub/entry.less'),
            )
            self.assertNotIn('leak', css)
            self.assertIn('url("../secret.txt")', css)


class FileIoDenyTests(unittest.TestCase):
    """`deny` mode refuses every file-touching feature."""

    def test_import_rejected(self) -> None:
        with _Tree(
            {
                'colors.less': '.c { color: red; }\n',
                'entry.less': '@import "colors.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='deny')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_inline_import_rejected(self) -> None:
        with _Tree(
            {
                'piece.less': '.x { color: red; }\n',
                'entry.less': '@import (inline) "piece.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='deny')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_data_uri_rejected(self) -> None:
        with _Tree(
            {
                'logo.svg': '<svg/>\n',
                'entry.less': ".a { background: data-uri('logo.svg'); }\n",
            }
        ) as root:
            ls = Lessish(file_io='deny')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_image_size_rejected(self) -> None:
        with _Tree(
            {
                'logo.svg': '<svg width="10" height="10"/>\n',
                'entry.less': ".a { content: image-size('logo.svg'); }\n",
            }
        ) as root:
            ls = Lessish(file_io='deny')
            with self.assertRaises(SecurityError):
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )

    def test_remote_pass_through_still_allowed(self) -> None:
        # `deny` blocks file I/O — it does not gag pass-through CSS
        # `@import`s (browser-time fetch, no compile-time read).
        css = Lessish(file_io='deny').compile(
            '@import url("https://fonts.googleapis.com/css?family=Roboto");\n',
        )
        self.assertIn('@import url("https://fonts.googleapis.com/', css)

    def test_no_imports_no_data_uri_compiles_fine(self) -> None:
        # `deny` on a Less source that doesn't touch the filesystem is
        # a no-op — it just opts out of the dangerous sinks.
        css = Lessish(file_io='deny').compile('.x { color: red; }')
        self.assertIn('color: red', css)


class FileIoValidationTests(unittest.TestCase):
    def test_unknown_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Lessish(file_io='whatever').compile('.x { color: red; }')


class AllowFileIoWarningTests(unittest.TestCase):
    """`file_io` defaults to the secure `'jail'`, so the default is
    silent. Choosing `'allow'` (the less.js-compatible read-anything
    mode) emits `LessishSecurityWarning` on EVERY compile — there is no
    once-per-process latch — so the risk stays visible at each call
    site. The warning is silenceable via `warnings.filterwarnings`.
    """

    def _compile_capturing(self, ls: Lessish, **kw: object) -> list[warnings.WarningMessage]:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            ls.compile('.x { color: red; }', **kw)
        return [w for w in caught if issubclass(w.category, LessishSecurityWarning)]

    def test_default_jail_does_not_warn(self) -> None:
        # The secure default must be silent.
        self.assertEqual(self._compile_capturing(Lessish()), [])

    def test_explicit_allow_warns(self) -> None:
        caught = self._compile_capturing(Lessish(file_io='allow'))
        self.assertEqual(len(caught), 1)
        self.assertIn('file_io', str(caught[0].message))

    def test_explicit_jail_does_not_warn(self) -> None:
        self.assertEqual(self._compile_capturing(Lessish(file_io='jail')), [])

    def test_explicit_deny_does_not_warn(self) -> None:
        self.assertEqual(self._compile_capturing(Lessish(file_io='deny')), [])

    def test_per_call_allow_override_warns(self) -> None:
        # A per-call `allow` override is still an explicit `allow` → warns.
        caught = self._compile_capturing(Lessish(), file_io='allow')
        self.assertEqual(len(caught), 1)

    def test_per_call_jail_override_silences_constructor_allow(self) -> None:
        # Constructor picked `allow`, but the per-call override lands on
        # `jail`, so the effective policy is safe → no warning.
        caught = self._compile_capturing(Lessish(file_io='allow'), file_io='jail')
        self.assertEqual(caught, [])

    def test_allow_warns_every_compile(self) -> None:
        # No once-per-process latch: three compiles under `allow` emit
        # three warnings.
        ls = Lessish(file_io='allow')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            ls.compile('.x { color: red; }')
            ls.compile('.x { color: red; }')
            ls.compile('.x { color: red; }')
        sec = [w for w in caught if issubclass(w.category, LessishSecurityWarning)]
        self.assertEqual(len(sec), 3)

    def test_warning_silenceable_via_filter(self) -> None:
        # Embedders who accept the risk silence the warning with the
        # standard `warnings.filterwarnings` mechanism (this is exactly
        # what the CLI does internally).
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            warnings.filterwarnings('ignore', category=LessishSecurityWarning)
            Lessish(file_io='allow').compile('.x { color: red; }')
        sec = [w for w in caught if issubclass(w.category, LessishSecurityWarning)]
        self.assertEqual(sec, [])


class FileNotFoundMessageTests(unittest.TestCase):
    """`_make_file_not_found_error` must not leak filesystem layout in
    the stricter `jail` / `deny` modes. `allow` preserves the helpful
    less.js-shaped `Tried - <candidates>` list (no security boundary
    being crossed).
    """

    def test_allow_mode_keeps_tried_candidates(self) -> None:
        # Embedder opted into `allow`; less.js-parity diagnostic is
        # preferable here — easier to debug a typo'd `@import`.
        with _Tree(
            {
                'entry.less': '@import "missing.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='allow')
            with self.assertRaises(FileError) as cm, warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=LessishSecurityWarning)
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )
            msg = str(cm.exception)
            self.assertIn('missing.less', msg)
            self.assertIn('Tried', msg)

    def test_jail_mode_suppresses_tried_candidates(self) -> None:
        # The candidate list contains absolute resolved paths to files
        # the policy refused to read; printing them turns the error into
        # a filesystem-layout oracle. Jail mode must surface just the
        # path-as-written.
        #
        # The standard LessError formatting includes a `in <file>:
        # line:column` band, which echoes the entry file the embedder
        # passed via `filename=`. That path was supplied by the
        # embedder, so it is not a disclosure — we only assert that
        # the BLOCKED target's resolved path stays out of the message.
        with _Tree(
            {
                'secret/leak.less': '.s { color: leaked; }\n',
                'work/entry.less': '@import "../secret/leak.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail')
            with self.assertRaises(FileError) as cm:
                ls.compile(
                    (root / 'work/entry.less').read_text(),
                    filename=str(root / 'work/entry.less'),
                )
            msg = str(cm.exception)
            self.assertIn('../secret/leak.less', msg)
            self.assertNotIn('Tried', msg)
            # The actual resolved path of the blocked file must not
            # appear anywhere in the message. (`str(root / 'secret/...')`
            # is the absolute resolved path; the relative form
            # `'../secret/leak.less'` above is what the user wrote.)
            self.assertNotIn(str((root / 'secret/leak.less').resolve()), msg)

    def test_jail_mode_missing_file_message_is_minimal(self) -> None:
        # Even when the requested file truly doesn't exist (no security
        # violation), the message stays minimal in jail mode for
        # consistency — embedders parsing the error shouldn't need to
        # branch on `file_io`.
        with _Tree(
            {
                'entry.less': '@import "missing.less";\n',
            }
        ) as root:
            ls = Lessish(file_io='jail')
            with self.assertRaises(FileError) as cm:
                ls.compile(
                    (root / 'entry.less').read_text(),
                    filename=str(root / 'entry.less'),
                )
            self.assertNotIn('Tried', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
