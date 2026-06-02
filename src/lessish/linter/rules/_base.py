"""Rule base class + LintContext.

Each rule subclasses `Rule`, sets class-level `id`, `severity`,
`fix_tier`, `description`, and implements `check(ctx) -> Iterable[Finding]`.
Rules are stateless — `LintContext` carries everything else.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ... import Lessish
from ...ast_nodes import Node, Ruleset
from ...errors import LessError, SourceLocation
from ...lexer import Token
from ...source import Source
from .._findings import Finding
from .._project import CrossFileIndex


@dataclass
class FileBundle:
    """Per-file parse artefacts shared across every rule on the same
    file. Built once in the engine; lazy on the AST and the evaluated
    tree so a Tier-0-only pass never pays for parsing.

    Sharing is the perf hot path: 13+ rules call `ast()`, and without
    a shared bundle each one re-parsed the file independently.
    """

    source: Source
    tokens: list[Token]
    full_mode: bool = False
    _ast: Ruleset | None = None
    _ast_error: LessError | None = None
    _ast_attempted: bool = False
    _evaluated: Ruleset | None = None
    _evaluated_error: LessError | None = None
    _evaluated_attempted: bool = False

    def ast(self) -> Ruleset | None:
        if not self._ast_attempted:
            self._ast_attempted = True
            try:
                self._ast = Lessish().parse(self.source)
            except LessError as e:
                self._ast_error = e
        return self._ast

    def evaluated_ast(self) -> Ruleset | None:
        if not self.full_mode:
            return None
        if not self._evaluated_attempted:
            self._evaluated_attempted = True
            root = self.ast()
            if root is None:
                return None
            try:
                self._evaluated = Lessish().evaluate(root, src=self.source)
            except LessError as e:
                self._evaluated_error = e
        return self._evaluated


@dataclass
class LintContext:
    """Per-rule view onto a `FileBundle`.

    Each rule receives its own LintContext with rule-specific
    `options`, but the bundle (and therefore the parsed AST / eval'd
    tree) is shared across every rule running on the same file.
    """

    bundle: FileBundle
    options: dict[str, Any]
    project_index: CrossFileIndex | None = None

    @property
    def source(self) -> Source:
        return self.bundle.source

    @property
    def tokens(self) -> list[Token]:
        return self.bundle.tokens

    @property
    def text(self) -> str:
        return self.bundle.source.text

    @property
    def full_mode(self) -> bool:
        return self.bundle.full_mode

    def location_at(self, index: int) -> SourceLocation:
        return self.bundle.source.location_at(index)

    def ast(self) -> Ruleset | None:
        return self.bundle.ast()

    def evaluated_ast(self) -> Ruleset | None:
        return self.bundle.evaluated_ast()


class Rule:
    """Base class for every lint rule.

    Two execution modes — a rule may use one OR the other (or both):

    * **Streaming hooks** — declare interest via `token_kinds` /
      `node_types`. The engine walks tokens and the AST ONCE per
      file, dispatching to every interested rule. Per-file state is
      opaque to the engine: `state_factory()` builds a fresh object
      per file, threaded through every hook. Token hooks fire BEFORE
      AST hooks; cross-pass dependencies (e.g. a rule that wants to
      filter token refs by AST node positions) accumulate in state
      and reconcile in `on_file_end`.

    * **`check(ctx)`** — the rule does its own one-shot pass over
      tokens or the AST. Used for file-text scans (regex over
      `ctx.text`) and rules that need depth / structural context
      streaming doesn't provide cheaply. Engine still shares the
      parsed AST via `ctx.bundle`, so this isn't slower than
      streaming for one-pass file scans.
    """

    id: str = ''
    severity: str = 'warning'
    fix_tier: str = 'none'  # 'safe' | 'risky' | 'none'
    description: str = ''
    requires_eval: bool = False  # if True, only runs when --full is set

    # Streaming declarations. Empty tuples = "I don't subscribe."
    token_kinds: tuple[Any, ...] = ()  # of Kind
    node_types: tuple[type[Node], ...] = ()  # of Node subclasses

    def state_factory(self) -> Any:
        """Return a fresh per-file scratch object passed to every hook
        of this rule on the file. Stateless rules return None.
        """
        return None

    def on_file_start(self, ctx: LintContext, state: Any) -> None:  # noqa: ARG002
        """Called once per file before token/AST dispatch."""

    def on_token(self, tok: Token, idx: int, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        return ()

    def on_node(self, node: Node, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        return ()

    def on_file_end(self, ctx: LintContext, state: Any) -> Iterable[Finding]:  # noqa: ARG002
        return ()

    def check(self, ctx: LintContext) -> Iterable[Finding]:  # noqa: ARG002
        """One-shot per-file hook. Default no-op so streaming-only
        rules can omit it.
        """
        return ()
