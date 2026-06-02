"""Linter configuration: defaults, overrides, and TOML loading.

Precedence (highest wins): CLI flags → inline directives → project
config file → built-in defaults. Inline directives are handled in the
engine; the dataclass here represents the merged "project + CLI" view.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LinterConfig:
    """Merged config used by the engine.

    `enabled` / `disabled` are sets of rule IDs (no globbing yet — M2).
    `severity_overrides` maps rule id → 'error' | 'warning' | 'info'.
    `rule_options` maps rule id → dict of rule-specific settings.
    """

    enabled: set[str] | None = None
    disabled: set[str] = field(default_factory=set)
    severity_overrides: dict[str, str] = field(default_factory=dict)
    rule_options: dict[str, dict[str, Any]] = field(default_factory=dict)

    def rule_active(self, rule_id: str) -> bool:
        if rule_id in self.disabled:
            return False
        if self.enabled is not None and rule_id not in self.enabled:
            return False
        return True

    def severity_for(self, rule_id: str, default: str) -> str:
        return self.severity_overrides.get(rule_id, default)

    def options_for(self, rule_id: str) -> dict[str, Any]:
        return self.rule_options.get(rule_id, {})


def load_from_pyproject(start_dir: Path) -> LinterConfig:
    """Walk upwards from `start_dir` looking for a `pyproject.toml`
    with a `[tool.lessish.lint]` section, or a `lessish.toml` with a
    top-level `[lint]` table. Returns a default `LinterConfig` if
    nothing found.

    The shape mirrors `[tool.lessish.lint]` ↔ `[lint]`: the project-
    local file's top-level keys map to compile options, with `[lint]`
    nested for the linter — same structure stripped of the `tool.lessish`
    prefix.
    """
    for parent in [start_dir, *start_dir.parents]:
        py = parent / 'pyproject.toml'
        if py.is_file():
            data = _load_toml(py)
            section = data.get('tool', {}).get('lessish', {}).get('lint')
            if section is not None:
                return _from_section(section)
        local = parent / 'lessish.toml'
        if local.is_file():
            data = _load_toml(local)
            section = data.get('lint')
            if section is not None:
                return _from_section(section)
    return LinterConfig()


def load_from_path(path: Path) -> LinterConfig:
    data = _load_toml(path)
    if path.name == 'pyproject.toml':
        section = data.get('tool', {}).get('lessish', {}).get('lint', {})
    elif 'lint' in data:
        section = data['lint']
    else:
        section = data
    return _from_section(section)


def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, 'rb') as f:
        return tomllib.load(f)


def _from_section(section: dict[str, Any]) -> LinterConfig:
    cfg = LinterConfig()
    enabled = section.get('enabled')
    if isinstance(enabled, list):
        # `['*']` (the documented default) means "no explicit filter".
        if enabled != ['*']:
            cfg.enabled = {str(x) for x in enabled}
    disabled = section.get('disabled', [])
    if isinstance(disabled, list):
        cfg.disabled = {str(x) for x in disabled}
    sev = section.get('severity', {})
    if isinstance(sev, dict):
        cfg.severity_overrides = {str(k): str(v) for k, v in sev.items()}
    rules = section.get('rules', {})
    if isinstance(rules, dict):
        cfg.rule_options = {str(k): dict(v) for k, v in rules.items() if isinstance(v, dict)}
    return cfg
