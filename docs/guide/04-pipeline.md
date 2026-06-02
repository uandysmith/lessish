# Embedding · The addressable pipeline

`compile` is four stages chained together. Each stage is a public method, so
tooling — editors, linters, language servers — can call exactly the stage it
needs without paying for the rest.

| Method | Stage | Input → Output |
|---|---|---|
| `tokenize(source)` | lex | `str` / `Source` → `TokenStream` |
| `parse(source)` | parse | `str` / `Source` / `TokenStream` → `Ruleset` (AST; declaration values still raw text) |
| `evaluate(root, src=…)` | eval | `Ruleset` → `Ruleset` (`@import` resolved, variables substituted, mixins expanded, `:extend` applied, at-rules bubbled) |
| `emit(root, src=…)` | emit | `Ruleset` → CSS `str` (or `SourceMapResult`) |
| `compile(source)` | parse + eval + emit | `str` → CSS `str` |

## Running the stages by hand

The cleanest way is to wrap your source in a `Source` once and thread it
through, so error locations and source maps know the filename:

```python
from lessish import Lessish, Source

ls = Lessish()
src = Source('@c: red; .a { color: @c; }', filename='styles.less')

tokens = ls.tokenize(src)        # lex
ast = ls.parse(tokens)           # parse — reuses the token stream
evaled = ls.evaluate(ast, src=src)   # variables / mixins resolved
css = ls.emit(evaled, src=src)       # final CSS
# => '.a {\n  color: red;\n}\n'
```

Note `parse` accepts the `TokenStream` returned by `tokenize`, so a language
server that already tokenised for syntax highlighting doesn't pay the regex
cost twice.

`src` is optional. If you only need a quick transform and don't care about
filenames in diagnostics, omit it:

```python
from lessish import Lessish

ls = Lessish()
css = ls.emit(ls.evaluate(ls.parse('@c: red; .a { color: @c; }')))
# => '.a {\n  color: red;\n}\n'
```

## Stopping early — tokens and AST without a full compile

`tokenize` and `parse` are useful on their own for tooling that wants a
position-aware view of the source. Crucially, they work even on input that
`evaluate` would reject (backtick JS, `@plugin`, remote `@import`) — those
only fail at the eval stage. See [../philosophy.md](../philosophy.md#out-of-scope).

```python
from lessish import Lessish, TokenStream, Ruleset

ls = Lessish()

tokens = ls.tokenize('@c: red; .a { color: @c; }')
assert isinstance(tokens, TokenStream)
assert len(tokens) > 0          # iterable, sized, indexable

ast = ls.parse('@c: red; .a { color: @c; }')
assert isinstance(ast, Ruleset)
```

## Subclassing — override one stage

Because each stage is a method and `compile` calls them through `self`,
overriding one stage automatically affects `compile`. Per-compile state
(`EvalContext`, the emitter, guard state) is built fresh inside each method
and never held on the instance, so overrides stay thread-safe.

```python
from lessish import Lessish

class CountingLessish(Lessish):
    """A Lessish that counts how many times it emits CSS."""

    emit_calls = 0

    def emit(self, root, **kwargs):
        type(self).emit_calls += 1
        return super().emit(root, **kwargs)

ls = CountingLessish()
ls.compile('.a { color: red; }')   # routes through the overridden emit
ls.compile('.b { color: blue; }')
assert CountingLessish.emit_calls == 2
```

Typical uses: wrap `emit` with timing, swap `evaluate` for a
sandboxed-imports variant, or instrument `parse` for an editor.

Prev: [← Source maps](03-source-maps.md) · Next: [Errors →](05-errors.md)
