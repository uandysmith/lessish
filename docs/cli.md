# CLI reference

The wheel installs a single `lessish` console script with three
subcommands — `compile`, `lint`, `format` — plus `version`.

```console
$ lessish --version
lessish 0.1.0
```

```console
$ lessish version
lessish 0.1.0
```

> **No security warning on the CLI.** The library defaults to the secure
> `file_io='jail'`, but the CLI stays less.js-compatible: an explicit,
> interactive invocation reads the filesystem with `file_io='allow'` and
> suppresses the `LessishSecurityWarning` that `'allow'` raises on every
> compile. That warning is reserved for *programmatic* use, where untrusted
> Less might reach the compiler — see
> [guide/02-options.md](guide/02-options.md#file_io-sandbox). There is no
> `--file-io` flag; lock down reads in Python instead.

---

## `compile`

```
lessish compile [flags] INPUT
```

`INPUT` is a Less file, or `-` for stdin. Output goes to stdout unless `-o`
is given. Compile defaults come from `[tool.lessish]` in `pyproject.toml`
(or `lessish.toml`); flags override per key.

```console
$ lessish compile styles.less
.button {
  color: #4a90d9;
  border: 1px solid #FFFFFF;
  margin: 0px;
}
.button:hover {
  color: #2a76c6;
}
```

From stdin:

```console
$ printf '.x { c: 1 + 2; }' | lessish compile -
.x {
  c: 3;
}
```

To a file:

```console
$ lessish compile styles.less -o out.css
```

### Flags

| Flag | Effect |
|---|---|
| `-o, --out PATH` | Write CSS to `PATH` (default: stdout). |
| `--config PATH` | Compile-config file (default: `pyproject.toml`). |
| `--compress` | Minified output. |
| `--source-map` | Emit a Source Maps v3 annotation. |
| `--source-map-out PATH` | Write the map JSON to `PATH` (implies `--source-map`). |
| `--source-map-url URL` | URL embedded in the `sourceMappingURL` annotation. |
| `--paths DIR` | Extra `@import` search dir (repeatable; extends config). |
| `--math {always,parens-division,parens}` | Arithmetic mode (default: `parens-division`). |
| `--strict-units` | Raise on compound-unit arithmetic. |
| `--global-var NAME=VALUE` | Inject `@NAME: VALUE` *before* user code (repeatable). |
| `--modify-var NAME=VALUE` | Inject `@NAME: VALUE` *after* user code, overriding it (repeatable). |
| `--no-process-imports` | Don't splice `@import`s (less-shape only). |
| `--rewrite-urls {all,local,off}` | Rewrite `url(...)` in imported files (default: `off`). |
| `--rootpath PREFIX` | Prefix prepended to every `url(...)`. |
| `--url-args SUFFIX` | Query-string suffix appended to every `url(...)`. |
| `--banner TEXT` | Verbatim prefix for the emitted CSS. |

The option semantics match the Python API one-for-one — see
[guide/02-options.md](guide/02-options.md).

### Minify

```console
$ lessish compile styles.less --compress
.button{color:#4a90d9;border:1px solid #FFFFFF;margin:0px}.button:hover{color:#2a76c6}
```

### Inject a variable

```console
$ printf '.a { color: @c; }' | lessish compile - --global-var c=blue
.a {
  color: blue;
}
```

### Source maps

`--source-map-out` writes both the CSS (with a trailing
`sourceMappingURL` annotation) and the separate map JSON:

```console
$ lessish compile styles.less --source-map-out styles.css.map -o styles.css
$ tail -c 38 styles.css
/*# sourceMappingURL=styles.css.map */
```

See [guide/03-source-maps.md](guide/03-source-maps.md) for the map contents.

---

## `lint`

```
lessish lint [flags] FILE [FILE ...]
```

Runs the linter, prints diagnostics, and exits `1` if any
warning/error remains. Full walkthrough: [linter/01-overview.md](linter/01-overview.md).

```console
$ lessish lint styles.less # exit: 1
styles.less:5:21: warning [hex-short] `#FFFFFF` can be shortened to `#fff`
styles.less:5:21: warning [hex-case] `#FFFFFF` should be lowercase
styles.less:6:11: warning [zero-unit] `0px` simplifies to `0`
styles.less:7:12: warning [block-opening-brace-newline-after] block content should start on a new line
styles.less:7:40: warning [closing-brace-newline-before] `}` should be on its own line
styles.less:3:1: info [decls-before-rulesets] declarations should come before nested rulesets
```

### Flags

| Flag | Effect |
|---|---|
| `--fix` | Apply **safe** autofixes (Tier 0 + Tier 1) in place. |
| `--unsafe-fix` | Also apply **risky** autofixes (Tier 2). |
| `--full` | Run eval-augmented rules (slower; runs the evaluator). |
| `--format {text,json,github}` | Output format. `text` is the default. |
| `--config PATH` | Load config from `PATH` instead of the default lookup. |
| `--enable RULES` | Comma-separated rule IDs to force-enable (repeatable). |
| `--disable RULES` | Comma-separated rule IDs to force-disable (repeatable). |
| `--no-inline-config` | Ignore all `/* lessish-disable */` directives. |
| `--cache DIR` | Override cache location (default: auto-discovered). |
| `--no-cache` | Disable result caching (cache is on by default). |
| `--project DIR` | Cross-file mode. |
| `--telemetry-out PATH` | Write per-rule hit counts (JSON). |

Rule catalog, tiers, cache, and `--project` details:
[linter/03-rules.md](linter/03-rules.md). Configuration and inline
directives: [linter/02-configuration.md](linter/02-configuration.md).

### Autofix

```console
$ lessish lint --fix styles.less
$ lessish lint styles.less
```

(The first pass rewrites the file in place; the second finds nothing and
exits `0`.)

---

## `format`

```
lessish format [--check] FILE [FILE ...]
```

A pure formatter — runs only Tier 0 rules, rewrites in place, no diagnostic
output. Same engine as `lessish lint --fix`, but formatting-only and with
`blank-line-before-block` on for the canonical layout.

```console
$ lessish format messy.less
reformatted messy.less
$ cat messy.less
.a {
  color: red;
}
```

`--check` reports what *would* change and exits `1` if anything would,
without writing:

```console
$ lessish format --check messy.less # exit: 1
would reformat messy.less
```

---

## Exit codes

| Exit | Meaning |
|---|---|
| `0` | Success. For `lint`: no findings, or only `info`-level findings. |
| `1` | `lint`: a `warning`/`error` remained after `--fix`. `format --check`: a file would change. |
| `2` | Bad invocation, or an optional component isn't available. |
| `3` | I/O error (missing file, can't write output). |

Back to [index](index.md).
