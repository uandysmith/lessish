# Linter & formatter · Rules

45 rules in four tiers. **Tier** controls which fix-flag applies a rule's
autofix; **severity** is overridable in config (see
[02-configuration.md](02-configuration.md)). **Default** shows the rule's
out-of-the-box configuration in TOML form; **Options** lists every
configurable knob with its type.

Where Default is `—`, the rule has no per-rule options — to turn it off,
list it under `disabled` in the project config.

To configure any option, place it under
`[tool.lessish.lint.rules.<rule-id>]` (or `[lint.rules.<rule-id>]` in
`lessish.toml`):

```toml
[tool.lessish.lint.rules.quote-pref]
enabled = true
prefer = "double"
```

## Tier 0 — formatting (applied by `--fix` and `lessish format`)

Invariant: `compile(fix(s)) == compile(s)` — the compiled CSS is
byte-identical before and after. Verified on a real-world corpus.

| Rule | Severity | Default | Options | Description |
|---|---|---|---|---|
| `trailing-whitespace` | warning | — | — | Strip whitespace at end of line. |
| `final-newline` | warning | — | — | File ends with exactly one `\n`. |
| `no-tabs` | warning | `width = 2` | `width = int` | Tabs in indentation → spaces. |
| `indent-width` | warning | `width = 2` | `width = int` | Each indent level is `width` spaces (statement-starting lines only). |
| `indent-consistency` | warning | `prefer = "spaces"`, `width = 2` | `prefer = "spaces" \| "tabs"`, `width = int` | A file uses tabs OR spaces, not both. |
| `no-multiple-blank-lines` | info | `max = 2` | `max = int` | Collapse `max + 1` or more blank lines. |
| `space-after-colon` | warning | — | — | `color:red` → `color: red`. Skips pseudo-class colons. |
| `space-before-lbrace` | warning | — | — | `.a{` → `.a {`. |
| `no-space-after-lbrace` | warning | — | — | Collapse 2+ spaces after `{` on the same line. |
| `no-space-before-rbrace` | warning | — | — | Collapse 2+ spaces before `}` on the same line. |
| `space-around-combinator` | warning | — | — | `.a>.b` → `.a > .b`. Selector-context aware. |
| `space-around-binary-op` | info | `enabled = false` | `enabled = bool` | `1px+2px` → `1px + 2px`. |
| `no-space-around-attr-eq` | warning | — | — | `[type = "x"]` → `[type="x"]`. |
| `semicolon-required` | warning | — | — | Append `;` even on the last declaration in a block. |
| `no-trailing-semicolon-in-empty-block` | info | — | — | Drop lone `;` inside an otherwise-empty block. |
| `quote-pref` | info | `enabled = false`, `prefer = "single"` | `enabled = bool`, `prefer = "single" \| "double"` | Enforce single or double quotes consistently. |
| `blank-line-at-block-start` | warning | — | — | No blank line directly after `{`. |
| `blank-line-at-block-end` | warning | — | — | No blank line directly before `}`. |
| `blank-line-before-block` | info | `enabled = false` | `enabled = bool` | Blank line between top-level rulesets. `lessish format` turns this on. |
| `block-opening-brace-line` | info | `enabled = false` | `enabled = bool` | `{` on the selector line, not its own (Allman-style teams). |
| `block-opening-brace-newline-after` | warning | `width = 2` | `width = int` | Block content starts on a new line, indented. |
| `block-closing-brace-newline-after` | warning | `width = 2` | `width = int` | Next sibling after `}` starts on a new line. |
| `closing-brace-newline-before` | warning | `threshold = 1` | `threshold = int` | `}` on its own line for blocks with ≥ `threshold` declarations. Set to `2` to allow `.a { color: red; }`. |
| `semicolon-newline-after` | warning | `width = 2` | `width = int` | Each statement on its own line. |
| `decls-before-rulesets` | info | `width = 2` | `width = int` | Group declarations before nested rulesets, separated by a blank line. Refuses blocks with mixin calls / at-rules / `&` parent-refs / `:extend`. |

## Tier 1 — token-level safe rewrites (applied by `--fix`)

Semantic equivalents — compiled CSS may differ byte-wise but renders
identically.

| Rule | Severity | Default | Options | Description |
|---|---|---|---|---|
| `hex-short` | warning | — | — | `#RRGGBB` → `#RGB` when both halves match. |
| `hex-case` | warning | — | — | Hex literals lowercased. |
| `zero-unit` | warning | — | — | `0px` / `0em` / `0%` → `0` (units kept inside `calc(…)`). |
| `decimal-leading-zero` | warning | — | — | `.5em` → `0.5em`. |
| `trailing-zero` | warning | — | — | Trim trailing zeros in numeric literals. |

