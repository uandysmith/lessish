"""Shallow-cloning utilities for MixinDefinition instances.

Per-match annotations (``_closure_frames``, ``_match_frame_index``)
are written directly on the definition node when found by
``find_mixin_matches``. Concurrent invocations of the same source
definition must not see each other's annotations, so each match is
cloned first.
"""

from __future__ import annotations

import dataclasses as _dc

from ..ast_nodes import MixinDefinition


def _shallow_clone_def(d: MixinDefinition) -> MixinDefinition:
    """Copy a MixinDefinition (shallow on `rules`/`params`) so per-match
    annotations (`_closure_frames`, etc.) don't bleed across concurrent
    invocations that all picked up the SAME definition object.

    `dataclasses.replace` copies every declared field (including lifted
    ones like `_was_referenced`) via the constructor — and every
    per-match annotation lives in a typed field now, so a plain
    `replace()` is sufficient. The historical trailing `__dict__` loop
    that carried "not-yet-typed" keys was kept until every annotation
    landed on the dataclass; it's a no-op against today's typed shape
    and is also incompatible with `@dataclass(slots=True)`, so it's
    gone.
    """
    return _dc.replace(d)
