# Linter & formatter · Programmatic API

Everything the CLI does is available from Python under `lessish.linter`.

## Check

`LessLinter().check(source, filename=…)` returns a list of `Finding`s.

```python
from lessish.linter import LessLinter

linter = LessLinter()
findings = linter.check('.a { color: #FFFFFF; }', filename='styles.less')

rule_ids = [f.rule_id for f in findings]
assert 'hex-short' in rule_ids
assert 'hex-case' in rule_ids

for f in findings:
    line = f'{f.location}: {f.severity} [{f.rule_id}] {f.message}'
    assert isinstance(line, str)
```

## Fix

`fix` returns the rewritten source. `FixOptions(safe_only=True)` (the
default) applies Tier 0 + Tier 1; `safe_only=False` also applies Tier 2.

```python
from lessish.linter import LessLinter, FixOptions

fixed = LessLinter().fix(
    '.a { color: #FFFFFF; }',
    filename='styles.less',
    fix_options=FixOptions(safe_only=True),
)
# => '.a {\n  color: #fff;\n}\n'
```

## Constructor options

```python
# docs: skip
from lessish.linter import LessLinter, LinterConfig

LessLinter(
    config=LinterConfig(disabled={'magic-number'}),
    respect_inline=False,    # kill-switch for inline directives
    full=True,               # enable eval-augmented rules
    project_index=index,     # cross-file mode (see below)
    rule_timeout_seconds=30.0,
)
```

## Config

`LinterConfig` mirrors the TOML config from
[02-configuration.md](02-configuration.md):

```python
from lessish.linter import LessLinter, LinterConfig

cfg = LinterConfig(
    enabled={'hex-short', 'hex-case'},          # or None for all rules
    disabled={'no-multiple-blank-lines'},
    severity_overrides={'hex-short': 'error'},
    rule_options={'deep-nesting': {'max-depth': 6}},
)

# Only the two enabled rules can fire now.
findings = LessLinter(config=cfg).check('.a { color: #FFFFFF; }', filename='s.less')
assert {f.rule_id for f in findings} <= {'hex-short', 'hex-case'}

# A severity override took effect.
assert all(f.severity == 'error' for f in findings if f.rule_id == 'hex-short')
```

Load config from disk — `load_from_pyproject` walks up for
`pyproject.toml` / `lessish.toml`; `load_from_path` reads one explicitly:

```python
# docs: skip
from pathlib import Path
from lessish.linter import load_from_pyproject, load_from_path

cfg = load_from_pyproject(Path.cwd())
cfg = load_from_path(Path('custom.toml'))
```

## Cross-file index

`walk_project` collects the `.less` files under a root; `build_index` turns
them into a `CrossFileIndex` you hand to the linter for `--project`-style
cross-file awareness.

```python
import tempfile
from pathlib import Path
from lessish.linter import LessLinter, build_index, walk_project

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / 'theme.less').write_text('@brand: #4a90d9;\n', encoding='utf-8')
    (root / 'use.less').write_text('.a { color: @brand; }\n', encoding='utf-8')

    files = walk_project(root)
    assert len(files) == 2

    index = build_index(files)
    linter = LessLinter(project_index=index)
    findings = linter.check('@brand: #4a90d9;', filename='theme.less')
    # @brand is used in use.less, so unused-variable does not fire.
    assert 'unused-variable' not in {f.rule_id for f in findings}
```

## Cache

`LintCache` keys findings by `sha256(content + config + version)`:

```python
import tempfile
from pathlib import Path
from lessish.linter import LessLinter, LinterConfig, LintCache

with tempfile.TemporaryDirectory() as d:
    cache = LintCache(cache_dir=Path(d))
    cfg = LinterConfig()
    source = '.a { color: #FFFFFF; }'

    key = cache.key(source, cfg)
    assert cache.get(key) is None          # cold

    findings = LessLinter(config=cfg).check(source, filename='s.less')
    cache.put(key, findings)
    assert cache.get(key) is not None       # warm
```

## Dataclasses

```python
# docs: skip
@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    severity: str            # 'error' | 'warning' | 'info'
    message: str
    location: SourceLocation # filename / line / column / index
    span: tuple[int, int]    # (start_offset, end_offset)
    fix: Fix | None

@dataclass(frozen=True, slots=True)
class Fix:
    replacement: str
    safety: str              # 'safe' | 'risky'
    description: str

@dataclass(frozen=True, slots=True)
class FixOptions:
    safe_only: bool = True
    max_passes: int = 6
```

Prev: [← Rules](03-rules.md) · Back to [index](../index.md)
