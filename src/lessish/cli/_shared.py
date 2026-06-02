"""Shared CLI helpers — exit codes, error formatting, key=value parsing."""

from __future__ import annotations

import sys

from ..errors import (
    EvalError,
    LessError,
    ParseError,
    UnsupportedFeatureError,
)

EXIT_OK = 0
EXIT_PARSE_ERROR = 1
EXIT_EVAL_ERROR = 2
EXIT_IO_ERROR = 3
EXIT_UNSUPPORTED = 4


def exit_code_for(err: BaseException) -> int:
    if isinstance(err, UnsupportedFeatureError):
        return EXIT_UNSUPPORTED
    if isinstance(err, ParseError):
        return EXIT_PARSE_ERROR
    if isinstance(err, EvalError):
        return EXIT_EVAL_ERROR
    if isinstance(err, OSError):
        return EXIT_IO_ERROR
    if isinstance(err, LessError):
        return EXIT_EVAL_ERROR
    return EXIT_IO_ERROR


def print_error(err: BaseException) -> None:
    if isinstance(err, LessError):
        print(f'lessish: error: {err}', file=sys.stderr)
        return
    print(f'lessish: error: {err}', file=sys.stderr)


def parse_key_value(raw: str, *, flag: str) -> tuple[str, str]:
    """Split `name=value` from a repeatable CLI flag like `--global-var`.

    The Less variable name keeps its source spelling (caller decides
    whether to prepend `@`). Missing `=` is a usage error.
    """
    if '=' not in raw:
        raise argparse_error(f'{flag} expects NAME=VALUE, got {raw!r}')
    name, _, value = raw.partition('=')
    name = name.strip()
    if not name:
        raise argparse_error(f'{flag} got empty name in {raw!r}')
    return name, value


def argparse_error(message: str) -> SystemExit:
    """Raise the same shape argparse uses for invalid arguments."""
    sys.stderr.write(f'lessish: error: {message}\n')
    return SystemExit(2)
