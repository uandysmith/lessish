"""The `Importer` class — orchestrates `@import` resolution.

A pre-eval pass that walks the parsed tree, finds `AtRule(name='@import')`,
parses the prelude into (options, path, media-features), reads and parses
the imported file, and splices its rules in.

Supported qualifiers (in `(...)` prefix):
  * `once`       — default; if the file is already imported, skip it.
  * `multiple`   — always include, even on repeated imports.
  * `optional`   — missing file is not an error; silently drop the import.
  * `reference`  — include for mixin/variable lookup; suppress emission.
  * `inline`     — read the file as text, splice verbatim without parsing.
  * `less`       — force Less parsing regardless of extension.
  * `css`        — treat as CSS; keep `@import` in the output unchanged.

Path resolution: relative paths resolve against the importing file's
directory first, then against each search dir in `paths`. Files without
an extension fall back to `<path>.less`.

Cycle detection caps recursion via a stack of currently-importing paths.
Files at depth zero (already-imported once) are skipped unless `multiple`
is set.
"""

from __future__ import annotations

from pathlib import Path

from ..ast_nodes import AtRule, MixinDefinition, Node, Ruleset
from ..ast_nodes import Comment as _Comment
from ..errors import FileError as _FileError
from ..errors import ParseError as _ParseError
from ..errors import SecurityError, UnsupportedFeatureError
from ..mixins import transform_mixins
from ..parser import parse as _parse
from ..source import Source
from .spec import _is_malformed_import_prelude, parse_import_prelude
from .transforms import (
    _atrule_has_inner_rulesets,
    _inline_as_nodes,
    _mark_reference,
    _pass_through_as_css,
    _tag_source,
)
from .url_classify import (
    _has_known_extension,
    _is_absolute_filesystem_path,
    _is_remote_url,
    _path_basename,
)
from .url_rewrite import _rewrite_urls_in_rules


