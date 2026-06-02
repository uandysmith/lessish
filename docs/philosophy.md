# Philosophy

## Mirror `less.js`, don't reinvent Less

lessish is **not** an independent Less implementation and does not try to
be one. Its single design goal is to mirror upstream
[`less.js`](https://github.com/less/less.js) faithfully — so a tool (or an
LLM) that knows Less can compile it in a pure-Python process without ever
learning that it isn't running on Node.

That is why **this documentation does not describe the Less language.** The
language is whatever `less.js` does, and the canonical reference is
[lesscss.org](https://lesscss.org). If you want to know what `@var`,
`.mixin()`, `:extend`, guards, `~"escaping"`, or `darken()` mean, read the
upstream docs — lessish follows them. Conformance is measured against the
upstream fixture corpus and a set of real-world codebases (Bootstrap v3,
UIkit, AdminLTE, Font Awesome v4/v5, WeUI, intro.js, Hover.css, Milligram,
normalize.less, toastr), with byte-for-byte parity the bar.

What lessish *adds on top* — and what these docs do cover — is:

- a **clean Python embedding API** (`lessish.Lessish`), and
- a **Less linter and formatter** that have no `less.js` equivalent.

## Why it exists

Less is wonderful, and for a Python project, dragging in a Node toolchain
just to compile a few hundred lines of CSS is overkill. For years
[lesscpy](https://github.com/lesscpy/lesscpy) covered that — you stayed in
Python, wrote Less, got CSS.

Then LLMs raised the CSS budget per feature. A model can comfortably produce
a polished UI — mixins, nested selectors, colour math, the works — but
lesscpy only covers a chunk of Less, and every time a model hits a feature
lesscpy stumbles on, it burns tokens working around the gap. lessish's
answer is to follow `less.js` as closely as practical so the gap closes.

Speed is roughly on par with lesscpy — up to ~3× slower than `less.js`,
which turns out to be a non-issue: even big real-world projects compile in
well under a second.

## Out of scope

Two `less.js` features lessish deliberately does **not** run:

- **JavaScript execution** — `` `…` `` backtick expressions evaluate
  arbitrary JS in `less.js`. That is an obvious RCE story; lessish does not
  run JS.
- **`@plugin "…"` Node plugins** — they execute arbitrary code at compile
  time. Same reason. A Python-plugin shim was considered and rejected —
  nobody writes Less plugins in Python.

Also out: remote `@import url(http…)` (SSRF-shaped) and `dumpLineNumbers`
debug-comment output (a legacy `lessc` feature superseded by source maps).

**These constructs still lex and parse cleanly.** The lexer emits the right
token kinds and the parser builds an AST without complaining — rejection
happens at `evaluate()` time as an `UnsupportedFeatureError` (a subclass of
`LessError`). That keeps `tokenize()` and `parse()` usable for editors,
syntax highlighters, and linters that want a full token / AST view without
enforcing the runtime restrictions. See
[guide/04-pipeline.md](guide/04-pipeline.md).

Anything else from the upstream `less.js` surface that lessish doesn't yet
handle is a **bug** — please file an issue with a minimal repro.

## Security posture

Less source can read local files (`@import`, `@import (inline)`,
`data-uri()`, `image-size()`…). `less.js` allows this unconditionally.
lessish is **secure by default** — `file_io='jail'` confines reads to the
source's directory and the configured `paths`, so a server compiling
LLM-generated or user-submitted Less cannot be coerced into leaking
arbitrary files. Opt into the less.js-compatible `file_io='allow'` only for
Less you trust; `'deny'` blocks filesystem reads entirely.

Because `'allow'` is a deliberate loosening, the *programmatic* API emits
`LessishSecurityWarning` on **every** compile that selects it — the risk
stays visible at each call site, not just the first. The **CLI** is an
explicit, interactive invocation: it reads with `file_io='allow'` and
silences that warning itself. See
[guide/02-options.md](guide/02-options.md#file_io-sandbox).

DoS surfaces in untrusted Less — runaway mixin expansion, billion-laughs
`@{var}` interpolation, and `replace()` regex backtracking — are bounded by
default and tunable via the `mixin_total_limit`, `interp_expansion_limit`,
and `replace_input_limit` options.
