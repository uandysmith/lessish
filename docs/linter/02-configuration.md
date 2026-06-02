# Linter & formatter · Configuration

lessish walks up from each input file's directory looking for a config file:
`pyproject.toml` (reads `[tool.lessish.lint]`) or `lessish.toml` (reads a
top-level `[lint]` table).

## `pyproject.toml`

```toml
[tool.lessish.lint]
enabled = ['*']                          # or an explicit list of rule IDs
disabled = ['no-multiple-blank-lines', 'magic-number']

[tool.lessish.lint.severity]
hex-short = 'error'
deep-nesting = 'info'

[tool.lessish.lint.rules.deep-nesting]
max-depth = 5

[tool.lessish.lint.rules.no-tabs]
width = 4
```

## `lessish.toml`

Same shape, without the `tool.lessish` prefix; the lint config lives under
`[lint]`.

```toml
[lint]
disabled = ['no-multiple-blank-lines']

[lint.severity]
hex-short = 'error'

[lint.rules.deep-nesting]
max-depth = 5
```

Per-rule options go under `[tool.lessish.lint.rules.<rule-id>]` (or
`[lint.rules.<rule-id>]`). Every configurable knob is listed in
[03-rules.md](03-rules.md).

```toml
[tool.lessish.lint.rules.quote-pref]
enabled = true
prefer = "double"
```

## CLI overrides

`--enable` / `--disable` take comma-separated rule IDs and are repeatable.
They override the config file for that run. For example, silencing one rule:

```console
$ lessish lint --disable space-after-colon messy.less # exit: 1
messy.less:1:3: warning [space-before-lbrace] missing space before `{`
messy.less:1:4: warning [block-opening-brace-newline-after] block content should start on a new line
messy.less:1:13: warning [semicolon-required] missing `;` at end of declaration
```

(Without `--disable`, the same file also reports `space-after-colon` at
`1:10`.) Point at a non-default config with `--config PATH`.

## Precedence

Highest wins:

1. **Inline directives** (`/* lessish-disable … */`, `/* lessish-enable … */`).
2. CLI flags (`--enable`, `--disable`).
3. Project config file.
4. Built-in defaults.

`--no-inline-config` is the kill-switch — it ignores every inline directive.

## Inline directives

Two verbs — `disable` and `enable` — with block scope or single-line scope.

| Directive | Scope |
|---|---|
| `/* lessish-disable */` | block: every rule OFF |
| `/* lessish-disable hex-short, hex-case */` | block: those rules OFF |
| `/* lessish-enable */` | block: clear all overrides and force-ON |
| `/* lessish-enable hex-short */` | block: force `hex-short` ON |
| `/* lessish-disable-line [rule] */` | this line only |
| `/* lessish-disable-next-line [rule] */` | the following source line |
| `/* lessish-enable-line [rule] */` | this line only, force-ON |
| `/* lessish-enable-next-line [rule] */` | the next line, force-ON |
| `// lessish-disable [rule]` | `//` line comments work too |

```less
/* lessish-disable hex-short */
.b { color: #FFFFFF; }       /* hex-short suppressed here */
/* lessish-enable hex-short */

/* lessish-enable unused-variable */
@should-be-used: red;        /* finding emitted even if config disables it */

.a { color: #FFFFFF; /* lessish-disable-line hex-short */ }
```

### Directives survive formatting

`lessish format` (and `lessish lint --fix`) preserve inline directives. A
line-scoped directive stays on the line it governs even when the formatter
reflows a single-line block onto multiple lines, so the suppression keeps
working. Given `directive.less`:

```less
.a { color: #FFFFFF; /* lessish-disable-line hex-short */ }
```

```console
$ lessish format directive.less
reformatted directive.less
$ cat directive.less
.a {
  color: #FFFFFF; /* lessish-disable-line hex-short */
}
```

The `hex-short` directive rides along on the declaration's line; `hex-short`
stays suppressed after formatting.

Prev: [← Overview](01-overview.md) · Next: [Rules →](03-rules.md)
