# Linter & formatter · Overview

This is lessish's **own** tooling — it has no `less.js` equivalent. A Less
linter and a code formatter, both pure Python, both shipped in the same
wheel as the compiler. No Node, no npm.

```console
$ lessish format styles.less   # canonical multi-line layout, in place
$ lessish lint   styles.less   # diagnostics; exit 1 on findings  # docs: skip
$ lessish lint --fix styles.less  # apply safe autofixes  # docs: skip
```

**45 rules across four safety tiers.** The cache is on by default, so
re-runs on unchanged files are typically **5–9× faster**. Everything is
configurable from `pyproject.toml` — see [02-configuration.md](02-configuration.md).

## A first run

Take this `styles.less`:

```less
@brand: #4a90d9;

.button {
  color: @brand;
  border: 1px solid #FFFFFF;
  margin: 0px;
  &:hover { color: darken(@brand, 10%); }
}
```

Linting it surfaces a handful of findings and exits non-zero:

```console
$ lessish lint styles.less # exit: 1
styles.less:5:21: warning [hex-short] `#FFFFFF` can be shortened to `#fff`
styles.less:5:21: warning [hex-case] `#FFFFFF` should be lowercase
styles.less:6:11: warning [zero-unit] `0px` simplifies to `0`
styles.less:7:12: warning [block-opening-brace-newline-after] block content should start on a new line
styles.less:7:40: warning [closing-brace-newline-before] `}` should be on its own line
styles.less:3:1: info [decls-before-rulesets] declarations should come before nested rulesets
```

Each line is `file:line:column: severity [rule-id] message`.

## Output formats

`--format` picks the reporter. `text` (above) is the default; `json` is for
tooling and carries the autofix payload; `github` emits GitHub Actions
annotations.

The `json` reporter prints an array of finding objects — each with `rule`,
`severity`, `message`, `file`, `line`, `column`, the `span` byte offsets,
and a `fix` (or `null` when the rule has no autofix). Pulling out just the
first finding to see the shape:

```console
$ lessish lint --format json styles.less | python -c "import sys, json; print(json.dumps(json.load(sys.stdin)[0], indent=2))"
{
  "rule": "hex-short",
  "severity": "warning",
  "message": "`#FFFFFF` can be shortened to `#fff`",
  "file": "styles.less",
  "line": 5,
  "column": 21,
  "span": [
    65,
    72
  ],
  "fix": {
    "replacement": "#fff",
    "safety": "safe",
    "description": "shorten '#FFFFFF' to '#fff'"
  }
}
```

The `github` reporter emits one annotation per finding (warnings as
`::warning`, info as `::notice`):

```console
$ lessish lint --format github styles.less # exit: 1
::warning file=styles.less,line=5,col=21::[hex-short] `#FFFFFF` can be shortened to `#fff`
::warning file=styles.less,line=5,col=21::[hex-case] `#FFFFFF` should be lowercase
::warning file=styles.less,line=6,col=11::[zero-unit] `0px` simplifies to `0`
::warning file=styles.less,line=7,col=12::[block-opening-brace-newline-after] block content should start on a new line
::warning file=styles.less,line=7,col=40::[closing-brace-newline-before] `}` should be on its own line
::notice file=styles.less,line=3,col=1::[decls-before-rulesets] declarations should come before nested rulesets
```

## Fix, don't just report

`--fix` applies the **safe** autofixes (Tier 0 + Tier 1) in place;
`--unsafe-fix` also applies the risky ones (Tier 2). After a `--fix` pass,
the findings above are resolved, so the command exits `0`:

```console
$ lessish lint --fix styles.less
$ lessish lint styles.less
```

(The first command rewrote the file; the second now finds nothing and prints
nothing.)

For a pure-formatting pass with the strictest safety guarantee, use
`lessish format` — see [06… in the CLI reference](../cli.md#format) and the
fix-tier table in [03-rules.md](03-rules.md).

Next: [Configuration →](02-configuration.md)
