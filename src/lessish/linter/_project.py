"""Cross-file analysis: project-wide reference index.

When the user passes `--project <dir>`, we walk every `.less` file
under that dir, collect variable references, mixin call segments, and
`:extend(...)` targets, and inject the union into each file's
`LintContext`. Rules that participate (`unused-variable`,
`unused-mixin`) consult the index before flagging.

Cross-file mode raises the safety of those Tier-2 fixes from risky to
safe — we're seeing the entire reference set, so the autofix can run
under `--fix` (not just `--unsafe-fix`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import Lessish
from ..ast_nodes import (
    AtRule,
    Extend,
    MixinCall,
    Node,
    Ruleset,
)
from ..errors import LessError
from ..lexer import Kind, Token

_SEGMENT_RE = re.compile(r'[#.>][^.#>\s]+')


@dataclass
class CrossFileIndex:
    variable_references: set[str] = field(default_factory=set)
    mixin_call_segments: set[str] = field(default_factory=set)
    extend_targets: set[str] = field(default_factory=set)
    files_scanned: int = 0


def walk_project(root: Path) -> list[Path]:
    """Return every `.less` file under `root`, sorted for determinism."""
    return sorted(root.rglob('*.less'))


def build_index(files: list[Path]) -> CrossFileIndex:
    index = CrossFileIndex()
    lessish = Lessish()
    for f in files:
        try:
            text = f.read_text(encoding='utf-8')
        except OSError:
            continue
        try:
            tokens = list(lessish.tokenize(text))
        except LessError:
            continue
        _collect_var_refs(tokens, index)
        try:
            ast = lessish.parse(text, filename=str(f))
        except LessError:
            index.files_scanned += 1
            continue
        _collect_mixin_calls(ast, index)
        _collect_extends(ast, index)
        index.files_scanned += 1
    return index


def _collect_var_refs(tokens: list[Token], index: CrossFileIndex) -> None:
    for i, t in enumerate(tokens):
        if t.kind is Kind.AT_NAME:
            # Skip the LHS of declarations: an AT_NAME followed by COLON
            # at a name-position is a declaration, not a reference.
            if _is_decl_lhs(tokens, i):
                continue
            index.variable_references.add(t.text[1:])
        elif t.kind is Kind.AT_AT_NAME:
            index.variable_references.add(t.text[2:])
        elif t.kind is Kind.INTERP_OPEN and i + 1 < len(tokens):
            if tokens[i + 1].kind is Kind.IDENT:
                index.variable_references.add(tokens[i + 1].text)


def _is_decl_lhs(tokens: list[Token], i: int) -> bool:
    """Approximate `@x:` test: previous non-trivia is `{`/`;`/`}`/start,
    and next non-trivia is `:`.
    """
    if i + 1 >= len(tokens):
        return False
    nxt = tokens[i + 1]
    if nxt.kind is not Kind.COLON:
        return False
    if i == 0:
        return True
    prev = tokens[i - 1]
    return prev.kind in (Kind.LBRACE, Kind.RBRACE, Kind.SEMICOLON)


def _collect_mixin_calls(root: Ruleset, index: CrossFileIndex) -> None:
    def visit(n: Node) -> None:
        if isinstance(n, MixinCall):
            for seg in _SEGMENT_RE.findall(n.name) or [n.name]:
                index.mixin_call_segments.add(seg)
        if isinstance(n, Ruleset):
            for c in n.rules:
                visit(c)
        elif isinstance(n, AtRule) and n.body is not None:
            for c in n.body:
                visit(c)

    visit(root)


def _collect_extends(root: Ruleset, index: CrossFileIndex) -> None:
    def visit(n: Node) -> None:
        if isinstance(n, Ruleset):
            for sel in n.selectors:
                for ext in sel.extend_list:
                    if isinstance(ext, Extend):
                        for seg in _SEGMENT_RE.findall(ext.target) or [ext.target]:
                            index.extend_targets.add(seg)
            for c in n.rules:
                visit(c)
        elif isinstance(n, AtRule) and n.body is not None:
            for c in n.body:
                visit(c)

    visit(root)
