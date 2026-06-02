"""Output formatters for `lessish lint` findings."""

from __future__ import annotations

import json
from collections.abc import Iterable

from ._findings import Finding


def format_text(findings: Iterable[Finding]) -> str:
    lines = []
    for f in findings:
        loc = f.location
        lines.append(f'{loc.filename}:{loc.line}:{loc.column}: {f.severity} [{f.rule_id}] {f.message}')
    return '\n'.join(lines) + ('\n' if lines else '')


def format_json(findings: Iterable[Finding]) -> str:
    out = []
    for f in findings:
        loc = f.location
        out.append(
            {
                'rule': f.rule_id,
                'severity': f.severity,
                'message': f.message,
                'file': loc.filename,
                'line': loc.line,
                'column': loc.column,
                'span': list(f.span),
                'fix': (
                    None
                    if f.fix is None
                    else {
                        'replacement': f.fix.replacement,
                        'safety': f.fix.safety,
                        'description': f.fix.description,
                    }
                ),
            }
        )
    return json.dumps(out, indent=2) + '\n'


def format_github(findings: Iterable[Finding]) -> str:
    """GitHub Actions annotation syntax — `::warning file=…,line=…::msg`.

    `info` severity maps to `notice`; `error` and `warning` keep their
    names (those are the only three commands GH recognises).
    """
    lines = []
    for f in findings:
        loc = f.location
        cmd = {'error': 'error', 'warning': 'warning', 'info': 'notice'}.get(f.severity, 'notice')
        lines.append(f'::{cmd} file={loc.filename},line={loc.line},col={loc.column}::[{f.rule_id}] {f.message}')
    return '\n'.join(lines) + ('\n' if lines else '')


FORMATTERS = {
    'text': format_text,
    'json': format_json,
    'github': format_github,
}
