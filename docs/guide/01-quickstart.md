# Embedding · Quickstart

> This guide covers driving lessish **from Python**. It assumes you already
> know Less — if you don't, [lesscss.org](https://lesscss.org) is the
> reference. lessish compiles the same language `less.js` does.

`Lessish` is the only top-level class you need. Instantiate it once and call
`compile`.

```python
from lessish import Lessish

ls = Lessish()
css = ls.compile('@c: red; .a { color: @c; }')
# => '.a {\n  color: red;\n}\n'
```

`compile` takes a string of Less source and returns a string of CSS. That's
the whole contract for the common case.

## One instance, many compiles

A `Lessish` instance is cheap and reusable. Per-compile state is built fresh
inside each call and never stored on the instance, so a single instance is
safe to reuse — and even to share across threads.

```python
from lessish import Lessish

ls = Lessish()
a = ls.compile('.a { width: 1px + 1px; }')
b = ls.compile('.b { color: #fff; }')
# => '.b {\n  color: #fff;\n}\n'
assert a == '.a {\n  width: 2px;\n}\n'
```

## Baking in options

The constructor stores defaults that every `compile` call inherits;
per-call keyword arguments override them. Option names mirror `less.js`,
snake-cased.

```python
from lessish import Lessish

ls = Lessish(compress=True)
minified = ls.compile('.a { color: red; }')
# => '.a{color:red}'

# A per-call override wins over the baked-in default.
pretty = ls.compile('.a { color: red; }', compress=False)
# => '.a {\n  color: red;\n}\n'
```

The full option set — `paths`, `math`, `global_vars`, `source_map`,
`file_io`, and the rest — is in [02-options.md](02-options.md).

## Compiling a file

`compile` takes source text, not a path, so read the file yourself and pass
`filename` so error messages and `@import` resolution have a base path.

```python
# docs: skip
from lessish import Lessish

ls = Lessish()
css = ls.compile(
    open('styles.less', encoding='utf-8').read(),
    filename='styles.less',
)
```

(Marked skip above because it touches the filesystem; the behaviour is
exercised by the CLI in [../cli.md](../cli.md).)

## A note on the security warning

`file_io` defaults to the secure `'jail'`, so the default is silent. If you
opt into the less.js-compatible `file_io='allow'` (Less source can read any
file the process can), lessish emits a `LessishSecurityWarning` on **every**
such compile — informational, never written to the returned CSS. When the
input is trusted and you genuinely want `'allow'`, silence the warning
explicitly:

```python
import warnings
from lessish import Lessish, LessishSecurityWarning

warnings.filterwarnings('ignore', category=LessishSecurityWarning)
css = Lessish(file_io='allow').compile('.a { color: red; }')
# => '.a {\n  color: red;\n}\n'
```

For untrusted input, keep the default `'jail'` (or use `'deny'`) rather than
reaching for `'allow'` — see [02-options.md](02-options.md#file_io-sandbox).

Next: [Options →](02-options.md)
