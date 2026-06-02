#!/usr/bin/env python3
"""Verify that every example in the lessish documentation still works.

Run it from anywhere:

    python docs/check_examples.py            # check docs/ + README.md
    python docs/check_examples.py --verbose  # print every block as it runs

What it does
------------
It walks the Markdown files under ``docs/`` (plus the package ``README.md``)
and executes the fenced code blocks, so the docs can never drift from the
shipped behaviour without this script going red.

Two kinds of blocks are checked:

``python`` blocks
    Executed as Python in a fresh namespace. ``LessishSecurityWarning`` (and
    every other warning) is silenced for the run, so example code stays clean.
    Assertions can be written two ways:

    * a normal ``assert`` statement, or
    * an *arrow comment* on the line after an assignment or a bare
      expression::

          css = ls.compile('@c: red; .a { color: @c; }')
          # => '.a {\\n  color: red;\\n}\\n'

      The ``# =>`` (``# ->`` and the unicode ``# →`` also work) line asserts
      that the value on the preceding line equals the Python literal to its
      right.

``console`` blocks
    Shell sessions. Lines starting with ``$ `` are commands; the lines that
    follow (until the next ``$`` or the end of the block) are the expected
    stdout. Commands run in a throwaway directory seeded with the fixtures in
    ``FIXTURES`` below, with ``PYTHONWARNINGS=ignore`` so the security banner
    stays out of the captured output. A trailing ``# exit: N`` on a command
    line asserts a non-zero exit code.

Opting out
----------
A block that is illustrative only (a signature sketch, a partial snippet)
is skipped if it contains the marker ``# docs: skip`` on a line of its own.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import traceback
import warnings
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Fixtures for ``console`` blocks. Every CLI example runs in a temp dir that
# starts out holding exactly these files. Keep the set small and stable —
# the expected output baked into the docs is derived from this content.
# --------------------------------------------------------------------------

FIXTURES: dict[str, str] = {
    # Compile + lint demo. Chosen so it both compiles to something tidy and
    # trips a handful of lint rules (`#FFFFFF` → hex-short/hex-case, `0px` →
    # zero-unit).
    'styles.less': textwrap.dedent(
        """\
        @brand: #4a90d9;

        .button {
          color: @brand;
          border: 1px solid #FFFFFF;
          margin: 0px;
          &:hover { color: darken(@brand, 10%); }
        }
        """
    ),
    # A deliberately un-formatted file for the `format` examples.
    'messy.less': '.a{color:red}\n',
    # A single-line block carrying an inline lint directive — used to show
    # that `lessish format` keeps the directive on the line it governs.
    'directive.less': '.a { color: #FFFFFF; /* lessish-disable-line hex-short */ }\n',
}


SKIP_MARKER = '# docs: skip'
ARROW_RE = re.compile(r'^(?P<indent>\s*)#\s*(?:=>|->|→)\s*(?P<expected>.+?)\s*$')
# A top-level assignment: ``name = value`` with spaces around ``=``. The
# spaces matter — they keep keyword arguments (``fix_options=FixOptions(…)``)
# from being mistaken for assignments when walking back from a call's closer.
ASSIGN_RE = re.compile(r'^(?P<indent>\s*)(?P<lhs>[A-Za-z_][A-Za-z0-9_.]*)\s+=\s+\S.*$')


@dataclass
class Block:
    lang: str
    code: str
    file: Path
    line: int  # 1-based line of the opening fence


@dataclass
class Result:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Markdown extraction
# --------------------------------------------------------------------------

FENCE_RE = re.compile(r'^(?P<fence>```+)(?P<info>[^\n`]*)$')


def extract_blocks(path: Path) -> list[Block]:
    blocks: list[Block] = []
    lines = path.read_text(encoding='utf-8').splitlines()
    i = 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i].rstrip())
        if not m:
            i += 1
            continue
        fence = m.group('fence')
        lang = m.group('info').strip().split()[0] if m.group('info').strip() else ''
        start = i + 1
        j = start
        while j < len(lines) and lines[j].rstrip() != fence:
            j += 1
        code = '\n'.join(lines[start:j])
        blocks.append(Block(lang=lang, code=code, file=path, line=i + 1))
        i = j + 1
    return blocks


# --------------------------------------------------------------------------
# Python block runner
# --------------------------------------------------------------------------


def _rewrite_arrows(code: str) -> str:
    """Turn ``# => <literal>`` arrow comments into real assertions.

    The arrow refers to the value produced by the previous code line: either
    the variable it assigned, or the bare expression it evaluated.
    """
    src_lines = code.splitlines()
    out: list[str] = []
    for line in src_lines:
        m = ARROW_RE.match(line)
        if not m:
            out.append(line)
            continue
        indent = m.group('indent')
        expected = m.group('expected')
        # Find the previous emitted code line (skip blanks / comments).
        prev_idx = None
        for k in range(len(out) - 1, -1, -1):
            s = out[k].strip()
            if s and not s.startswith('#'):
                prev_idx = k
                break
        if prev_idx is None:
            continue
        prev = out[prev_idx].strip()
        am = ASSIGN_RE.match(out[prev_idx])
        if am:
            target = am.group('lhs')
        elif prev[:1] in ')]}':
            # Closer of a multi-line assignment, e.g.
            #     fixed = linter.fix(
            #         source,
            #     )
            #     # => '…'
            # Walk back to the nearest assignment line and use its LHS.
            target = prev
            for k in range(prev_idx - 1, -1, -1):
                back = ASSIGN_RE.match(out[k])
                if back:
                    target = back.group('lhs')
                    break
        else:
            target = prev  # bare expression on the previous line
        out.append(
            f'{indent}assert ({target}) == ({expected}), '
            f'"example mismatch: {{!r}} != {{!r}}".format(({target}), ({expected}))'
        )
    return '\n'.join(out)


def run_python_block(block: Block, result: Result, verbose: bool, namespace: dict[str, object]) -> None:
    if SKIP_MARKER in block.code:
        result.skipped += 1
        return
    code = _rewrite_arrows(block.code)
    if verbose:
        print(f'  python  {block.file}:{block.line}')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            compiled = compile(code, f'{block.file}:{block.line}', 'exec')
            exec(compiled, namespace)  # noqa: S102 — running our own docs
        except Exception:  # noqa: BLE001
            result.failed += 1
            tb = traceback.format_exc()
            result.failures.append(f'PYTHON  {block.file}:{block.line}\n{textwrap.indent(tb, "    ")}')
            return
    result.passed += 1


# --------------------------------------------------------------------------
# Console block runner
# --------------------------------------------------------------------------

PROMPT = '$ '
EXIT_RE = re.compile(r'#\s*exit:\s*(\d+)\s*$')

# `src/` next to `docs/` — put it on the subprocess PYTHONPATH so the
# `lessish` package imports without a prior `pip install` (the doc
# examples must run straight from a checkout / in CI).
_SRC_DIR = Path(__file__).resolve().parent.parent / 'src'

# Rewrite the documented program names to the *current* interpreter so
# the examples are hermetic: they don't depend on a `lessish` console
# script being on PATH or on `python` resolving to anything in
# particular. Only a token in command position (start, or right after a
# shell operator) is touched, so a filename argument named `python`
# stays intact.
_CMD_HEAD_RE = re.compile(r'(^|[|&;]\s*)(lessish|python3|python)\b')


def _rewrite_cmd(cmd: str) -> str:
    exe = shlex.quote(sys.executable)

    def repl(m: re.Match[str]) -> str:
        prog = m.group(2)
        replacement = f'{exe} -m lessish' if prog == 'lessish' else exe
        return f'{m.group(1)}{replacement}'

    return _CMD_HEAD_RE.sub(repl, cmd)


def _parse_console(code: str) -> list[tuple[str, int, str]]:
    """Split a console block into (command, expected_exit, expected_stdout)."""
    steps: list[tuple[str, int, str]] = []
    lines = code.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith(PROMPT):
            i += 1
            continue
        cmd = line[len(PROMPT) :]
        exit_code = 0
        em = EXIT_RE.search(cmd)
        if em:
            exit_code = int(em.group(1))
            cmd = cmd[: em.start()].rstrip()
        expected: list[str] = []
        i += 1
        while i < len(lines) and not lines[i].startswith(PROMPT):
            expected.append(lines[i])
            i += 1
        steps.append((cmd, exit_code, '\n'.join(expected)))
    return steps


# Collapse a `lessish <version>` banner line to a stable token. The
# concrete version is whatever `importlib.metadata` reports: the real
# release version when run against an installed wheel (CI / conformance),
# but `0.0.0+dev` when run straight from a source checkout (the unit
# test). Pinning an exact number in the docs would also rot on every
# version bump — matching the *shape* keeps the `version` example
# meaningful (it ran, exited 0, printed a version) without that fragility.
_VERSION_LINE_RE = re.compile(r'^lessish \d+\.\d+\.\d+\S*$', re.MULTILINE)


def _normalize(text: str) -> str:
    text = _VERSION_LINE_RE.sub('lessish <version>', text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def run_console_block(block: Block, result: Result, verbose: bool) -> None:
    if SKIP_MARKER in block.code:
        result.skipped += 1
        return
    steps = _parse_console(block.code)
    if not steps:
        result.skipped += 1
        return
    env = dict(os.environ)
    env['PYTHONWARNINGS'] = 'ignore'
    # Absolute PYTHONPATH so the rewritten `python -m lessish` resolves
    # the package from the checkout (cwd below is a temp dir, so a
    # relative entry would not).
    env['PYTHONPATH'] = os.pathsep.join(p for p in (str(_SRC_DIR), env.get('PYTHONPATH', '')) if p)
    with tempfile.TemporaryDirectory(prefix='lessish-docs-') as tmp:
        workdir = Path(tmp)
        for name, content in FIXTURES.items():
            (workdir / name).write_text(content, encoding='utf-8')
        for cmd, want_exit, want_out in steps:
            if verbose:
                print(f'  console {block.file}:{block.line}  $ {cmd}')
            proc = subprocess.run(
                _rewrite_cmd(cmd),
                shell=True,  # noqa: S602 — running our own documented commands
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
            )
            if proc.returncode != want_exit:
                result.failed += 1
                result.failures.append(
                    f'CONSOLE {block.file}:{block.line}\n'
                    f'    $ {cmd}\n'
                    f'    exit {proc.returncode}, expected {want_exit}\n'
                    f'    stderr: {proc.stderr.strip()[:400]}'
                )
                return
            if want_out.strip():
                got = _normalize(proc.stdout)
                exp = _normalize(want_out)
                if got != exp:
                    result.failed += 1
                    result.failures.append(
                        f'CONSOLE {block.file}:{block.line}\n'
                        f'    $ {cmd}\n'
                        f'    --- expected ---\n{textwrap.indent(exp, "    ")}\n'
                        f'    --- got ---\n{textwrap.indent(got, "    ")}'
                    )
                    return
    result.passed += 1


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def iter_doc_files(docs_dir: Path, readme: Path) -> list[Path]:
    files = sorted(p for p in docs_dir.rglob('*.md'))
    if readme.exists():
        files.append(readme)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Verify lessish documentation examples.')
    parser.add_argument('-v', '--verbose', action='store_true', help='print every block as it runs')
    args = parser.parse_args(argv)

    docs_dir = Path(__file__).resolve().parent
    package_root = docs_dir.parent
    readme = package_root / 'README.md'

    result = Result()
    for path in iter_doc_files(docs_dir, readme):
        # Python blocks in one file share a namespace, top to bottom, so a
        # later block can build on names a earlier one defined (the way a
        # reader steps through the page). Each file starts fresh.
        namespace: dict[str, object] = {'__name__': '__doc_example__'}
        for block in extract_blocks(path):
            if block.lang == 'python':
                run_python_block(block, result, args.verbose, namespace)
            elif block.lang == 'console':
                run_console_block(block, result, args.verbose)
            # Other languages (toml, less, text, …) are documentation-only.

    print()
    print(f'examples: {result.passed} passed, {result.failed} failed, {result.skipped} skipped')
    if result.failures:
        print('\n' + '=' * 72)
        for f in result.failures:
            print(f)
            print('-' * 72)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
