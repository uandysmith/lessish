# lessish documentation

A pure-Python compiler for the [Less CSS](https://lesscss.org) language,
plus a Less linter and formatter. **Zero runtime dependencies** — stdlib
only. lessish mirrors upstream [`less.js`](https://github.com/less/less.js)
faithfully: byte-for-byte parity on the in-scope fixture suite and across
11 curated real-world Less codebases.

## Scope of these docs

This documentation does **not** teach the Less language. Less is defined
by `less.js`, and the canonical reference is **[lesscss.org](https://lesscss.org)** —
variables, mixins, nesting, operations, and the built-in function library
all live there. lessish is a faithful re-implementation, so anything that
holds for `less.js` holds here; when in doubt about *what a Less construct
means*, the upstream docs are the source of truth.

What these pages cover is the part that is **specific to lessish**:

| Pillar | What it is | Start here |
|---|---|---|
| **Embedding** | Driving the compiler from Python — the `Lessish` API, options, source maps, the addressable pipeline, and errors. | [guide/01-quickstart.md](guide/01-quickstart.md) |
| **Linter & formatter** | lessish's own Less linter and code formatter — not part of `less.js`. Rules, configuration, inline directives, and the programmatic API. | [linter/01-overview.md](linter/01-overview.md) |
| **CLI** | The `lessish` command — `compile`, `lint`, `format`. | [cli.md](cli.md) |

Design rationale and what lessish deliberately leaves out: [philosophy.md](philosophy.md).

## Install

```console
$ pip install lessish   # docs: skip
```

The wheel ships the `lessish` console script and the importable `lessish`
package. No Node, no npm.

## Quickstart — embedding

```python
from lessish import Lessish

ls = Lessish()
css = ls.compile('@c: red; .a { color: @c; }')
# => '.a {\n  color: red;\n}\n'
```

## Quickstart — CLI

Given a file `styles.less`:

```less
@brand: #4a90d9;

.button {
  color: @brand;
  border: 1px solid #FFFFFF;
  margin: 0px;
  &:hover { color: darken(@brand, 10%); }
}
```

Compile it to CSS:

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

The library defaults to the secure `file_io='jail'`. The CLI is quiet on
stderr because, being an explicit invocation, it opts into the
less.js-compatible `file_io='allow'` and silences the
`LessishSecurityWarning` that mode raises. That warning is reserved for
*programmatic* use that loosens the sandbox to `'allow'` — see
[guide/02-options.md](guide/02-options.md#file_io-sandbox).

## Quickstart — linting

Against the same `styles.less`:

```console
$ lessish lint styles.less # exit: 1
styles.less:5:21: warning [hex-short] `#FFFFFF` can be shortened to `#fff`
styles.less:5:21: warning [hex-case] `#FFFFFF` should be lowercase
styles.less:6:11: warning [zero-unit] `0px` simplifies to `0`
styles.less:7:12: warning [block-opening-brace-newline-after] block content should start on a new line
styles.less:7:40: warning [closing-brace-newline-before] `}` should be on its own line
styles.less:3:1: info [decls-before-rulesets] declarations should come before nested rulesets
```

`lessish lint` exits `1` when any warning or error remains. See
[linter/01-overview.md](linter/01-overview.md) for `--fix`, `--format json`,
and configuration.

## Learning path

1. **[philosophy.md](philosophy.md)** — why lessish exists and how it relates to `less.js`.
2. **Embedding** — [quickstart](guide/01-quickstart.md) → [options](guide/02-options.md) → [source maps](guide/03-source-maps.md) → [pipeline](guide/04-pipeline.md) → [errors](guide/05-errors.md).
3. **Linter** — [overview](linter/01-overview.md) → [configuration](linter/02-configuration.md) → [rules](linter/03-rules.md) → [API](linter/04-api.md).
4. **[CLI reference](cli.md)** — every subcommand and flag.

## Verifying the examples

Every fenced example in this documentation is executable and checked by a
script. If an example here ever drifts from shipped behaviour, the script
goes red:

```console
$ python docs/check_examples.py # docs: skip
```

See [check_examples.py](check_examples.py) for the conventions (`# =>` arrow
assertions in `python` blocks, `$`-prefixed `console` sessions).

## License

Apache-2.0. See [LICENSE](../LICENSE).
