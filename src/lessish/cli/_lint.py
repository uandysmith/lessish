"""`lessish lint` subcommand."""

from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        'lint',
        help='run the Less linter on one or more files',
        description='Run the Less linter on one or more files.',
    )
    p.add_argument('files', nargs='+', metavar='FILE', help='Less source files')
    p.add_argument('--fix', action='store_true', help='apply safe-tier autofixes in place')
    p.add_argument('--unsafe-fix', action='store_true', help='also apply risky-tier autofixes')
    p.add_argument(
        '--format',
        choices=('text', 'json', 'github'),
        default='text',
        help='output format (default: text)',
    )
    p.add_argument('--config', metavar='PATH', help='lint config file (default: pyproject.toml)')
    p.add_argument(
        '--enable',
        action='append',
        default=[],
        metavar='RULES',
        help='comma-separated rule IDs to force-enable (repeatable)',
    )
    p.add_argument(
        '--disable',
        action='append',
        default=[],
        metavar='RULES',
        help='comma-separated rule IDs to force-disable (repeatable)',
    )
    p.add_argument(
        '--no-inline-config',
        action='store_true',
        help='ignore /* lessish-disable */ / /* lessish-enable */ inline directives',
    )
    p.add_argument(
        '--full',
        action='store_true',
        help='enable eval-augmented rules (slower; runs the full pipeline)',
    )
    p.add_argument(
        '--cache',
        metavar='DIR',
        help=(
            'cache lint results under DIR. Default location is '
            "`.lessish-cache/` next to the project's pyproject.toml "
            '(or lessish.toml).'
        ),
    )
    p.add_argument(
        '--no-cache',
        action='store_true',
        help='disable result caching (cache is on by default)',
    )
    p.add_argument(
        '--project',
        metavar='DIR',
        help='cross-file mode: scan DIR for `.less` files to build a global reference index',
    )
    p.add_argument(
        '--telemetry-out',
        metavar='PATH',
        help='write per-rule hit counts to PATH (JSON) — opt-in only',
    )
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    try:
        from ..linter import lint_cli
    except ImportError:
        print('lessish: lint subcommand not yet available', file=sys.stderr)
        return 2
    return int(lint_cli(args))
