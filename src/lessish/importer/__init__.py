"""@import resolution package.

* ``spec``         — `_ImportSpec` dataclass and prelude parser.
* ``url_classify`` — boolean classifiers for path / URL strings.
* ``url_rewrite``  — rootpath / rewriteUrls / urlArgs transforms.
* ``transforms``   — AST manipulations on the imported subtree.
* ``importer``     — the `Importer` class that orchestrates resolution.
"""

from __future__ import annotations

from .._strutil import strip_quotes as _strip_quotes
from .importer import Importer
from .spec import _ImportSpec, _is_malformed_import_prelude, parse_import_prelude
from .transforms import (
    _atrule_has_inner_rulesets,
    _inline_as_nodes,
    _mark_reference,
    _pass_through_as_css,
    _tag_source,
)
from .url_classify import (
    _has_known_extension,
    _is_absolute_filesystem_path,
    _is_absolute_url,
    _is_path_local_relative,
    _is_path_relative,
    _is_remote_url,
    _normalise_url_path,
    _normalize_path,
    _path_basename,
    _path_requires_rewrite,
)
from .url_rewrite import (
    _URL_RE,
    _append_url_args,
    _apply_rootpath,
    _apply_rootpath_to_urls_in_text,
    _apply_url_args_in_rules,
    _apply_url_args_in_text,
    _escape_url_path,
    _rewrite_path_with_rootpath,
    _rewrite_url,
    _rewrite_urls_in_rules,
    _rewrite_urls_in_text,
    apply_rootpath_to_imports,
    apply_rootpath_to_urls,
)

__all__ = [
    # Public surface.
    'Importer',
    'parse_import_prelude',
    'apply_rootpath_to_imports',
    'apply_rootpath_to_urls',
    # Privates re-exported so other modules / tests can reach them via
    # `from lessish.importer import _foo` without F401 noise.
    '_ImportSpec',
    '_is_malformed_import_prelude',
    '_atrule_has_inner_rulesets',
    '_inline_as_nodes',
    '_mark_reference',
    '_pass_through_as_css',
    '_tag_source',
    '_has_known_extension',
    '_is_absolute_filesystem_path',
    '_is_absolute_url',
    '_is_path_local_relative',
    '_is_path_relative',
    '_is_remote_url',
    '_normalise_url_path',
    '_normalize_path',
    '_path_basename',
    '_path_requires_rewrite',
    '_URL_RE',
    '_append_url_args',
    '_apply_rootpath',
    '_apply_rootpath_to_urls_in_text',
    '_apply_url_args_in_rules',
    '_apply_url_args_in_text',
    '_escape_url_path',
    '_rewrite_path_with_rootpath',
    '_rewrite_url',
    '_rewrite_urls_in_rules',
    '_rewrite_urls_in_text',
    '_strip_quotes',
]
