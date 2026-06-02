"""`lessish compile` subcommand — thin shim over `Lessish().compile()`.

Reads input from a file (or `-` for stdin), writes CSS to `--out` (or
stdout). Compile defaults come from `[tool.lessish]` in pyproject.toml
(or `lessish.toml`); CLI flags override per-key.

Argparse defaults are `None` for every overridable flag so we can tell
"user didn't pass the flag" (→ use config) from "user passed false" (→
overrides config). Boolean flags use `store_true` but get re-initialised
post-parse to keep the semantics.
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

from .. import Lessish, LessishSecurityWarning
from ._compile_config import CompileConfig, load_from_path, load_from_pyproject
from ._shared import (
    EXIT_OK,
    exit_code_for,
    parse_key_value,
    print_error,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        'compile',
        help='compile a Less source file to CSS',
        description='Compile a Less source file (or stdin) to CSS.',
    )
    p.add_argument('input', help="Less source file (use '-' for stdin)")
    p.add_argument(
        '-o',
        '--out',
        metavar='PATH',
        help='write CSS to PATH (default: stdout)',
    )
    p.add_argument('--config', metavar='PATH', help='compile config file (default: pyproject.toml)')

    # `default=None` on every overridable flag lets us merge with the
    # config dataclass. The "no" flags use `action='store_const'` so
    # the absence-vs-presence distinction survives.
    p.add_argument('--compress', action='store_const', const=True, default=None, help='produce minified output')
    p.add_argument('--source-map', action='store_true', help='emit a Source Maps v3 annotation')
    p.add_argument(
        '--source-map-out',
        metavar='PATH',
        help='write the source-map JSON to PATH (implies --source-map)',
    )
    p.add_argument(
        '--source-map-url',
        metavar='URL',
        help='URL embedded in the sourceMappingURL annotation',
    )
    p.add_argument(
        '--paths',
        action='append',
        metavar='DIR',
        default=[],
        help='extra @import search directory (repeatable; extends config)',
    )
    p.add_argument(
        '--math',
        choices=('always', 'parens-division', 'parens'),
        default=None,
        help='arithmetic mode (default: parens-division)',
    )
    p.add_argument(
        '--strict-units',
        action='store_const',
        const=True,
        default=None,
        help='raise on compound-unit arithmetic',
    )
    p.add_argument(
        '--global-var',
        action='append',
        metavar='NAME=VALUE',
        default=[],
        help='inject @NAME: VALUE before user code (repeatable; merges with config)',
    )
    p.add_argument(
        '--modify-var',
        action='append',
        metavar='NAME=VALUE',
        default=[],
        help='inject @NAME: VALUE after user code, overriding it (repeatable; merges with config)',
    )
    p.add_argument(
        '--no-process-imports',
        action='store_const',
        const=True,
        default=None,
        help='do not splice imported @imports (less-shape only)',
    )
    p.add_argument(
        '--rewrite-urls',
        choices=('all', 'local', 'off'),
        default=None,
        help='rewrite url(...) values in imported files (default: off)',
    )
    p.add_argument('--rootpath', metavar='PREFIX', default=None, help='prefix prepended to every url(...)')
    p.add_argument('--url-args', metavar='SUFFIX', default=None, help='query-string suffix appended to every url(...)')
    p.add_argument('--banner', metavar='TEXT', default=None, help='verbatim prefix for the emitted CSS')

    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        source, filename = _read_input(args.input)
    except OSError as err:
        print_error(err)
        return exit_code_for(err)

    config = _load_config(args)
    options = _build_options(args, config, filename)
    source_map_opt = _resolve_source_map(args)

    lessish = Lessish()
    try:
        # The CLI runs under `file_io='allow'` (see `_build_options`) — an
        # explicit, interactive invocation reading the local filesystem,
        # so the `LessishSecurityWarning` that `'allow'` emits for every
        # compile is noise here. Suppress that one category locally; any
        # other warning still surfaces.
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=LessishSecurityWarning)
            if source_map_opt is not None and args.source_map_out:
                options['source_map'] = source_map_opt
                result = lessish.compile_with_source_map(source, **options)
                _write_output(args.out, result.css)
                Path(args.source_map_out).write_text(result.map_json, encoding='utf-8')
            elif source_map_opt is not None:
                options['source_map'] = source_map_opt
                css = lessish.compile(source, **options)
                _write_output(args.out, css)
            else:
                css = lessish.compile(source, **options)
                _write_output(args.out, css)
    except OSError as err:
        print_error(err)
        return exit_code_for(err)
    except Exception as err:  # noqa: BLE001
        print_error(err)
        return exit_code_for(err)

    return EXIT_OK


def _load_config(args: argparse.Namespace) -> CompileConfig:
    if args.config:
        return load_from_path(Path(args.config))
    if args.input and args.input != '-':
        start = Path(args.input).resolve().parent
    else:
        start = Path.cwd()
    return load_from_pyproject(start)


def _build_options(args: argparse.Namespace, config: CompileConfig, filename: str) -> dict[str, object]:
    """Merge config + CLI args into the kwarg dict passed to compile().

    Per-key precedence: CLI > config > pipeline default (which lessish
    fills in when a key is absent). Lists merge: config first, then CLI
    appended. Var-dicts merge per-key with CLI overriding.
    """
    cli_global = _collect_vars(args.global_var, flag='--global-var')
    cli_modify = _collect_vars(args.modify_var, flag='--modify-var')
    merged_global = {**config.global_vars, **cli_global}
    merged_modify = {**config.modify_vars, **cli_modify}

    options: dict[str, object] = {
        'filename': filename,
        'paths': tuple(config.paths) + tuple(args.paths),
        # The library default is the secure `file_io='jail'`, but the CLI
        # stays less.js-compatible: an explicit, interactive invocation
        # reads the local filesystem with `file_io='allow'`. The
        # `LessishSecurityWarning` that `'allow'` raises on every compile
        # is suppressed at the call site in `run()` (not here) — for
        # programmatic embedders that warning stays loud.
        'file_io': 'allow',
    }

    _put(options, 'compress', args.compress, config.compress)
    _put(options, 'math', args.math, config.math)
    _put(options, 'strict_units', args.strict_units, config.strict_units)
    _put(options, 'rewrite_urls', args.rewrite_urls, config.rewrite_urls)
    _put(options, 'rootpath', args.rootpath, config.rootpath)
    _put(options, 'url_args', args.url_args, config.url_args)
    _put(options, 'banner', args.banner, config.banner)

    # `--no-process-imports` (truthy when set) maps to `process_imports=False`.
    if args.no_process_imports is True:
        options['process_imports'] = False
    elif config.process_imports is not None:
        options['process_imports'] = config.process_imports

    if merged_global:
        options['global_vars'] = merged_global
    if merged_modify:
        options['modify_vars'] = merged_modify
    return options


def _put(options: dict[str, object], key: str, cli_value: object, cfg_value: object) -> None:
    if cli_value is not None:
        options[key] = cli_value
    elif cfg_value is not None:
        options[key] = cfg_value


def _read_input(path: str) -> tuple[str, str]:
    if path == '-':
        return sys.stdin.read(), '<stdin>'
    return Path(path).read_text(encoding='utf-8'), path


def _write_output(out_path: str | None, css: str) -> None:
    if out_path is None or out_path == '-':
        sys.stdout.write(css)
        return
    Path(out_path).write_text(css, encoding='utf-8')


def _collect_vars(raw_list: list[str], *, flag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in raw_list:
        name, value = parse_key_value(raw, flag=flag)
        out[name] = value
    return out


def _resolve_source_map(args: argparse.Namespace) -> bool | dict[str, object] | None:
    """Translate the four source-map CLI flags into the kwarg
    `compile()` takes: None / True / dict.

    `--source-map-url` is meaningful even without `--source-map`
    because less.js accepts it as the trigger; we follow the same
    convention.
    """
    if not (args.source_map or args.source_map_out or args.source_map_url):
        return None
    if args.source_map_url:
        return {'sourceMapURL': args.source_map_url}
    return True
