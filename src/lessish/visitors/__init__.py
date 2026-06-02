"""Post-evaluation passes split across themed sub-modules.

  * `emitter`   — `Emitter`, `value_to_css`, dimension/important helpers
  * `compress`  — compress-mode string post-processors
  * `selectors` — selector serialization + `join_selectors` pass
  * `extends`   — `:extend(...)` resolution
  * `atrules`   — at-rule classification frozensets + media prelude norm
  * `structure` — dedup, merge, bubble passes
  * `checks`    — pre-emit safety + emittable-content probes
  * `emit`      — `emit_css` walker

`__all__` is the package's public surface — the passes `Lessish` runs
plus `Emitter` / `value_to_css` / `selector_to_str` for tooling. Internal
helpers (the leading-underscore names) live on the themed sub-modules and
are imported from there directly (`from .visitors.emitter import
_important_suffix`), not re-exported here.
"""

from __future__ import annotations

from .checks import check_no_root_properties
from .emit import emit_css
from .emitter import Emitter, value_to_css
from .extends import apply_extends
from .selectors import join_selectors, selector_to_str
from .structure import (
    bubble_atrules,
    dedup_charset,
    dedup_declarations,
    merge_rules,
    merge_same_path_nested,
)

__all__ = [
    'Emitter',
    'apply_extends',
    'bubble_atrules',
    'check_no_root_properties',
    'dedup_charset',
    'dedup_declarations',
    'emit_css',
    'join_selectors',
    'merge_rules',
    'merge_same_path_nested',
    'selector_to_str',
    'value_to_css',
]
