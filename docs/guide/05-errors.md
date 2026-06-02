# Embedding · Errors

Every exception lessish raises inherits from `LessError`, so one `except`
clause catches anything the compiler can throw.

```python
from lessish import Lessish, LessError

ls = Lessish()
try:
    ls.compile('.a { color: @undefined-name; }')
    ok = True
except LessError:
    ok = False
assert ok is False
```

## The hierarchy

| Exception | Raised when |
|---|---|
| `ParseError` | The lexer / parser refused the input. |
| `EvalError` | Base for evaluation-time failures. Subclasses below. |
| `UndefinedNameError` | A variable or mixin name doesn't resolve. |
| `OperationError` | Bad arithmetic (e.g. incoherent units under `strict_units`). |
| `TypeMismatchError` | A function/operator got the wrong value type. |
| `ArgumentError` | A mixin/function call's arguments don't match. |
| `UnsupportedFeatureError` | An [out-of-scope](../philosophy.md#out-of-scope) construct (JS backticks, `@plugin`, remote `@import`) reached eval. |
| `FileError` | An `@import` target wasn't found. |
| `SecurityError` | The `file_io` policy refused a read. |

`UndefinedNameError`, `OperationError`, `TypeMismatchError`, and
`ArgumentError` are subclasses of `EvalError`; `EvalError`,
`UnsupportedFeatureError`, `FileError`, and `SecurityError` are all
subclasses of `LessError`.

```python
from lessish import Lessish, LessError, EvalError, UndefinedNameError

ls = Lessish()
try:
    ls.compile('.a { color: @nope; }')
except UndefinedNameError as err:
    assert isinstance(err, EvalError)
    assert isinstance(err, LessError)
```

## Location and snippet

Every `LessError` carries a `.location` — a `SourceLocation` with
`filename`, `line`, `column`, and `index` — and, where the stage can
produce one, a `.snippet` string with a caret pointing at the offending
column.

```python
from lessish import Lessish, ParseError

ls = Lessish()
try:
    ls.compile('.a { color: ;', filename='styles.less')
except ParseError as err:
    assert err.location.filename == 'styles.less'
    assert err.location.line == 1
    assert err.location.column == 14
    assert str(err.location) == 'styles.less:1:14'
    print(err.snippet)
```

The `print(err.snippet)` above produces:

```text
   1 | .a { color: ;
                    ^
```

`.snippet` is best-effort: parse errors carry a caret snippet; some
eval-time errors expose only `.location` (then `.snippet` is `None`). Always
guard on `.location`, treat `.snippet` as optional.

Prev: [← The pipeline](04-pipeline.md) · Back to [index](../index.md)
