# lessish

A pure-Python compiler for the [Less CSS](https://lesscss.org)
language. **Zero runtime dependencies** — stdlib only. Oracle-tested
against the upstream `less.js` v4.6.3 corpus, byte-for-byte parity on
the in-scope fixture suite.

## Install

```bash
pip install lessish
```

## Why

LESS is wonderful. Since it landed, writing plain CSS feels like a step backwards — nesting alone is worth the price of admission. But for a Python project, dragging in a Node toolchain just to compile a few hundred lines of CSS is overkill.

For years [lesscpy](https://github.com/lesscpy/lesscpy) had us covered. It just worked: you stayed in Python, you wrote LESS, you got CSS. Beautiful.

Then LLMs happened.

Suddenly the budget for CSS-per-feature went up. Where a project used to ship a couple of plain layouts, an LLM can now comfortably produce a polished UI — mixins, nested selectors, color math, the works. Turns out lesscpy only covers a chunk of LESS, and every time the model hits a feature lesscpy stumbles on, it burns tokens contorting around the limitation.

OK, so: study the LESS surface, build test datasets, sketch out something resembling a spec, and write a compiler that follows `less.js` as closely as practical.

**lessish isn't a successor to lesscpy.** It's not trying to be an independent LESS implementation — its goal is to mirror upstream `less.js` faithfully, so the LLM doesn't have to know it isn't running on Node.

Speed? It's about as slow as lesscpy — up to ~3× slower than `less.js`. Turns out that's a non-issue: even big real-world projects compile in well under a second.

A second goal was a **clean embedding API**. lesscpy made this painful; lessish does it properly — you can wire LESS compilation into a Python tool without contortions.

One thing cut on purpose: **JavaScript execution and plugins**. JS-in-CSS is an obvious RCE story and stays out. Python plugins could in principle exist, but who's going to write LESS plugins in Python? Nobody. So: skipped.

## Status

Active development (pre-1.0). Byte-for-byte parity with `lessc` on
every in-scope upstream fixture and across 11 curated real-world LESS
codebases (Bootstrap v3, UIkit, AdminLTE, Font Awesome v4/v5, WeUI,
intro.js, Hover.css, Milligram, normalize.less, toastr).

Conformance lives in a [separate repo](https://github.com/uandysmith/less-conformance) and is not pulled into this
package.

## Use

```python
from lessish import Lessish

ls = Lessish()
css = ls.compile('@c: red; .a { color: @c; }')
# → '.a {\n  color: red;\n}\n'
```

Lower-level entry points (lexer / parser only) live on the same instance:

```python
source = '@c: red; .a { color: @c; }'
tokens = ls.tokenize(source)   # TokenStream (iterable / len / index of Token)
ast    = ls.parse(source)      # Ruleset (structural, values still raw text)
ast    = ls.parse(tokens)      # skip the lex pass — reuse the stream above
```

Useful for tooling — editors, linters, language servers — that wants
a position-aware tokenization or AST without paying for full compile.
`parse()` accepts the `TokenStream` returned by `tokenize()` so a
language server that's already tokenised for syntax highlighting
doesn't pay the regex cost twice.

## API

`Lessish` is the only top-level class. `compile()` runs the full
pipeline; each stage is also a public method you can call alone or chain
— useful for tooling that needs just one:

| Method | Stage | Input → Output |
|---|---|---|
| `tokenize(source)` | lex | str → `TokenStream` |
| `parse(source)` | parse | str → `Ruleset` (AST, declaration values still raw text) |
| `evaluate(root, src=…)` | eval | `Ruleset` → `Ruleset` (`@import`, variables, mixins, `:extend`, at-rule bubbling) |
| `emit(root, src=…)` | emit | `Ruleset` → CSS string (or `SourceMapResult`) |
| `compile(source)` | parse + eval + emit | str → CSS string |
| `compile_with_source_map(source)` | + source map | str → `SourceMapResult` |

Each stage is overridable on a subclass (`compile()` honours the
override), and per-compile state is never held on the instance, so
independent or parallel compiles never race. Subclassing, the
`copy_input` reuse flag, and thread-safety are covered in the
[embedding guide](docs/guide/01-quickstart.md).

### Options

Constructor stores defaults; per-call kwargs override (names mirror
`less.js`, snake-cased). The full set — `compress`, `math`, source maps,
the `file_io` sandbox, the DoS budgets (mixin count/output/input size,
plus an evaluation-time wall-clock deadline), and the opt-in hardening switches
(`disabled_functions` / `RESTRICTED_FUNCTIONS`, `neutralize_escape`) for
untrusted input — lives in the [options guide](docs/guide/02-options.md).

A plain `Lessish()` mirrors `less.js` and is **not** the configuration for
untrusted input: its `file_io='jail'` still reads files under the working
directory, escapes are emitted raw, and `replace`/`range` are enabled. For
Less you do not control, use `Lessish.hardened()` — it bundles every safety
switch in one constructor (`file_io='deny'`, restricted functions, escape
neutralisation, output/input ceilings, and an evaluation time budget) and
locks those security options against per-call weakening:

```python
from lessish import Lessish

ls = Lessish.hardened()
css = ls.compile('@c: red; .a { color: @c; }')
# => '.a {\n  color: red;\n}\n'
```

### Errors

All exceptions inherit from `LessError` and carry a `.location`
(`SourceLocation(filename, line, column, index)`) plus a `.snippet`
with a caret indicator:

- `ParseError` — lexer / parser refused the input.
- `EvalError` (and subclasses) — `UndefinedNameError`,
  `OperationError`, `TypeMismatchError`, `ArgumentError`.
- `UnsupportedFeatureError` — see [Out of scope](#out-of-scope).
- `FileError` — `@import` target not found.
- `SecurityError` — `file_io` policy refused a read (see the [options guide](docs/guide/02-options.md)).

## Linter & formatter

lessish ships with a Less linter and code formatter, available as
`lessish lint` and `lessish format` on the CLI plus a programmatic
API:

```bash
lessish format src/**/*.less     # canonical multi-line layout in place
lessish lint   src/**/*.less     # diagnostics; exit 1 on findings
lessish lint --fix src/**/*.less # apply safe autofixes
```

45 rules across four safety tiers. Cache is on by default — re-runs
on unchanged files are typically **5–9× faster**. Configurable via
`[tool.lessish.lint]` in `pyproject.toml`.

Full rule catalog, configuration options, inline directives, and
programmatic API: see the [linter guide](docs/linter/01-overview.md).

## Documentation

Full docs live in [docs/](docs/index.md). They cover the parts specific
to lessish — they don't re-document the Less language itself (for that,
[lesscss.org](https://lesscss.org) is the reference; lessish mirrors
`less.js`). Three pillars:

- **[Embedding guide](docs/guide/01-quickstart.md)** — the `Lessish` API,
  options, source maps, the addressable pipeline, and errors.
- **[Linter & formatter](docs/linter/01-overview.md)** — rules,
  configuration, inline directives, and the programmatic API.
- **[CLI reference](docs/cli.md)** — `compile`, `lint`, `format`.

Plus the [design rationale](docs/philosophy.md). Every example in the docs
is executable and checked by [`docs/check_examples.py`](docs/check_examples.py).

## Out of scope

Two things lessish deliberately does not run:

- **JavaScript execution** — `` `…` `` backtick expressions evaluate
  arbitrary JS in the less.js implementation. lessish doesn't run JS.
- **`@plugin "…"` Node plugins** — they execute arbitrary code at
  compile time. Same reason. A Python-plugin shim was considered and
  rejected — nobody writes LESS plugins in Python.

**These constructs lex and parse fine** — the lexer emits the
appropriate token kinds, and the parser builds an AST without
complaining. Rejection happens at `evaluate()` time as a
`UnsupportedFeatureError` (subclass of `LessError`). This means
`tokenize()` and `parse()` stay usable on input that contains them —
handy for editors, syntax highlighters, and linters that want a full
token / AST view without enforcing the runtime restrictions.

Also out: remote `@import url(http…)` (SSRF-shaped; same
`UnsupportedFeatureError` at eval time) and `dumpLineNumbers`
debug-comment output (a legacy `lessc`-CLI feature, superseded by
source maps — never emitted).

Anything else from the upstream `less.js` surface that lessish
doesn't yet handle is a bug — please file an issue with a minimal
repro.

## License

Apache-2.0. See [LICENSE](LICENSE).
