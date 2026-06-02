"""LessLinter — orchestrator that runs configured rules against source.

Precedence (highest wins):

  inline directives > CLI flags > project config > built-in defaults

A rule is run when EITHER config has it active OR an inline directive
in the file mentions it (force-enable would otherwise be inert).

A finding survives when the directive map at its line says
force-enable, OR (no inline override AND config has the rule active).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .. import Lessish
from ..errors import LessError, ParseError, UnsupportedFeatureError
from ..lexer import Kind, Token
from ..source import Source
from ._config import LinterConfig
from ._directives import DirectiveMap, scan_directives
from ._findings import Finding, FixOptions
from ._fix import apply_fixes
from ._project import CrossFileIndex
from ._walker import walk as walk_ast
from ._watchdog import RuleTimeoutError, watchdog
from .rules import ALL_RULES, LintContext, Rule
from .rules._base import FileBundle

_ProcessFn = Callable[[Iterable[Finding], DirectiveMap], Iterable[Finding]]

# Per-rule watchdog budget. A rule that takes longer than this on a
# single file is killed and recorded as a `watchdog-timeout` finding.
# 30 s is generous — every shipping rule runs well under 1 s; the
# margin is there so an exotic real-world fixture doesn't false-trigger.
DEFAULT_RULE_TIMEOUT_SECONDS = 30.0


class LessLinter:
    """Top-level linter facade.

    `respect_inline` is the kill-switch — set to False (CLI flag
    `--no-inline-config`) to ignore inline directives entirely.

    `rule_timeout_seconds` caps how long any single rule may run on
    one file before the watchdog fires; `0` disables the watchdog.
    """

    def __init__(
        self,
        config: LinterConfig | None = None,
        *,
        respect_inline: bool = True,
        full: bool = False,
        project_index: CrossFileIndex | None = None,
        rule_timeout_seconds: float = DEFAULT_RULE_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config or LinterConfig()
        self.respect_inline = respect_inline
        self.full = full
        self.project_index = project_index
        self.rule_timeout_seconds = rule_timeout_seconds
        self._rules: tuple[Rule, ...] = tuple(cls() for cls in ALL_RULES)

    def check(self, source: str, *, filename: str = '<input>') -> list[Finding]:
        src = Source(text=source, filename=filename)
        return list(self._check_source(src))

    def fix(
        self,
        source: str,
        *,
        filename: str = '<input>',
        fix_options: FixOptions | None = None,
    ) -> str:
        opts = fix_options or FixOptions()
        text = source
        for _ in range(opts.max_passes):
            findings = self.check(text, filename=filename)
            text, applied = apply_fixes(text, findings, safe_only=opts.safe_only)
            if applied == 0:
                break
        return text

    def _check_source(self, src: Source) -> Iterable[Finding]:
        try:
            tokens = list(Lessish().tokenize(src))
        except ParseError as e:
            yield _findings_from_error(e, src, 'parse')
            return

        directives = scan_directives(tokens, src) if self.respect_inline else DirectiveMap()

        # One bundle per file — parsed AST is built lazily on first
        # access and shared across every rule on the file (13+ rules
        # call `ast()`; without sharing each re-parses the file).
        bundle = FileBundle(source=src, tokens=tokens, full_mode=self.full)

        active: list[tuple[Rule, LintContext, Any]] = []
        token_dispatch: dict[Kind, list[tuple[Rule, LintContext, Any]]] = {}
        node_dispatch: dict[type, list[tuple[Rule, LintContext, Any]]] = {}

        for rule in self._rules:
            if not self._should_run(rule.id, directives):
                continue
            if rule.requires_eval and not self.full:
                continue
            ctx = LintContext(
                bundle=bundle,
                options=self.config.options_for(rule.id),
                project_index=self.project_index,
            )
            state = rule.state_factory()
            entry = (rule, ctx, state)
            active.append(entry)
            for kind in rule.token_kinds:
                token_dispatch.setdefault(kind, []).append(entry)
            for node_type in rule.node_types:
                node_dispatch.setdefault(node_type, []).append(entry)

        process = self._process_findings
        timeout = self.rule_timeout_seconds

        # Streaming hooks are individually cheap; wrap the whole
        # streaming pass in ONE file-level watchdog. A per-event
        # setitimer call costs an order of magnitude more than the
        # hook work itself.
        try:
            with watchdog(seconds=timeout, rule_id='*'):
                yield from self._run_streaming_pass(
                    active,
                    token_dispatch,
                    node_dispatch,
                    bundle,
                    tokens,
                    directives,
                    process,
                    src,
                )
        except RuleTimeoutError as e:
            yield _watchdog_finding(e, src)

        # One-shot `check()` calls may do heavy per-file work; wrap
        # each individually so a hang is attributed to the right rule.
        yield from self._run_check_pass(active, directives, process, src, timeout)

    def _run_streaming_pass(
        self,
        active: list[tuple[Rule, LintContext, Any]],
        token_dispatch: dict[Kind, list[tuple[Rule, LintContext, Any]]],
        node_dispatch: dict[type, list[tuple[Rule, LintContext, Any]]],
        bundle: FileBundle,
        tokens: list[Token],
        directives: DirectiveMap,
        process: _ProcessFn,
        src: Source,
    ) -> Iterable[Finding]:
        for rule, ctx, state in active:
            rule.on_file_start(ctx, state)

        if token_dispatch:
            for idx, tok in enumerate(tokens):
                subscribers = token_dispatch.get(tok.kind)
                if not subscribers:
                    continue
                for rule, ctx, state in subscribers:
                    findings = rule.on_token(tok, idx, ctx, state)
                    if findings:
                        yield from process(findings, directives)

        if node_dispatch:
            try:
                ast = bundle.ast()
            except LessError as e:
                yield _findings_from_error(e, src, 'parse')
                ast = None
            if ast is not None:
                for node in walk_ast(ast):
                    subscribers = node_dispatch.get(type(node))
                    if not subscribers:
                        continue
                    for rule, ctx, state in subscribers:
                        findings = rule.on_node(node, ctx, state)
                        if findings:
                            yield from process(findings, directives)

        for rule, ctx, state in active:
            findings = rule.on_file_end(ctx, state)
            if findings:
                yield from process(findings, directives)

    def _run_check_pass(
        self,
        active: list[tuple[Rule, LintContext, Any]],
        directives: DirectiveMap,
        process: _ProcessFn,
        src: Source,
        timeout: float,
    ) -> Iterable[Finding]:
        for rule, ctx, _state in active:
            try:
                with watchdog(seconds=timeout, rule_id=rule.id):
                    findings = list(rule.check(ctx))
            except RuleTimeoutError as e:
                yield _watchdog_finding(e, src)
                continue
            except UnsupportedFeatureError as e:
                yield _findings_from_error(e, src, 'parse')
                continue
            except LessError as e:
                yield _findings_from_error(e, src, 'parse')
                continue
            if findings:
                yield from process(findings, directives)

    def _process_findings(self, findings: Iterable[Finding], directives: DirectiveMap) -> Iterable[Finding]:
        for finding in findings:
            if not self._finding_active(finding, directives):
                continue
            yield Finding(
                rule_id=finding.rule_id,
                severity=self.config.severity_for(finding.rule_id, finding.severity),
                message=finding.message,
                location=finding.location,
                span=finding.span,
                fix=finding.fix,
            )

    def _should_run(self, rule_id: str, directives: DirectiveMap) -> bool:
        """A rule runs if config enables it OR an inline directive in
        the file might want findings from it. We can't decide finding-
        by-finding without running the rule first.
        """
        if self.config.rule_active(rule_id):
            return True
        return directives.touches(rule_id)

    def _finding_active(self, finding: Finding, directives: DirectiveMap) -> bool:
        """inline (if any) > config."""
        inline = directives.lookup(finding.rule_id, finding.location.line)
        if inline is not None:
            return inline
        return self.config.rule_active(finding.rule_id)


def _findings_from_error(e: LessError, src: Source, _kind: str) -> Finding:
    loc = e.location or src.location_at(0)
    return Finding(
        rule_id='parse-error',
        severity='error',
        message=e.message,
        location=loc,
        span=(loc.index, loc.index),
    )


def _watchdog_finding(e: RuleTimeoutError, src: Source) -> Finding:
    return Finding(
        rule_id='watchdog-timeout',
        severity='error',
        message=str(e),
        location=src.location_at(0),
        span=(0, 0),
    )