## Tier 2 — risky rewrites (require `--unsafe-fix` or `--project`)

May change observable behaviour if cross-file references exist. `--project`
mode raises `unused-variable` / `unused-mixin` to safe.

| Rule | Severity | Default | Options | Description |
|---|---|---|---|---|
| `unused-variable` | warning | — | — | `@x: …;` declared but never referenced in the file. Fix removes the declaration. |
| `unused-mixin` | warning | — | — | Mixin defined but never called or `:extend`ed. Fix removes the definition. |
| `duplicate-property` | warning | — | — | Same property+value+`!important` declared twice in one ruleset. Fix drops the earlier. |
| `redundant-mixin-args` | info | — | — | Mixin call whose args all equal the definition defaults. Fires only when one matching definition exists. Fix strips args. |

## Tier 3 — detect only (no autofix)

| Rule | Severity | Default | Options | Description |
|---|---|---|---|---|
| `deep-nesting` | warning | `max-depth = 4` | `max-depth = int` | Rulesets nested deeper than `max-depth`. |
| `unsupported-feature` | error | — | — | `@plugin "…"`, backtick JS expressions, remote `@import`. |
| `magic-number` | info | `min-occurrences = 3` | `min-occurrences = int` | Numeric literal repeated this many times without an extracted variable. |
| `important-overuse` | warning | `max-per-file = 0` | `max-per-file = int` | Number of `!important` declarations exceeds `max-per-file`. |
| `excessive-mixin-args` | warning | `max-args = 6` | `max-args = int` | Mixin definition with this many parameters or more. |
| `confusing-default-value` | warning | — | — | Mixin default that forward-references a later parameter. |
| `mixed-rest-and-default` | warning | — | — | Mixin signature mixes `@rest...` with default-valued params. |
| `extend-cross-media` | warning | — | — | `:extend(…)` inside `@media` whose target sits outside it. |
| `ambiguous-math` | warning | — | — | Arithmetic outside parens where `math` mode would change the result. |
| `redefined-builtin` | error | — | — | User mixin whose name matches a built-in (`lighten`, `rgba`, …). |
| `unreachable-mixin-branch` | warning | — | — | Mixin / CSS guard that's statically false (e.g. `when (1 = 2)`). |

## Fix tiers and safety

| Tier | Applied by | Safety |
|---|---|---|
| 0 (formatting) | `--fix`, `lessish format` | `compile(fix(s)) == compile(s)` |
| 1 (token rewrites) | `--fix` | Semantic equivalence; output may differ byte-wise. |
| 2 (risky) | `--unsafe-fix`, or `--fix` with `--project` | May change observable CSS. |
| 3 (detect only) | — | No autofix. |

When in doubt: `lessish lint --fix`, review the diff, commit. Then optionally
`lessish lint --unsafe-fix` and review again. For pure formatting use
`lessish format` — the strictest invariant.

## Cross-file mode (`--project`)

Pass a directory; the linter scans every `.less` file under it, builds a
global index (variable references, mixin call segments, `:extend` targets),
and feeds that index to per-file linting.

What changes:

- `unused-variable` skips findings for any variable referenced anywhere in
  the project tree.
- `unused-mixin` skips findings for any mixin whose name appears as a call
  segment or `:extend` target across the project.
- Both rules' autofix safety is raised from `risky` to `safe`, so plain
  `--fix` applies them.

```console
$ lessish lint --project src/ src/**/*.less   # docs: skip
$ lessish lint --project src/ --fix src/**/*.less   # docs: skip
```

## Cache, `--full`, telemetry

**Cache** is on by default. On the first run lessish populates
`.lessish-cache/` next to the project's `pyproject.toml` / `lessish.toml`;
subsequent runs read cached findings for unchanged files (typically **5–9×**
warm vs cold).

- **Key:** `sha256(content + config_signature + lessish_version)` — changes
  to source, config, or lessish version invalidate the affected entries.
- **No project root, no cache.** If lessish can't find a config file walking
  up, caching is silently skipped.
- **Bypassed under `--fix` / `--unsafe-fix`** — fixes mutate source.
- **`lessish format`** does not use the cache.

Add `.lessish-cache/` to `.gitignore`.

**`--full`** enables eval-augmented rules — those that need the post-eval
AST. Slower; runs the full compile pipeline. Intended for CI / pre-merge
rather than save-on-edit.

**`--telemetry-out PATH`** writes per-rule hit counts to a JSON file. Opt-in;
nothing leaves the machine otherwise.

```json
{
  "schemaVersion": 1,
  "totalFiles": 42,
  "rules": [
    { "rule": "hex-short", "count": 119, "files": 27 }
  ]
}
```

Prev: [← Configuration](02-configuration.md) · Next: [Programmatic API →](04-api.md)