class Importer:
    """Resolves `@import` directives by reading, parsing, and splicing.

    Lifetime: one instance per `compile()` call. The `_seen` set persists
    across recursive imports so a file imported transitively only parses
    once (default `(once)` semantics).
    """

    def __init__(
        self,
        *,
        source_path: str,
        paths: list[str],
        rewrite_urls: str = 'off',
        rootpath: str = '',
        url_args: str = '',
        process_imports: bool = True,
        file_io: str = 'jail',
        max_input_size: int | None = None,
        entry_input_chars: int = 0,
    ) -> None:
        # `source_path` may be a sentinel like '<input>'; in that case
        # base_dir is the current working directory. Real paths use the
        # file's parent directory so `@import "x"` resolves alongside.
        # The branch structure keeps the in-memory hot path
        # (`source_path == '<input>'`) at exactly one `Path.cwd()`
        # syscall and zero `stat()` calls — short-lived "render this
        # LESS snippet" use cases call this on every compile.
        base = Path(source_path)
        parent = base.parent
        if source_path == '<input>':
            # In-memory sentinel. `Path('<input>').parent.name` is
            # always '' so the parent-dir branch below would miss
            # anyway; short-circuit.
            self.base_dir = Path.cwd()
        elif parent.name:
            # Real path with an explicit parent — use it whether or
            # not the file exists on disk. Missing files surface as
            # `FileError` at `@import` time, which is the right
            # diagnostic; defaulting to cwd here would silently
            # change the search root.
            self.base_dir = parent
        else:
            # Bare filename (`'foo.less'`, `'./foo.less'`, …) — no
            # disk anchor available, fall back to cwd.
            self.base_dir = Path.cwd()
        self.paths = [Path(p) for p in paths]
        self.rewrite_urls = rewrite_urls
        self.rootpath = rootpath
        self.url_args = url_args
        # `processImports: False` (less.js option) — when set, less-shaped
        # `@import`s get dropped from the output (since the fetch+inline
        # step is the entire point of import processing and we're told
        # not to do it). CSS-shaped imports (`.css` extension, `(css)`
        # qualifier, or any URL recognised as plain CSS) survive as
        # pass-through directives so the browser handles them. See
        # `_handle_import` for the per-spec routing.
        self.process_imports = process_imports
        if file_io not in ('allow', 'jail', 'deny'):
            raise ValueError(f"file_io must be 'allow', 'jail', or 'deny'; got {file_io!r}")
        self.file_io = file_io
        # `jail` mode treats the search roots (base_dir + every entry in
        # `paths`) as the only locations Less source is allowed to read
        # from. Pre-resolve them once so `_check_jail` can run an
        # is-relative-to test without re-resolving on every read.
        self._jail_roots: list[Path] = []
        if file_io == 'jail':
            self._jail_roots.append(self.base_dir.resolve())
            for p in self.paths:
                try:
                    self._jail_roots.append(p.resolve())
                except OSError:
                    # A non-existent paths[] entry can still be listed
                    # in less.js — just skip it for jail purposes.
                    pass
        self._seen: set[Path] = set()
        self._stack: list[Path] = []
        # Cumulative input budget (characters). The entry source is
        # pre-charged so the remaining allowance bounds the *sum* of all
        # imported files, not each one independently. `None` → off.
        self._input_remaining: int | None = None
        if max_input_size is not None:
            self._input_remaining = max_input_size - entry_input_chars
        self._max_input_size = max_input_size

    def resolve(self, root: Ruleset) -> Ruleset:
        """Return a new tree with all `@import` directives replaced by
        the corresponding rules (or left as-is for `(css)` imports).
        """
        # Walk the import graph in BFS order to decide WHICH `@import`
        # AtRule "wins" inlining for each file. less.js's import-visitor
        # is async and FIFO-queue-based, so a file imported transitively
        # at depth-2 loses to the same file imported directly at depth-1
        # (top-level). Mirroring that claim order is what makes
        # weui-shaped graphs round-trip byte-identically. Without this
        # pass our DFS resolver would inline at the FIRST-seen
        # (deepest-DFS) position, producing a different — but
        # equally-valid CSS-wise — emission order.
        self._bfs_assign_import_claims(root.rules, self.base_dir)
        new_rules = self._walk_rules(root.rules, self.base_dir)
        # When rewriteUrls is active, normalise url() values in the
        # outer file too. Paths are already relative to base_dir, so the
        # rewrite is effectively a normalisation pass (collapse `..`/`.`).
        if self.rewrite_urls != 'off':
            new_rules = _rewrite_urls_in_rules(
                new_rules,
                sub_dir=self.base_dir,
                base_dir=self.base_dir,
                mode=self.rewrite_urls,
                rootpath=self.rootpath,
            )
        return Ruleset(
            index=root.index,
            selectors=root.selectors,
            rules=new_rules,
            root=root.root,
            paths=root.paths,
            condition=root.condition,
        )

    def resolve_deferred(self, at: AtRule, substituted_prelude: str) -> list[Node]:
        """Re-run `_handle_import` for an `@import` whose path was marked
        `_deferred_import` (it contained `@{…}`) and is now substituted.
        `substituted_prelude` is the prelude after `@{var}` resolution.
        Returns the list of resulting nodes — raw (un-evaluated) AST
        from the imported file; the caller is responsible for splicing
        them and (re-)evaluating in the current frame.
        """
        base_dir = at._import_base_dir if at._import_base_dir is not None else self.base_dir
        substituted = AtRule(
            index=at.index,
            name=at.name,
            prelude=substituted_prelude,
            body=at.body,
            trailing_comments=at.trailing_comments,
        )
        return self._handle_import(substituted, base_dir)

    def _bfs_assign_import_claims(self, root_rules: list[Node], root_base_dir: Path) -> None:
        """BFS over the @import graph to decide which `@import` "claims"
        each file. Mirrors less.js's import-visitor + sequencer where
        onImported callbacks fire in BFS-FIFO order — top-level imports'
        callbacks fire before any nested imports' callbacks, so the
        top-level position "wins" inlining for any file referenced both
        ways. weui's `theme/index.less` is the canonical case: imported
        from `weui.less` line 20 AND transitively from `base/fn.less`
        line 17. less.js inlines at line 20; our prior DFS resolver
        inlined at line 17.

        Two outputs from this pass:
        * `_import_bfs_duplicate` on every @import AtRule whose target
          was already claimed by an earlier-in-BFS-order @import.
        * `_import_bfs_parsed` on every claiming @import AtRule — the
          pre-parsed content of its target file. Reused by
          `_handle_import` so the splice pass doesn't re-parse, and
          (more importantly) so the markers we placed on inner
          @imports survive into the splice pass (they're on the cached
          AST, not on the freshly-reparsed copy that would otherwise
          replace them).
        """
        from collections import deque

        # FIFO of (rules_list, base_dir_for_relative_paths).
        queue: deque[tuple[list[Node], Path]] = deque()
        queue.append((root_rules, root_base_dir))

        # Track files seen in BFS order. First encounter "claims" the
        # AtRule; later encounters get tagged `_import_bfs_duplicate`.
        seen_paths: set[Path] = set()

        while queue:
            rules, base_dir = queue.popleft()
            # First pass over THIS chunk: handle @imports in source order.
            for rule in rules:
                if isinstance(rule, AtRule) and rule.name == '@import':
                    resolved = self._bfs_resolve_import_target(rule, base_dir)
                    if resolved is None:
                        continue
                    if resolved in seen_paths:
                        rule._import_bfs_duplicate = True
                        continue
                    seen_paths.add(resolved)
                    # Parse the file NOW (instead of in _handle_import)
                    # so we can both annotate its nested @imports and
                    # later splice the same AST without reparsing.
                    try:
                        sub_source = Source(
                            text=self._read_source(resolved, at=rule),
                            filename=str(resolved),
                        )
                        parsed_root = _parse(sub_source)
                        transformed = transform_mixins(parsed_root)
                        assert isinstance(transformed, Ruleset)
                        rule._import_bfs_parsed = transformed
                        rule._import_bfs_source = sub_source
                        queue.append((transformed.rules, resolved.parent))
                    except Exception:
                        # Parse / IO errors surface during the splice
                        # pass with the same code path as the un-BFS'd
                        # case — keep this prefetch best-effort.
                        pass
                # Recurse into containers for nested @imports.
                elif isinstance(rule, Ruleset):
                    queue.append((rule.rules, base_dir))
                elif isinstance(rule, AtRule) and rule.body is not None:
                    queue.append((rule.body, base_dir))
                elif isinstance(rule, MixinDefinition):
                    queue.append((rule.rules, base_dir))

    def _bfs_resolve_import_target(self, at: AtRule, base_dir: Path) -> Path | None:
        """Lightweight resolver for the BFS pre-pass: returns the
        canonical Path that the @import targets, or None for imports
        that don't participate in file-level dedup (remote URLs, CSS
        passthrough, inline, deferred interpolation, malformed). Also
        short-circuits when `file_io='deny'` is in effect — we won't
        read this file anyway, no point pre-parsing it.
        """
        if self.file_io == 'deny':
            return None
        spec = parse_import_prelude(at.prelude)
        if not spec.path or '@{' in spec.path:
            return None
        if _is_malformed_import_prelude(at.prelude):
            return None
        if _is_remote_url(spec.path) or _is_absolute_filesystem_path(spec.path):
            return None
        if 'multiple' in spec.options:
            return None
        if 'inline' in spec.options:
            return None
        if 'css' in spec.options:
            return None
        if spec.path.endswith('.css') and 'less' not in spec.options:
            return None
        return self._resolve_path(spec.path, base_dir)

    def _walk_rules(self, rules: list[Node], base_dir: Path) -> list[Node]:
        out: list[Node] = []
        for rule in rules:
            if isinstance(rule, AtRule) and rule.name == '@import':
                out.extend(self._handle_import(rule, base_dir))
                continue
            if isinstance(rule, AtRule) and rule.body is not None:
                # @media etc. — recurse into the body so nested
                # `@import`s resolve in context.
                out.append(
                    AtRule(
                        index=rule.index,
                        name=rule.name,
                        prelude=rule.prelude,
                        body=self._walk_rules(rule.body, base_dir),
                        trailing_comments=rule.trailing_comments,
                    )
                )
                continue
            if isinstance(rule, Ruleset):
                out.append(
                    Ruleset(
                        index=rule.index,
                        selectors=rule.selectors,
                        rules=self._walk_rules(rule.rules, base_dir),
                        root=rule.root,
                        paths=rule.paths,
                        condition=rule.condition,
                    )
                )
                continue
            # Mixin definition bodies can contain `@import` directives
            # too — `less.js` resolves them so the imported rules splice
            # in at call sites. Recurse here so the body is rewritten in
            # place. Avoids leaking a literal `@import …;` into the
            # call-site emit.
            if isinstance(rule, MixinDefinition):
                rule.rules = self._walk_rules(rule.rules, base_dir)
                out.append(rule)
                continue
            out.append(rule)
        return out

    def _handle_import(self, at: AtRule, base_dir: Path) -> list[Node]:
        # BFS pre-pass verdict overrides DFS source-order dedup. Skip
        # imports the BFS marked as duplicates (their target file is
        # being inlined elsewhere, at the BFS-first claim position).
        if at._import_bfs_duplicate:
            return []
        spec = parse_import_prelude(at.prelude)
        if not spec.path:
            return [at]  # malformed — keep as-is
        # `processImports: False` (less.js option) routing. Empirical
        # less.js behaviour:
        #   * `.css` extension OR explicit `(css)` qualifier   → kept
        #     in output (valid CSS @import, browser handles it).
        #   * everything else (`.less`, no extension, `(less)`,
        #     `(reference)`, `(inline)`, etc.)                 → dropped
        #     silently. less.js's logic is "if I'd have to fetch+inline
        #     to give you useful output, and you told me not to fetch,
        #     the import is now dead weight; drop it rather than leak
        #     a broken `@import "foo.less"` line into CSS the browser
        #     can't handle." We mirror that exactly.
        if not self.process_imports:
            forces_css = 'css' in spec.options
            is_css_ext = spec.path.endswith('.css') and 'less' not in spec.options
            if forces_css or is_css_ext:
                return [_pass_through_as_css(at, spec)]
            return []
        # Path contains `@{var}` interpolation — the importer runs before
        # evaluation so the variable isn't bound yet. Mark the AtRule and
        # leave it in place; the evaluator re-resolves it through
        # `Importer.resolve_deferred` once `ctx` is available.
        if '@{' in spec.path:
            at._deferred_import = True
            at._import_base_dir = base_dir
            return [at]
        # Strict-mode check: bare identifier before the path (e.g.
        # `@import malformed "file.less"`) is invalid. less.js raises
        # `SyntaxError: malformed import statement`.
        if _is_malformed_import_prelude(at.prelude):
            err = _ParseError('malformed import statement')
            err._less_js_name = 'SyntaxError'
            err._base_index = at.index
            raise err

        # Remote URLs (http/https/ftp scheme, protocol-relative `//host`):
        # less.js fetches Less-content remote imports at compile time
        # (RCE-shaped — arbitrary network reads, fetched text runs
        # through the evaluator). We refuse those outright. Plain CSS
        # remote `@import`s (`@import url(https://fonts.googleapis.com/...)`
        # — fonts/CDN stylesheets) are *not* fetched at compile time;
        # they pass through verbatim into the output CSS and the
        # browser handles the fetch at render time. Those stay.
        # `(optional)` silently skips even Less-content remotes so
        # gracefully-degrading trees don't break.
        if _is_remote_url(spec.path):
            forces_css = 'css' in spec.options
            forces_less = 'inline' in spec.options or 'reference' in spec.options or 'less' in spec.options
            looks_less = _path_basename(spec.path).endswith('.less')
            needs_fetch = not forces_css and (forces_less or looks_less)
            if needs_fetch:
                if 'optional' in spec.options:
                    return []
                remote_err = UnsupportedFeatureError(
                    f'remote @import {spec.path!r} would require fetching '
                    f'Less content over the network at compile time — not '
                    f'supported by lessish (RCE-shaped vector: fetched text '
                    f'runs through the evaluator). Use `(css)` to keep as '
                    f'a plain CSS @import (browser fetches at render time), '
                    f'vendor the file locally, or mark `(optional)` to skip.'
                )
                remote_err._base_index = at.index
                raise remote_err
            # Plain CSS remote @import (no Less qualifier, no `.less`
            # extension, or explicit `(css)` override): emit as a CSS
            # `@import` with Less qualifiers stripped — `(css)` etc.
            # aren't valid CSS, the browser would reject the prelude.
            return [_pass_through_as_css(at, spec)]
        # Absolute filesystem paths (`/usr/local/...`).
        #
        # `allow`: pass through as a CSS `@import` — a
        # deliberate divergence from less.js (which reads the file).
        # Pass-through is safer and we keep it that way even in
        # `allow` mode.
        # `jail` / `deny`: refuse with SecurityError so the source
        # can't smuggle an absolute path past the policy unnoticed.
        if _is_absolute_filesystem_path(spec.path):
            if self.file_io != 'allow':
                self._check_io_allowed(path=spec.path, at=at)
            return [_pass_through_as_css(at, spec)]

        # Inline mode wins over everything else: read the file as text
        # and splice into the tree as a single verbatim chunk.
        if 'inline' in spec.options:
            self._check_io_allowed(path=spec.path, at=at)
            resolved_inline = self._resolve_path(spec.path, base_dir)
            if resolved_inline is None:
                if 'optional' in spec.options:
                    return []
                raise self._make_file_not_found_error(spec.path, base_dir, at)
            text = self._read_source(resolved_inline, at=at)
            nodes = _inline_as_nodes(text, at)
            if spec.media:
                return [
                    AtRule(
                        index=at.index,
                        name='@media',
                        prelude=spec.media,
                        body=nodes,
                    )
                ]
            return nodes

        # CSS treatment: keep the `@import` in the output. This applies
        # when the file ends `.css` (and `(less)` isn't set) or when
        # the user explicitly opts into `(css)`. Strip Less-only
        # qualifiers from the prelude — `(css)` etc. aren't valid CSS.
        force_less = 'less' in spec.options
        force_css = 'css' in spec.options
        if force_css or (spec.path.endswith('.css') and not force_less):
            return [_pass_through_as_css(at, spec)]

        self._check_io_allowed(path=spec.path, at=at)

        # Resolve path on disk. `(less)` strips any non-.less extension
        # mismatch (we just look up the file as named).
        resolved = self._resolve_path(spec.path, base_dir)
        if resolved is None:
            if 'optional' in spec.options:
                return []
            raise self._make_file_not_found_error(spec.path, base_dir, at)

        # Once-vs-multiple semantics: by default a file imported twice
        # only contributes once. `(multiple)` opts out.
        multiple = 'multiple' in spec.options
        if not multiple and resolved in self._seen:
            return []
        if resolved in self._stack:
            # Cycle. Without `(multiple)` this is harmless; with it,
            # silently break the cycle to avoid stack overflow.
            return []

        self._seen.add(resolved)
        self._stack.append(resolved)
        # `(multiple)` on a parent propagates to transitive children:
        # `f.less` imported (multiple) twice re-parses, and each parse
        # also re-includes `f`'s own `@import "e";` even though `e`
        # itself is default-`once`. less.js does this by allocating a
        # fresh import session for each multiple top-level entry; we
        # mirror with a saved/restored `_seen` set.
        saved_seen: set[Path] | None = None
        if multiple:
            saved_seen = self._seen
            self._seen = {resolved}
        try:
            # Reuse the parse from the BFS pre-pass if available: the
            # markers it left on nested @imports live on THAT AST, so
            # reparsing here would lose them. For `(multiple)` imports
            # the BFS skips dedup, so there's no cache — parse fresh.
            cached = at._import_bfs_parsed
            cached_source = at._import_bfs_source
            transformed: Node
            if cached is not None and cached_source is not None and not multiple:
                sub_source = cached_source
                transformed = cached
            else:
                sub_source = Source(text=self._read_source(resolved, at=at), filename=str(resolved))
                parsed_root = _parse(sub_source)
                transformed = transform_mixins(parsed_root)
            assert isinstance(transformed, Ruleset)
            sub_rules = self._walk_rules(transformed.rules, resolved.parent)
            # Tag every node in the imported subtree with its source so
            # later evaluator-time errors anchor at the *imported* file's
            # `<file>:line:column`, not the outer compile's source.
            for r in sub_rules:
                _tag_source(r, sub_source)
            # Rewrite url() values inside imported declarations so they
            # resolve relative to the *consuming* file's directory rather
            # than the imported file's. Triggered by `rewriteUrls` option.
            if self.rewrite_urls != 'off':
                sub_rules = _rewrite_urls_in_rules(
                    sub_rules,
                    sub_dir=resolved.parent,
                    base_dir=base_dir,
                    mode=self.rewrite_urls,
                    rootpath=self.rootpath,
                )
        finally:
            self._stack.pop()
            if saved_seen is not None:
                # Merge so future siblings don't re-import these too.
                saved_seen.update(self._seen)
                self._seen = saved_seen

        # `(reference)` qualifier — rules imported this way participate
        # in extend/mixin resolution but emit no CSS unless `:extend`-ed
        # or mixin-invoked from a non-reference site (J4). Mark each
        # top-level Ruleset with `reference=True` (deep) so `emit_ruleset`
        # skips it. Block-form at-rules whose bodies contain Rulesets
        # (`@media`, `@supports`, `@container`) are KEPT with the
        # `_reference` tag so `:extend(...)` from outside can reach
        # inner targets — media.less's `@media print { .class { … } }`
        # is supposed to surface under a `.class` extender. Statement-
        # form at-rules (`@import`, `@namespace`, `@charset`) and bodies
        # that hold only Declarations (`@font-face`, `@page`, …) carry
        # no extend-targets, so drop them outright. Comments are also
        # dropped since they can't be referenced.
        if 'reference' in spec.options:
            filtered: list[Node] = []
            for r in sub_rules:
                if isinstance(r, _Comment):
                    continue
                if isinstance(r, AtRule):
                    if r.body is None or not _atrule_has_inner_rulesets(r):
                        continue
                marked = _mark_reference(r)
                if isinstance(marked, AtRule):
                    # The AtRule itself gets `_reference=True` so
                    # `emit_atrule` skips it unless one of its inner
                    # Rulesets later picks up extend-added paths.
                    marked._reference = True
                filtered.append(marked)
            sub_rules = filtered

        # Media-query suffix wraps the imported rules in an @media at-rule.
        if spec.media:
            return [
                AtRule(
                    index=at.index,
                    name='@media',
                    prelude=spec.media,
                    body=sub_rules,
                )
            ]
        return sub_rules

    def _read_source(self, resolved: Path, *, at: AtRule | None = None) -> str:
        """Read an imported file as text, charging its size against the
        cumulative input budget (`max_input_size`).

        A file whose on-disk byte size already exceeds the remaining
        allowance is rejected *before* it is read into memory — in UTF-8
        the byte count is an upper bound on the character count, so this
        never under-counts. Returns the file text; raises `ParseError`
        anchored at the `@import` when the budget is exhausted.
        """
        if self._input_remaining is not None:
            try:
                size = resolved.stat().st_size
            except OSError:
                size = 0
            if size > self._input_remaining:
                raise self._input_budget_error(resolved, at)
        text = resolved.read_text(encoding='utf-8')
        if self._input_remaining is not None:
            self._input_remaining -= len(text)
            if self._input_remaining < 0:
                raise self._input_budget_error(resolved, at)
        return text

    def _input_budget_error(self, resolved: Path, at: AtRule | None) -> Exception:
        err = _ParseError(
            f'cumulative @import input exceeds max_input_size ({self._max_input_size} characters) at {resolved.name!r}'
        )
        if at is not None:
            err._base_index = at.index
        return err

    def _make_file_not_found_error(self, spec_path: str, base_dir: Path, at: AtRule) -> Exception:
        """Build a less.js-shaped `FileError: '<path>' wasn't found.
        Tried - <candidate-list>` exception. The candidate list mirrors
        less.js's resolver order: current-file-relative, paths-option,
        `npm://<spec>`, and the raw `<spec>` itself. Anchored at the
        `@import` AtRule's source position via the `_base_index` flag.

        In `jail` / `deny` modes the `Tried - …` list is suppressed:
        enumerating absolute filesystem paths that we deliberately
        refused to read would let a hostile Less source map out the
        host's directory layout via probe-and-error-message. The bare
        `'<spec>' wasn't found` form keeps the diagnostic useful for
        legitimate typos without disclosing anything the source did
        not already supply.
        """
        if self.file_io != 'allow':
            err = _FileError(f"'{spec_path}' wasn't found.")
            err._base_index = at.index
            return err

        # less.js lists current-dir, paths, npm://, raw — including
        # candidates we didn't actually consult — for exact format
        # parity with its error message.
        tried: list[str] = [str((base_dir / spec_path).resolve())]
        for search_dir in self.paths:
            tried.append(str((search_dir / spec_path).resolve()))
        if len(tried) < 2:
            tried.append(tried[0])
        tried.append(f'npm://{spec_path}')
        tried.append(spec_path)
        err = _FileError(f"'{spec_path}' wasn't found. Tried - " + ','.join(tried))
        err._base_index = at.index
        return err

    def _resolve_path(self, path: str, base_dir: Path) -> Path | None:
        """Search base_dir then `paths` for the file. Tries the path
        as-given first; if the file has no extension and isn't found,
        retries with `.less` appended.

        In `jail` mode every candidate is checked against the pre-
        resolved search roots; matches that escape the jail are
        silently skipped, so the lookup behaves as if they didn't exist
        on disk. `_handle_import` then surfaces a `FileError`/
        `SecurityError` based on whether the unresolved path was
        absolute or relative — preserving less.js's `(optional)`
        semantics while still blocking the read.
        """
        candidates = [path]
        if not _has_known_extension(path):
            candidates.append(path + '.less')
        for cand in candidates:
            p = Path(cand)
            if p.is_absolute():
                if p.is_file() and self.jail_allows(p):
                    return p
                continue
            local = (base_dir / cand).resolve()
            if local.is_file() and self.jail_allows(local):
                return local
            for search_dir in self.paths:
                trial = (search_dir / cand).resolve()
                if trial.is_file() and self.jail_allows(trial):
                    return trial
        return None

    def jail_allows(self, resolved: Path) -> bool:
        """True when `resolved` is permitted by the active `file_io`
        policy. `allow` accepts every path; `deny` rejects every read
        (caller raises before invoking this); `jail` requires the
        resolved path to live under one of the pre-computed roots.

        Public so `data-uri()` (which resolves files outside the
        `@import` path) enforces the identical boundary instead of a
        second copy that could drift.
        """
        if self.file_io != 'jail':
            return True
        for root in self._jail_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _check_io_allowed(self, *, path: str, at: AtRule) -> None:
        """Raise `SecurityError` when the active `file_io` policy
        forbids any read for `path`. `deny` mode rejects unconditionally;
        `jail` and `allow` defer to `_resolve_path`'s jail filter and
        only surface here for absolute paths (which jail rejects up
        front so the diagnostic is precise instead of `FileError`).
        """
        if self.file_io == 'deny':
            err = SecurityError(f"file_io='deny' refuses @import {path!r}")
            err._base_index = at.index
            raise err
        if self.file_io == 'jail' and _is_absolute_filesystem_path(path):
            err = SecurityError(f"file_io='jail' refuses absolute @import {path!r}")
            err._base_index = at.index
            raise err
