# Embedding · Source maps

lessish emits [Source Maps v3](https://sourcemaps.info/spec.html), matching
`less.js`'s output. There are two ways to ask for one.

## `compile_with_source_map`

The dedicated entry point returns a `SourceMapResult` with the CSS, the map
JSON, and the annotation URL split apart:

```python
import json
from lessish import Lessish

ls = Lessish()
result = ls.compile_with_source_map(
    '.a { color: red; }',
    filename='styles.less',
    source_map={'outputSourceFiles': True},
)

# CSS, with the trailing /*# sourceMappingURL=… */ annotation.
result.css
# => '.a {\n  color: red;\n}\n/*# sourceMappingURL=styles.css.map */'

# The URL embedded in that annotation.
result.annotation_url
# => 'styles.css.map'

# The Source Maps v3 JSON, as a string.
document = json.loads(result.map_json)
assert document['version'] == 3
assert document['file'] == 'styles.css'
assert document['sources'] == ['styles.less']
# outputSourceFiles=True inlines the original Less into sourcesContent.
assert 'sourcesContent' in document
```

`source_map` accepts the same options dict `less.js` does — for example
`outputSourceFiles`, `sourceMapURL`, `sourceMapBasepath`, `sourceMapRootpath`.
Pass `source_map=True` for defaults.

## Via `compile(source_map=…)`

`compile` also accepts `source_map`; when set, it returns the CSS string
with the annotation appended (the same `result.css` as above), without the
separate map JSON. Use `compile_with_source_map` when you need the map file
itself.

```python
from lessish import Lessish

css = Lessish().compile('.a { color: red; }', filename='styles.less', source_map=True)
assert css.endswith('/*# sourceMappingURL=styles.css.map */')
```

## `SourceMapResult` fields

| Field | Type | Meaning |
|---|---|---|
| `css` | `str` | CSS with the trailing `sourceMappingURL` annotation. |
| `map_json` | `str` | The Source Maps v3 document, serialised. |
| `annotation_url` | `str` | The URL embedded in the annotation comment. |

Writing the two artifacts to disk is exactly what the CLI's `--source-map`
/ `--source-map-out` flags do — see [../cli.md](../cli.md#compile).

Prev: [← Options](02-options.md) · Next: [The pipeline →](04-pipeline.md)
