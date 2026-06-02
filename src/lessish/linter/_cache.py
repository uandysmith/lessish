"""Optional content-hashed lint cache.

Speeds up CI runs over big projects: a file whose content hash matches
a previous entry skips parsing + rule scanning. Cache entries are
keyed by `sha256(source_text + config_signature + lessish_version)`.

Off by default; opt in via `--cache <dir>` (or pass `cache_dir=` to
`lint_cli` programmatically).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import SourceLocation
from ._config import LinterConfig
from ._findings import Finding, Fix


@dataclass
class LintCache:
    cache_dir: Path

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def key(self, source: str, config: LinterConfig) -> str:
        h = hashlib.sha256()
        h.update(__version__.encode())
        h.update(b'\x00')
        h.update(_config_signature(config).encode())
        h.update(b'\x00')
        h.update(source.encode('utf-8'))
        return h.hexdigest()

    def get(self, key: str) -> list[Finding] | None:
        path = self.cache_dir / f'{key}.json'
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return [_finding_from_dict(d) for d in data]

    def put(self, key: str, findings: list[Finding]) -> None:
        path = self.cache_dir / f'{key}.json'
        payload = [_finding_to_dict(f) for f in findings]
        path.write_text(json.dumps(payload), encoding='utf-8')


def _config_signature(config: LinterConfig) -> str:
    return json.dumps(
        {
            'enabled': sorted(config.enabled) if config.enabled is not None else None,
            'disabled': sorted(config.disabled),
            'severity_overrides': dict(sorted(config.severity_overrides.items())),
            'rule_options': {k: dict(sorted(v.items())) for k, v in sorted(config.rule_options.items())},
        },
        sort_keys=True,
    )


def _finding_to_dict(f: Finding) -> dict[str, Any]:
    return {
        'rule_id': f.rule_id,
        'severity': f.severity,
        'message': f.message,
        'loc': {
            'filename': f.location.filename,
            'line': f.location.line,
            'column': f.location.column,
            'index': f.location.index,
        },
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


def _finding_from_dict(d: dict[str, Any]) -> Finding:
    loc = SourceLocation(**d['loc'])
    fix = None if d.get('fix') is None else Fix(**d['fix'])
    return Finding(
        rule_id=d['rule_id'],
        severity=d['severity'],
        message=d['message'],
        location=loc,
        span=tuple(d['span']),
        fix=fix,
    )
