# Embedding · Options

Every option can be set on the constructor (becomes the default for all
compiles) or passed per-call (overrides the default for that call). Names
mirror `less.js`, snake-cased. What each option *does* to the Less is
defined by `less.js` — see [lesscss.org](https://lesscss.org/usage/); this
page is about the Python surface.

## The full set

| Option | Type | Notes |
|---|---|---|
| `filename` | `str` | Logical path; base for `@import` resolution and source-map `sources[]`. |
| `paths` | `list[str]` | Extra `@import` search dirs. |
| `process_imports` | `bool` | Splice imports before eval. |
| `global_vars` | `dict[str, str]` | Prepended as `@name: value;`; user code can override them. |
| `modify_vars` | `dict[str, str]` | Appended; overrides user code. |
| `banner` | `str` | Verbatim string prefix on the emitted CSS. |
| `compress` | `bool` | Single-line, no-whitespace output. |
| `rewrite_urls` | `'all' \| 'local' \| 'off'` | `url(…)` rewriting in imported files. |
| `rootpath` | `str` | Prefix prepended to every `url(…)` at emit. |
| `url_args` | `str` | Query string appended to every `url(…)`. Data URIs exempt. |
| `strict_units` | `bool` | Compound-unit arithmetic raises. |
| `math` | `'always' \| 'parens-division' \| 'parens'` | Plus the numeric aliases `less.js` accepts. |
| `source_map` | `bool \| dict` | `True` or an options dict enables map emission. See [03-source-maps.md](03-source-maps.md). |
| `strict_imports` | `bool` | Accepted but a no-op — mirrors `less.js`'s own deprecated handling. |
| `file_io` | `'allow' \| 'jail' \| 'deny'` | Filesystem-read sandbox. Default `'jail'`. See [below](#file_io-sandbox). |
| `mixin_depth_limit` / `mixin_total_limit` | `int \| None` | DoS backstops on mixin invocation. `None` → built-in defaults. See [DoS limits](#dos-limits). |
| `interp_expansion_limit` | `int` | Max bytes one `@{var}` interpolation may expand to (billion-laughs guard). Default 1 MB. |
| `replace_input_limit` | `int` | Max pattern/subject length for the `replace()` function (ReDoS bound). Default 100 000. |
| `range_max_elements` | `int` | Max element count `range()` may generate (memory-blowup guard). Default 1 000 000. |
| `max_eval_seconds` | `float \| None` | Wall-clock evaluation deadline, checked **throughout evaluation** (the cycle trampoline, the per-declaration path, and mixin invocation). `None` → off. Catches exponential mixin expansion *and* a large flat mixin-free workload the count limit lets run for seconds; it does **not** interrupt a single uninterruptible operation (e.g. a catastrophic `replace()` regex) — disable `replace` for that. See [DoS limits](#dos-limits). |
| `max_output_size` | `int \| None` | Max emitted CSS size in bytes. `None` → off. Bounds output amplification (a small mixin body splatted to megabytes). See [DoS limits](#dos-limits). |
| `max_input_size` | `int \| None` | Max **cumulative** parser input in characters — entry source plus every imported file — checked before lexing. `None` → off. Bounds the one phase the wall-clock deadline doesn't: parsing is O(n) and runs ahead of evaluation. See [DoS limits](#dos-limits). |
| `disabled_functions` | `Iterable[str] \| None` | Built-in functions to refuse at call time. `None` → none disabled. A name that isn't a real built-in raises `ValueError` (catches typos that would silently disable nothing). See [Hardened mode](#hardened-mode). |
| `neutralize_escape` | `bool` | CSS-escape every `</` in the final output so compiled CSS can't break out of an inlined HTML `<style>` — covers values, selectors, property names, comments, and at-rule preludes. Default `False` (less.js emits raw). See [Hardened mode](#hardened-mode). |

## Injecting variables

`global_vars` is prepended to the source — user code may override it.
`modify_vars` is appended — it overrides user code. This mirrors `less.js`'s
`globalVars` / `modifyVars`.

```python
from lessish import Lessish

ls = Lessish()

# global_vars seeds a default the source can override.
seeded = ls.compile('.a { color: @c; }', global_vars={'c': 'blue'})
# => '.a {\n  color: blue;\n}\n'

overridden = ls.compile('@c: green; .a { color: @c; }', global_vars={'c': 'blue'})
# => '.a {\n  color: green;\n}\n'

# modify_vars always wins.
forced = ls.compile('@c: green; .a { color: @c; }', modify_vars={'c': 'blue'})
# => '.a {\n  color: blue;\n}\n'
```

## Math modes

`math` controls when arithmetic evaluates. The default is
`'parens-division'`: `+`, `-`, `*` always evaluate, but division only inside
parentheses.

```python
from lessish import Lessish

ls = Lessish()

# Division left alone in the default mode.
ls.compile('.a { w: 6px / 2; }')
# => '.a {\n  w: 6px / 2;\n}\n'

# 'always' evaluates everything.
ls.compile('.a { w: 6px / 2; }', math='always')
# => '.a {\n  w: 3px;\n}\n'
```

## Strict units

With `strict_units=True`, arithmetic that produces an incoherent compound
unit raises instead of silently producing it. The error is an
`OperationError` (a subclass of `LessError`):

```python
from lessish import Lessish, OperationError

ls = Lessish()

# Without strict units, px*px just yields a number with a px unit.
ls.compile('.a { w: 2px * 2px; }')
# => '.a {\n  w: 4px;\n}\n'

try:
    ls.compile('.a { w: 2px * 2px; }', strict_units=True)
    raised = False
except OperationError:
    raised = True
assert raised
```

## Banner

`banner` is emitted verbatim ahead of the CSS — handy for license headers.

```python
from lessish import Lessish

Lessish().compile('.a { color: red; }', banner='/*! v1 */\n')
# => '/*! v1 */\n.a {\n  color: red;\n}\n'
```

## `file_io` sandbox

Less source can read local files — `@import`, `@import (inline)`,
`data-uri()`, `image-size()`, and friends. `file_io` chooses the policy:

| Value | Behaviour |
|---|---|
| `'jail'` (default) | Confines reads to the **jail root** (see below) and `paths`; absolute paths and `..`-escapes raise `SecurityError`. |
| `'allow'` | Any file the process can read. Mirrors `less.js`. Emits a `LessishSecurityWarning` on **every** compile that selects it (so the risk stays visible). Silence it deliberately with `warnings.filterwarnings('ignore', category=LessishSecurityWarning)`. |
| `'deny'` | Blocks every read sink outright. The only value that allows **no** disk read. |

### `'jail'` is not "no disk access" — know your jail root

`'jail'` means *reads are confined to the jail root*, not *reads are
blocked*. The root is derived from `filename`:

- **`filename` unset** (the default `'<input>'`, or a bare name like
  `'foo.less'`) → root is the **current working directory**. Everything
  under the process CWD is readable.
- **`filename` is a path with a directory** (`'/srv/app/styles/main.less'`)
  → root is that directory (`/srv/app/styles`).
- `paths=[...]` adds further readable roots.

So `'jail'` is the right default for **scripts** run from a stylesheet
directory — the root is just your assets. It is a trap for **applications
and services** whose process CWD is the project root: untrusted Less can
then `data-uri("config/secrets.env")` and exfiltrate any file under that
root into the compiled CSS (it still cannot escape via `..` or absolute
paths). For an application compiling Less you do not control:

- pass an explicit `filename` so the root is the stylesheet directory, **and**
- use `file_io='deny'` (or `Lessish.hardened()`) so no disk read happens at all.

Opt into `'allow'` only when the Less is trusted and needs
less.js-compatible filesystem access (the **CLI** does this — an explicit,
interactive invocation — and suppresses the warning itself).

```python
from lessish import Lessish, SecurityError

ls = Lessish()  # jail by default
src = '.a { background: data-uri("/etc/hostname"); }'

# Even the default jail blocks the absolute read; 'deny' blocks every sink.
try:
    ls.compile(src, file_io='deny')
    blocked = False
except SecurityError:
    blocked = True
assert blocked
```

`SecurityError` is a subclass of `LessError`, so a single `except LessError`
catches sandbox violations alongside parse and eval errors — see
[05-errors.md](05-errors.md).

## DoS limits

When compiling untrusted Less, several pathological inputs could otherwise
burn unbounded CPU or memory. lessish bounds each; all are tunable per
compile (or per instance):

| Option | Guards against | Default |
|---|---|---|
| `mixin_total_limit` / `mixin_depth_limit` | Runaway / exponential mixin expansion (`None` → built-in defaults: 50 000 / 1000). The count keeps ~10× headroom over the heaviest real-world stylesheet; pair it with `max_eval_seconds` for untrusted input (the count alone can let an exponential fan-out run a few seconds first). | `None` |
| `max_eval_seconds` | A wall-clock deadline checked **throughout evaluation** (the cycle trampoline, the per-declaration path, and mixin invocation) — interrupts both the exponential mixin case and a large flat mixin-free workload the count limit only catches after seconds. The one thing it can't interrupt is a single uninterruptible call (notably a catastrophic-backtracking `replace()` regex, which runs in C-level `re`). For untrusted regex, disable `replace` (it's in `RESTRICTED_FUNCTIONS`). Off by default; the count limit is the always-on guard. | `None` |
| `max_output_size` | Output amplification. A mixin body invoked enough times can emit megabytes while staying under the invocation count; this caps the emitted CSS in bytes and raises `EvalError` once crossed. | `None` |
| `max_input_size` | Unbounded parser input. The wall-clock deadline covers evaluation, but parsing runs first and is O(n) — a giant entry source or an `@import` fan-out of many files would lex/parse unbounded ahead of it. This caps the **cumulative** input (entry + every imported file) in characters, checked before each lex; an imported file whose on-disk byte size already exceeds the remaining budget is rejected without being read. Raises `ParseError` (anchored at the offending `@import`). It bounds *input only* — a tiny source can still expand via mixins, which is what the eval-time limits above catch. | `None` |
| `interp_expansion_limit` | Billion-laughs string interpolation (`@a: "@{b}@{b}"; …`). A single `@{var}` expansion past this many bytes raises `EvalError`. | `1_000_000` |
| `replace_input_limit` | `replace()` regex on oversized input. Patterns with nested unbounded quantifiers (`(a+)+`) **or** overlapping alternatives under a quantifier (`(a\|a)*`) are also rejected outright — Python's `re` has no backtracking budget. | `100_000` |
| `range_max_elements` | `range()` memory blow-up: `range(1e9)` would allocate a billion nodes, and `step <= 0` would never terminate. Both trip this cap. | `1_000_000` |

```python
from lessish import Lessish

# Tighten the budgets for a hostile, low-trust workload.
ls = Lessish(
    file_io='deny',
    mixin_total_limit=10_000,
    max_eval_seconds=5.0,
    max_output_size=10_000_000,
    interp_expansion_limit=256_000,
    replace_input_limit=10_000,
    range_max_elements=10_000,
)
```

## Hardened mode

The limits above are *best-effort* bounds. One built-in resists them:
`replace()` runs a user-supplied regex through Python's `re`, and
catastrophic backtracking happens in C — it **cannot** be interrupted or
time-bounded in pure stdlib. The pattern guard rejects nested unbounded
quantifiers (`(a+)+`) and the common overlapping-alternation forms
(`(a|a)*`, `(a|ab)+`), but ReDoS detection is undecidable in general — a
constructed pattern can still slip past a structural check.

When you need a hard guarantee rather than mitigation, disable the
unbounded-risk functions outright with `disabled_functions`:

```python
from lessish import Lessish, RESTRICTED_FUNCTIONS

ls = Lessish(file_io='deny', disabled_functions=RESTRICTED_FUNCTIONS)
# A disabled function now raises UnsupportedFeatureError at compile:
#   ls.compile('.a { x: replace("y", "z", "w"); }')  -> UnsupportedFeatureError
```

- **`RESTRICTED_FUNCTIONS`** is `{'replace', 'range'}` — the built-ins whose
  DoS risk isn't fully boundable by validation. You can pass your own set
  instead (names are case-insensitive). A name that isn't a real built-in
  raises `ValueError` rather than silently disabling nothing.
- This is a **deliberate degradation of Less support**: source that calls a
  disabled function fails with `UnsupportedFeatureError` rather than
  compiling. The trade is "less LESS coverage" for "no unbounded-risk code
  path runs".
- It is **off by default** — out of the box you get the full less.js
  function set. Enable it explicitly for untrusted input.
- File-reading functions (`data-uri`, `image-size`, `image-width`,
  `image-height`) are **not** in `RESTRICTED_FUNCTIONS`; they are governed
  by [`file_io`](#file_io-sandbox) — set `file_io='deny'` to block them.

### One preset: `Lessish.hardened()`

Untrusted-input safety needs several independent switches set together —
`file_io='deny'`, `disabled_functions`, `neutralize_escape`, and the
wall-clock / output budgets. `Lessish.hardened()` bundles all of them so you
can't forget one:

```python
from lessish import Lessish

ls = Lessish.hardened()
# Equivalent to:
#   Lessish(file_io='deny', disabled_functions=RESTRICTED_FUNCTIONS,
#           neutralize_escape=True, max_eval_seconds=10.0,
#           max_output_size=50_000_000, max_input_size=10_000_000)
css = ls.compile('@c: red; .a { color: @c; }')
# => '.a {\n  color: red;\n}\n'
```

Override any preset default with a keyword *here*: `Lessish.hardened(compress=True,
max_eval_seconds=2.0)`.

The six security-critical options — `file_io`, `disabled_functions`,
`neutralize_escape`, `max_eval_seconds`, `max_output_size`, `max_input_size` —
are then **locked**.
A per-call `compile(**overrides)` may make any of them *stricter*, but an
override that would *weaken* the sandbox raises `SecurityError` instead of
silently widening it. This stops a config-derived options dict splatted into
`compile()` from quietly defeating the preset:

```python
from lessish import Lessish, SecurityError

ls = Lessish.hardened()
# Tightening is fine — a shorter budget than the preset's 10s:
ls.compile('.a { color: red; }', max_eval_seconds=1.0)
# Weakening is refused:
try:
    ls.compile('.a { color: red; }', file_io='allow')
    raised = False
except SecurityError:
    raised = True
assert raised
```

If you genuinely need a looser policy, build a separate instance with that
policy rather than overriding per call.

`neutralize_escape` closes an XSS shape: untrusted Less can place a literal
`</style>` into the output — through `e()` / `~"…"`, a plain quoted string,
selector or property-name interpolation, or a comment. If you inline the
compiled CSS into an HTML `<style>` block, any of those breaks out. With
`neutralize_escape=True` every `</` in the final output is CSS-escaped to
`\00003c/` — still valid CSS (a browser decodes `\00003c` back to `<` inside
strings, so `content: "</p>"` renders unchanged), but no longer a sequence an
HTML parser treats as a closing tag. The escape is applied at the emit sink,
so it covers all output paths at once and leaves legitimate `<` / `>` — media
query range operators (`width < 600px`), `<!--`, lone `<` — untouched. Default
`False` (less.js emits raw).

## `paths` and `@import`

`@import` resolution is filesystem-backed, so it's easiest to show on the
CLI — see [`--paths` in the CLI reference](../cli.md#compile). In Python,
pass `paths=['./mixins/', './vendor/']` and a `filename` so relative imports
resolve from the right base directory.

Prev: [← Quickstart](01-quickstart.md) · Next: [Source maps →](03-source-maps.md)
