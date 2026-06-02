"""Command-line interface for lessish.

`main()` parses a subcommand (`compile`, `lint`, `version`) and dispatches
to the matching `_<name>.run(args)` handler. argparse subparsers carry
their own flags; nothing here is global beyond `--help` / `--version`.

Subcommand modules expose `register(subparsers)` and `run(args) -> int`.
Adding a new subcommand is local: drop a `_foo.py`, import + register it
in `_build_parser`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .. import __version__
from . import _compile, _format, _lint


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, 'handler', None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='lessish',
        description='Pure-Python Less → CSS compiler and linter.',
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'lessish {__version__}',
    )

    subparsers = parser.add_subparsers(dest='command', metavar='<command>')

    _compile.register(subparsers)
    _lint.register(subparsers)
    _format.register(subparsers)
    _register_version(subparsers)

    return parser


def _register_version(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser('version', help='print the lessish version and exit')
    p.set_defaults(handler=_version_run)


def _version_run(_args: argparse.Namespace) -> int:
    print(f'lessish {__version__}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
