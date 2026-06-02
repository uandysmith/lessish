"""`lessish compile` defaults loaded from pyproject.toml.

Highest-precedence wins: CLI flags > config file > built-in defaults
(`Lessish._PIPELINE_DEFAULTS`).

Lookup: walk up from the start dir looking for `pyproject.toml` with a
`[tool.lessish]` section, or `lessish.toml` as a project-local
alternative. The two files mirror each other:

    pyproject.toml         lessish.toml
    ---------------        -----------
    [tool.lessish]    ==   <top-level keys>
    [tool.lessish.lint] == [lint]

The compile config reads the `[tool.lessish]` / top-level table; the
`[lint]` sub-table is owned by `lessish.linter._config`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CompileConfig:
    """Compile defaults pulled from `[tool.lessish]`.

    Field names follow the Python kwargs lessish.compile() takes; TOML
    keys use dashes (`strict-units` → `strict_units`) for readability.
    None values mean "not set in config — fall through to CLI / built-in
    defaults"; this lets us cleanly distinguish "user set false" from
    "user didn't set anything".
    """

    compress: bool | None = None
    math: str | None = None
    strict_units: bool | None = None
    paths: tuple[str, ...] = ()
    rewrite_urls: str | None = None
    rootpath: str | None = None
    url_args: str | None = None
    banner: str | None = None
    process_imports: bool | None = None
    global_vars: dict[str, str] = field(default_factory=dict)
    modify_vars: dict[str, str] = field(default_factory=dict)


def load_from_pyproject(start_dir: Path) -> CompileConfig:
    for parent in [start_dir, *start_dir.parents]:
        py = parent / 'pyproject.toml'
        if py.is_file():
            data = _load_toml(py)
            section = data.get('tool', {}).get('lessish')
            if section is not None:
                return _from_section(section)
        local = parent / 'lessish.toml'
        if local.is_file():
            return _from_section(_load_toml(local))
    return CompileConfig()


def load_from_path(path: Path) -> CompileConfig:
    data = _load_toml(path)
    if path.name == 'pyproject.toml':
        section = data.get('tool', {}).get('lessish', {})
    else:
        section = data
    return _from_section(section)


def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, 'rb') as f:
        return tomllib.load(f)


def _from_section(section: dict[str, Any]) -> CompileConfig:
    cfg = CompileConfig()

    if isinstance(section.get('compress'), bool):
        cfg.compress = section['compress']
    math = section.get('math')
    if isinstance(math, (str, int)):
        cfg.math = str(math)
    if isinstance(section.get('strict-units'), bool):
        cfg.strict_units = section['strict-units']
    paths = section.get('paths')
    if isinstance(paths, list):
        cfg.paths = tuple(str(p) for p in paths)
    rw = section.get('rewrite-urls')
    if isinstance(rw, str):
        cfg.rewrite_urls = rw
    rp = section.get('rootpath')
    if isinstance(rp, str):
        cfg.rootpath = rp
    ua = section.get('url-args')
    if isinstance(ua, str):
        cfg.url_args = ua
    banner = section.get('banner')
    if isinstance(banner, str):
        cfg.banner = banner
    if isinstance(section.get('process-imports'), bool):
        cfg.process_imports = section['process-imports']

    gv = section.get('global-vars', {})
    if isinstance(gv, dict):
        cfg.global_vars = {str(k): str(v) for k, v in gv.items()}
    mv = section.get('modify-vars', {})
    if isinstance(mv, dict):
        cfg.modify_vars = {str(k): str(v) for k, v in mv.items()}
    return cfg
