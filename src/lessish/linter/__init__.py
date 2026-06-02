"""Public surface of the lessish linter.

>>> from lessish.linter import LessLinter
>>> linter = LessLinter()
>>> findings = linter.check('.a { color: #FFFFFF; }')
>>> [(f.rule_id, f.message) for f in findings]
[('hex-short', '`#FFFFFF` can be shortened to `#fff`'), ('hex-case', '`#FFFFFF` should be lowercase')]

`lint_cli(args)` is the entry point wired into `lessish lint`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._cache import LintCache
from ._config import LinterConfig, load_from_path, load_from_pyproject
from ._engine import LessLinter
from ._findings import Finding, Fix, FixOptions
from ._formatters import FORMATTERS
from ._project import CrossFileIndex, build_index, walk_project

__all__ = [
    'LessLinter',
    'LinterConfig',
    'LintCache',
    'CrossFileIndex',
    'Finding',
    'Fix',
    'FixOptions',
    'lint_cli',
]


def lint_cli(args: argparse.Namespace) -> int:
    explicit_config: LinterConfig | None = None
    if args.config:
        explicit_config = load_from_path(Path(args.config))

    telemetry: dict[str, dict[str, int]] = {}
    caches: dict[Path, LintCache] = {}

    project_index: CrossFileIndex | None = None
    project_dir = getattr(args, 'project', None)
    if project_dir:
        files = walk_project(Path(project_dir))
        project_index = build_index(files)

    formatter = FORMATTERS[args.format]
    any_findings_remaining = False
    has_io_error = False

    for path_str in args.files:
        path = Path(path_str)
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as e:
            print(f'lessish: error: {e}', file=sys.stderr)
            has_io_error = True
            continue

        config = explicit_config or load_from_pyproject(path.resolve().parent)
        _apply_cli_overrides(config, args)

        respect_inline = not getattr(args, 'no_inline_config', False)
        full = bool(getattr(args, 'full', False))
        linter = LessLinter(
            config=config,
            respect_inline=respect_inline,
            full=full,
            project_index=project_index,
        )
        filename = str(path)

        cache = _resolve_cache(args, path, caches)
        cache_key: str | None = None
        cached: list[Finding] | None = None
        if cache is not None and not (args.fix or args.unsafe_fix):
            cache_key = cache.key(text, config)
            cached = cache.get(cache_key)

        if cached is not None:
            findings = cached
        else:
            findings = linter.check(text, filename=filename)
            if cache is not None and cache_key is not None:
                cache.put(cache_key, findings)

        if args.fix or args.unsafe_fix:
            opts = FixOptions(safe_only=not args.unsafe_fix)
            fixed = linter.fix(text, filename=filename, fix_options=opts)
            if fixed != text:
                path.write_text(fixed, encoding='utf-8')
                text = fixed
            findings = linter.check(text, filename=filename)

        if getattr(args, 'telemetry_out', None):
            for f in findings:
                bucket = telemetry.setdefault(f.rule_id, {'count': 0, 'files': 0})
                bucket['count'] += 1
            for rule_id in {f.rule_id for f in findings}:
                telemetry[rule_id]['files'] += 1

        if findings:
            output = formatter(findings)
            if output:
                sys.stdout.write(output)
            if any(f.severity in ('warning', 'error') for f in findings):
                any_findings_remaining = True

    if getattr(args, 'telemetry_out', None):
        _write_telemetry(Path(args.telemetry_out), telemetry, len(args.files))

    if has_io_error:
        return 3
    return 1 if any_findings_remaining else 0


def _resolve_cache(args: argparse.Namespace, path: Path, caches: dict[Path, LintCache]) -> LintCache | None:
    """Decide which `LintCache` (if any) to use for a given input file.

    Precedence:
    1. `--no-cache` → None.
    2. `--cache PATH` → that path (override; created if missing).
    3. Auto-discover: walk up from the file's directory looking for
       `pyproject.toml` / `lessish.toml`; cache lives in `.lessish-cache/`
       next to it.
    4. No project root → no cache (avoids polluting unrelated dirs).

    Results are memoised so multiple inputs sharing a project root share
    a single `LintCache` instance.
    """
    if getattr(args, 'no_cache', False):
        return None
    override = getattr(args, 'cache', None)
    cache_dir: Path | None
    if override:
        cache_dir = Path(override).resolve()
    else:
        cache_dir = _autodiscover_cache_dir(path.resolve().parent)
        if cache_dir is None:
            return None
    if cache_dir not in caches:
        caches[cache_dir] = LintCache(cache_dir=cache_dir)
    return caches[cache_dir]


def _autodiscover_cache_dir(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / 'pyproject.toml').is_file() or (parent / 'lessish.toml').is_file():
            return parent / '.lessish-cache'
    return None


def _write_telemetry(path: Path, counts: dict[str, dict[str, int]], total_files: int) -> None:
    import json

    sorted_items = sorted(counts.items(), key=lambda kv: (-kv[1]['count'], kv[0]))
    payload = {
        'schemaVersion': 1,
        'totalFiles': total_files,
        'rules': [{'rule': k, 'count': v['count'], 'files': v['files']} for k, v in sorted_items],
    }
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def _apply_cli_overrides(config: LinterConfig, args: argparse.Namespace) -> None:
    enabled_set: set[str] = set()
    for chunk in args.enable:
        for token in chunk.split(','):
            token = token.strip()
            if token:
                enabled_set.add(token)
    if enabled_set:
        config.enabled = enabled_set if config.enabled is None else (config.enabled | enabled_set)
    for chunk in args.disable:
        for token in chunk.split(','):
            token = token.strip()
            if token:
                config.disabled.add(token)
