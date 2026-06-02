"""Cross-file `--project` mode."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast

from lessish.linter import Fix, LessLinter, LinterConfig
from lessish.linter._project import build_index, walk_project


class TestProjectIndex(unittest.TestCase):
    def test_walk_finds_less_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'a.less').write_text('.x {}', encoding='utf-8')
            (root / 'sub').mkdir()
            (root / 'sub' / 'b.less').write_text('.y {}', encoding='utf-8')
            (root / 'c.css').write_text('.z {}', encoding='utf-8')
            files = walk_project(root)
            names = sorted(f.name for f in files)
            self.assertEqual(names, ['a.less', 'b.less'])

    def test_index_collects_variable_references(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'defs.less').write_text('@theme: red;\n', encoding='utf-8')
            (root / 'use.less').write_text('.a { color: @theme; }\n', encoding='utf-8')
            index = build_index(walk_project(root))
            self.assertIn('theme', index.variable_references)

    def test_index_collects_mixin_calls(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'defs.less').write_text('.shared() { color: red; }\n', encoding='utf-8')
            (root / 'use.less').write_text('.a { .shared(); }\n', encoding='utf-8')
            index = build_index(walk_project(root))
            self.assertIn('.shared', index.mixin_call_segments)


class TestUnusedVariableInProjectMode(unittest.TestCase):
    def test_variable_used_in_other_file_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'defs.less').write_text('@theme: red;\n', encoding='utf-8')
            (root / 'use.less').write_text('.a { color: @theme; }\n', encoding='utf-8')
            index = build_index(walk_project(root))

            cfg = LinterConfig(enabled={'unused-variable'})
            linter = LessLinter(config=cfg, project_index=index)
            findings = linter.check('@theme: red;\n', filename=str(root / 'defs.less'))
            self.assertEqual(findings, [])

    def test_truly_unused_still_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'defs.less').write_text('@theme: red;\n@dead: blue;\n', encoding='utf-8')
            (root / 'use.less').write_text('.a { color: @theme; }\n', encoding='utf-8')
            index = build_index(walk_project(root))
            cfg = LinterConfig(enabled={'unused-variable'})
            linter = LessLinter(config=cfg, project_index=index)
            findings = linter.check(
                '@theme: red;\n@dead: blue;\n',
                filename=str(root / 'defs.less'),
            )
            self.assertEqual(len(findings), 1)
            self.assertIn('@dead', findings[0].message)

    def test_project_mode_makes_fix_safe(self) -> None:
        # Without project mode, fix is risky; with project mode, safe.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'defs.less').write_text('@unused: red;\n', encoding='utf-8')
            index = build_index(walk_project(root))
            cfg = LinterConfig(enabled={'unused-variable'})
            linter = LessLinter(config=cfg, project_index=index)
            findings = linter.check('@unused: red;\n', filename='defs.less')
            self.assertEqual(len(findings), 1)
            self.assertIsNotNone(findings[0].fix)
            self.assertEqual(cast(Fix, findings[0].fix).safety, 'safe')


class TestUnusedMixinInProjectMode(unittest.TestCase):
    def test_mixin_called_elsewhere_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'defs.less').write_text('.shared() { color: red; }\n', encoding='utf-8')
            (root / 'use.less').write_text('.a { .shared(); }\n', encoding='utf-8')
            index = build_index(walk_project(root))
            cfg = LinterConfig(enabled={'unused-mixin'})
            linter = LessLinter(config=cfg, project_index=index)
            findings = linter.check('.shared() { color: red; }\n', filename=str(root / 'defs.less'))
            self.assertEqual(findings, [])


class TestProjectIndexEdgeCases(unittest.TestCase):
    """Edge cases of `build_index` — unreadable files, parse errors,
    variable refs inside `@{...}` interpolation, AT-AT-NAME refs,
    extend targets / mixin calls inside at-rule bodies.
    """

    def test_at_at_name_collected_as_reference(self) -> None:
        # `@@x` — indirect variable reference; collected for the
        # `unused-variable` rule's cross-file check.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'use.less').write_text('.a { color: @@x; }\n', encoding='utf-8')
            index = build_index(walk_project(root))
        self.assertIn('x', index.variable_references)

    def test_interpolation_variable_reference_collected(self) -> None:
        # `@{name}` — the INTERP_OPEN + IDENT pair is recorded.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'use.less').write_text('@{name}-suffix { color: red; }\n', encoding='utf-8')
            index = build_index(walk_project(root))
        self.assertIn('name', index.variable_references)

    def test_unreadable_file_does_not_crash_index(self) -> None:
        # A path that exists but isn't readable is skipped silently.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            broken = root / 'broken.less'
            broken.write_text('.x {}\n', encoding='utf-8')
            broken.chmod(0o000)
            try:
                index = build_index([broken])
                # Build proceeds; whether the file ends up counted
                # depends on the OS permissions model.
                self.assertGreaterEqual(index.files_scanned, 0)
            finally:
                broken.chmod(0o644)

    def test_unparseable_file_still_advances_count(self) -> None:
        # Tokenize succeeds but parse fails — index records the file
        # as scanned without crashing.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'broken.less').write_text('@import garbage "x";', encoding='utf-8')
            index = build_index(walk_project(root))
        self.assertEqual(index.files_scanned, 1)

    def test_unlexable_file_is_skipped(self) -> None:
        # Backtick triggers UnsupportedFeatureError at parse, but
        # tokenization itself might succeed. Use bytes the lexer
        # rejects: an unterminated `~"` string raises a LessError
        # from tokenize.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'broken.less').write_text('~"unclosed', encoding='utf-8')
            index = build_index(walk_project(root))
        # File doesn't get a parse-pass count because tokenize bailed.
        self.assertEqual(index.files_scanned, 0)

    def test_mixin_call_inside_atrule_body_collected(self) -> None:
        # Cross-file mixin calls inside an `@media { ... }` body are
        # also recorded.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'use.less').write_text('@media (a) { .a { .shared(); } }\n', encoding='utf-8')
            index = build_index(walk_project(root))
        self.assertIn('.shared', index.mixin_call_segments)

    def test_extend_target_collected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'use.less').write_text('.a:extend(.target all) { color: red; }\n', encoding='utf-8')
            index = build_index(walk_project(root))
        self.assertIn('.target', index.extend_targets)

    def test_extend_inside_atrule_body_collected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'use.less').write_text(
                '@media (a) { .x:extend(.target) { color: red; } }\n',
                encoding='utf-8',
            )
            index = build_index(walk_project(root))
        self.assertIn('.target', index.extend_targets)


if __name__ == '__main__':
    unittest.main()
