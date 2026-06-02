"""URL / path classification primitives.

Boolean predicates and small normalisers used by the import policy and
URL-rewriting code paths to decide whether a given path string should
be treated as remote, absolute, relative, or eligible for rewriting.
"""

from __future__ import annotations

import posixpath
import re


def _is_absolute_url(path: str) -> bool:
    """True for any of: remote URL scheme (`http:`, `https:`, etc.),
    protocol-relative `//host/...`, or absolute filesystem path `/foo`.
    Kept as a union for the few call sites (e.g. url-rewriting) that
    just need "don't touch this path"; the import policy splits these
    apart via `_is_remote_url` vs `_is_absolute_filesystem_path`.
    """
    return _is_remote_url(path) or _is_absolute_filesystem_path(path)


def _is_remote_url(path: str) -> bool:
    """True for paths that would require a network fetch — explicit
    `http:`/`https:`/`ftp:` schemes or protocol-relative `//host/...`.
    Caller raises `UnsupportedFeatureError` for these (we never fetch).
    """
    if path.startswith('//'):
        return True
    # Match any `<scheme>:` URL where `<scheme>` is alphanumeric (per
    # RFC 3986). Covers `http`, `https`, `ftp`, `ftps`. Excludes
    # Windows-style `C:\…` (alpha + `:` + `\`) and `data:` (handled
    # separately — `data:` URIs don't need a fetch, the bytes are
    # inline).
    if path.startswith('data:'):
        return False
    colon = path.find(':')
    if colon <= 0:
        return False
    scheme = path[:colon]
    return scheme.isalnum() and len(scheme) >= 2


def _is_absolute_filesystem_path(path: str) -> bool:
    """True for a `/foo`-style absolute path (POSIX). Excludes
    protocol-relative `//host`."""
    return path.startswith('/') and not path.startswith('//')


def _path_basename(path: str) -> str:
    """Last path component, stripped of any `?…` query string. Used to
    classify remote @import paths (`.css` plain-pass vs `.less`-needs-
    fetch) without dragging in `urllib.parse`.
    """
    q = path.split('?', 1)[0]
    return q.rsplit('/', 1)[-1]


def _has_known_extension(path: str) -> bool:
    """`.less` and `.css` are the only extensions that participate in
    extension-aware routing. Anything else falls back to the `.less`
    candidate (less.js does the same)."""
    lower = path.lower()
    return lower.endswith(('.less', '.css'))


def _path_requires_rewrite(path: str) -> bool:
    """Mirrors less.js's `isPathRelative` (the `rewriteUrls=all` /
    default branch of `pathRequiresRewrite`): path needs prefixing
    unless it starts with `<scheme>:`, `/`, or `#`.
    """
    if not path:
        return False
    return _is_path_relative(path)


def _is_path_relative(path: str) -> bool:
    if not path:
        return False
    if path.startswith('/') or path.startswith('#'):
        return False
    return re.match(r'^[a-zA-Z-]+:', path) is None


def _is_path_local_relative(path: str) -> bool:
    return bool(path) and path[0] == '.'


def _normalize_path(path: str) -> str:
    """less.js `normalizePath`: collapses `.` segments away and
    resolves `..` when there's a preceding non-`..` segment.
    """
    out: list[str] = []
    for seg in path.split('/'):
        if seg == '.':
            continue
        if seg == '..' and out and out[-1] != '..':
            out.pop()
        else:
            out.append(seg)
    return '/'.join(out)


def _normalise_url_path(path: str) -> str:
    """Collapse `./` and `../` segments inside a relative URL path. The
    leading `./` (when present in the input) is preserved.
    """
    had_dot_slash = path.startswith('./')
    normalised = posixpath.normpath(path)
    if normalised == '.':
        return './'
    if had_dot_slash and not normalised.startswith('../') and not normalised.startswith('./'):
        return './' + normalised
    return normalised
