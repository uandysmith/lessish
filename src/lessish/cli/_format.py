"""`lessish format` subcommand — Prettier-style formatter.

Runs the linter with only Tier-0 rules enabled, applies the safe fixes
in place. `--check` mode reports which files would change (without
writing) and exits non-zero if any changes are needed.

This is the same engine `lessish lint --fix` uses, just framed as a
formatter: no diagnostic output, no Tier-1/2/3 rules.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..linter import FixOptions, LessLinter, LinterConfig
from ..linter.rules import TIER_0_RULES


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        'format',
        help='format Less source files (Tier-0 rules only)',
        description=(
            "Apply lessish's Tier-0 (formatting/whitespace) rules to one "
            'or more files. Same engine as `lessish lint --fix`, but with '
            'only the formatting rules enabled and no diagnostic output.'
        ),
    )
    p.add_argument('files', nargs='+', metavar='FILE', help='Less source files')
    p.add_argument(
        '--check',
        action='store_true',
        help='exit 1 if any file would change; do not write',
    )
    p.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    tier0_ids = {cls.id for cls in TIER_0_RULES}
    # Some Tier-0 rules ship off-by-default (they're opt-in for lint
    # because users may prefer compact one-liners). For `format` we
    # turn them on — the whole point of the subcommand is to produce
    # a canonical multi-line layout.
    rule_options = {
        'blank-line-before-block': {'enabled': True},
    }
    config = LinterConfig(enabled=tier0_ids, rule_options=rule_options)
    linter = LessLinter(config=config)

    any_changed = False
    has_io_error = False

    for path_str in args.files:
        path = Path(path_str)
        try:
            text = path.read_text(encoding='utf-8')
        except OSError as e:
            print(f'lessish: error: {e}', file=sys.stderr)
            has_io_error = True
            continue

        # max_passes=8 gives the reorder rule enough cascade depth to
        # converge after the line-breaking rules settle the layout.
        # Real-world inputs converge in 3–5 passes; 8 is the safety net.
        fixed = linter.fix(
            text,
            filename=str(path),
            fix_options=FixOptions(safe_only=True, max_passes=8),
        )
        if fixed == text:
            continue
        any_changed = True
        if args.check:
            print(f'would reformat {path}')
        else:
            path.write_text(fixed, encoding='utf-8')
            print(f'reformatted {path}')

    if has_io_error:
        return 3
    if args.check and any_changed:
        return 1
    return 0
