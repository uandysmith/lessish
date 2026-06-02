"""Mixin support: AST transform, parameter/argument parsing, invocation.

A ``.name(params) { rules }`` block initially parses as a Ruleset whose
selector ends with a ``(...)`` tail element. The block parser
post-processes that tree to rewrite those Rulesets into
``MixinDefinition`` nodes — and ``MixinCallStatement`` placeholders into
structured ``MixinCall`` nodes — before evaluation runs.

The evaluator (``evaluator.eval_ruleset``) invokes mixins by binding
positional args into a synthetic frame, pushing the mixin body as
another frame so its own variable declarations resolve, and splicing
the evaluated body rules back into the parent block.
"""

from __future__ import annotations

from .arg_frame import (
    _arg_item,
    _build_arg_value,
    _peel_singleton_wrappers,
    build_arg_frame,
)
from .clones import _shallow_clone_def
from .matching import (
    _arity_compatible,
    _descend_namespace,
    _match_in_rules,
    _mixin_index_for,
    _MixinIndex,
    _ruleset_amp_compound_name,
    _ruleset_as_mixin,
    _ruleset_mixin_name,
    _ruleset_mixin_names,
    _selector_mixin_name,
    _values_equal,
    find_mixin_matches,
    name_exists,
)
from .namespace import _split_namespace_path
from .params import (
    _NAMED_ARG_RE,
    _detect_mixed_delim_pos,
    _has_top_level_separator,
    _parse_mixin_call_text,
    _skip_string,
    _split_on,
    _split_top_level,
    _strip_comments_from_args,
    parse_mixin_args,
    parse_mixin_params,
)
from .transform import (
    _KNOWN_STATEMENT_AT_RULES,
    _ExtendStatement,
    _extract_css_guard,
    _extract_mixin_name,
    _is_mixin_definition,
    _is_paren_balanced,
    _parse_guard_if_any,
    _selector_has_guard_tail,
    _split_signature_and_guard,
    _strip_outer_parens,
    transform_mixins,
)

__all__ = [
    '_ExtendStatement',
    '_KNOWN_STATEMENT_AT_RULES',
    '_MixinIndex',
    '_NAMED_ARG_RE',
    '_arg_item',
    '_arity_compatible',
    '_build_arg_value',
    '_descend_namespace',
    '_detect_mixed_delim_pos',
    '_extract_css_guard',
    '_extract_mixin_name',
    '_has_top_level_separator',
    '_is_mixin_definition',
    '_is_paren_balanced',
    '_match_in_rules',
    '_mixin_index_for',
    '_parse_guard_if_any',
    '_parse_mixin_call_text',
    '_peel_singleton_wrappers',
    '_ruleset_amp_compound_name',
    '_ruleset_as_mixin',
    '_ruleset_mixin_name',
    '_ruleset_mixin_names',
    '_selector_has_guard_tail',
    '_selector_mixin_name',
    '_shallow_clone_def',
    '_skip_string',
    '_split_namespace_path',
    '_split_on',
    '_split_signature_and_guard',
    '_split_top_level',
    '_strip_comments_from_args',
    '_strip_outer_parens',
    '_values_equal',
    'build_arg_frame',
    'find_mixin_matches',
    'name_exists',
    'parse_mixin_args',
    'parse_mixin_params',
    'transform_mixins',
]
