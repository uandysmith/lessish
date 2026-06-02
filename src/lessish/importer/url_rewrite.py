"""URL rewriting / rootpath / urlArgs application.

Walks evaluated rule trees and rewrites `url(...)` values to compensate
for the consuming file's directory differing from the imported file's
directory, applies the `rootpath` prefix, and appends `urlArgs` query
strings — mirrors the same three-pass pipeline less.js runs after its
import-visitor resolves the file graph.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..ast_nodes import Anonymous, AtRule, Declaration, Node, Ruleset
from .spec import _ImportSpec, parse_import_prelude
from .transforms import _pass_through_as_css
from .url_classify import (
    _is_absolute_url,
    _is_path_local_relative,
    _is_path_relative,
    _normalise_url_path,
    _normalize_path,
    _path_requires_rewrite,
)

# Matches `url(<contents>)`. Captures the inside (with or without
# surrounding quotes) so we can extract and rewrite the path. Anchored
# to the literal `url(` token; assumes balanced single `)` at the end
# (no nested parens — matches less.js's URL parser).
# Capture `url(<quote?><path><quote?>)` where `<path>` may contain
# backslash-escaped chars (`\"`, `\(`, `\)` — common in CSS URLs that
# carry literal quotes / parens, e.g.
# `url(http://fonts.googleapis.com/css?family=\"Rokkitt\":\(400\),700)`).
_URL_RE = re.compile(r'\burl\(\s*([\'"]?)((?:\\.|[^)\'"\\])*)\1\s*\)', re.DOTALL)


def apply_rootpath_to_imports(rules: list[Node], rootpath: str) -> list[Node]:
    """Prefix every surviving root-level `@import` directive's path
    with `rootpath`. Mirrors less.js's behaviour with the `rootpath`
    option: the prefix applies both to `url()` references and to
    top-level `@import`s whether or not `rewriteUrls` is set.
    Absolute / data: / remote / fragment-only paths pass through.
    """
    if not rootpath:
        return rules
    out: list[Node] = []
    for r in rules:
        if isinstance(r, AtRule) and r.name == '@import' and r.body is None:
            spec = parse_import_prelude(r.prelude)
            if spec.path and _path_requires_rewrite(spec.path):
                new_spec = _ImportSpec()
                new_spec.options = spec.options
                new_spec.path = _rewrite_path_with_rootpath(spec.path, rootpath)
                new_spec.path_quote = spec.path_quote
                new_spec.is_url_form = spec.is_url_form
                new_spec.media = spec.media
                out.append(_pass_through_as_css(r, new_spec))
                continue
        out.append(r)
    return out


def apply_rootpath_to_urls(rules: list[Node], rootpath: str) -> list[Node]:
    """Walk evaluated rules and prefix `rootpath` to every `url(...)`
    value that's relative (i.e. not absolute / not data: / not
    fragment-only). Mirrors less.js's `tree/url.js` rootpath branch —
    applies regardless of `rewriteUrls` mode. Unquoted URLs get
    their rootpath escaped (`(`, `)`, quotes, whitespace prefixed
    with `\\`) because parens otherwise terminate the `url(...)` token.
    """
    if not rootpath:
        return rules
    out: list[Node] = []
    for r in rules:
        if isinstance(r, Declaration) and isinstance(r.value, Anonymous):
            new_text = _apply_rootpath_to_urls_in_text(r.value.value, rootpath)
            if new_text != r.value.value:
                out.append(
                    Declaration(
                        index=r.index,
                        name=r.name,
                        value=Anonymous(index=r.value.index, value=new_text),
                        important=r.important,
                        variable=r.variable,
                        merge=r.merge,
                    )
                )
                continue
            out.append(r)
            continue
        if isinstance(r, Ruleset):
            out.append(
                Ruleset(
                    index=r.index,
                    selectors=r.selectors,
                    rules=apply_rootpath_to_urls(r.rules, rootpath),
                    root=r.root,
                    paths=r.paths,
                    condition=r.condition,
                )
            )
            continue
        if isinstance(r, AtRule) and r.body is not None:
            out.append(
                AtRule(
                    index=r.index,
                    name=r.name,
                    prelude=r.prelude,
                    body=apply_rootpath_to_urls(r.body, rootpath),
                )
            )
            continue
        out.append(r)
    return out


def _apply_rootpath_to_urls_in_text(text: str, rootpath: str) -> str:
    def repl(m: re.Match[str]) -> str:
        quote = m.group(1)
        path = m.group(2)
        if not _path_requires_rewrite(path):
            return m.group(0)
        rp = rootpath if quote else _escape_url_path(rootpath)
        new_path = _rewrite_path_with_rootpath(path, rp)
        return f'url({quote}{new_path}{quote})'

    return _URL_RE.sub(repl, text)


def _rewrite_path_with_rootpath(path: str, rootpath: str) -> str:
    """less.js `rewritePath`: normalise(rootpath + path), re-prefix
    `./` when the original path was explicit-local but the rootpath
    join collapsed it away — keeps `./folder (1)/assets/logo.png` from
    flattening into `folder (1)/assets/logo.png`.
    """
    new_path = _normalize_path(rootpath + path)
    if _is_path_local_relative(path) and _is_path_relative(rootpath) and not _is_path_local_relative(new_path):
        new_path = './' + new_path
    return new_path


def _escape_url_path(path: str) -> str:
    """Prefix `\\` to `(`, `)`, `'`, `"`, whitespace in `path` — used
    when concatenating a rootpath into an *unquoted* `url(...)` value,
    where `(` / `)` would otherwise terminate the URL token. Matches
    less.js's `escapePath` in tree/url.js.
    """
    return re.sub(r"[()'\"\s]", lambda m: '\\' + m.group(0), path)


def _rewrite_urls_in_rules(
    rules: list[Node],
    *,
    sub_dir: Path,
    base_dir: Path,
    mode: str,
    rootpath: str = '',
) -> list[Node]:
    """Walk a list of evaluated rules and rewrite each `url(...)` value
    to be relative to `base_dir` instead of `sub_dir`. Recurses into
    nested rulesets and at-rule bodies. Pure: returns new nodes.

    `mode='all'` rewrites every relative url. `mode='local'` only
    rewrites paths that begin with `.` (leaving module-style names
    like `module/path` alone). Absolute URLs (`http://`, `/`, etc.)
    always pass through unchanged.

    `rootpath` is applied to the same paths that get rewritten, so a
    module-style url in `local` mode neither gets rewritten relative to
    base nor receives the rootpath prefix.
    """
    out: list[Node] = []
    for r in rules:
        if isinstance(r, Declaration) and isinstance(r.value, Anonymous):
            new_text = _rewrite_urls_in_text(r.value.value, sub_dir, base_dir, mode, rootpath)
            if new_text != r.value.value:
                out.append(
                    Declaration(
                        index=r.index,
                        name=r.name,
                        value=Anonymous(index=r.value.index, value=new_text),
                        important=r.important,
                        variable=r.variable,
                        merge=r.merge,
                    )
                )
                continue
            out.append(r)
            continue
        if isinstance(r, Ruleset):
            out.append(
                Ruleset(
                    index=r.index,
                    selectors=r.selectors,
                    rules=_rewrite_urls_in_rules(
                        r.rules, sub_dir=sub_dir, base_dir=base_dir, mode=mode, rootpath=rootpath
                    ),
                    root=r.root,
                    paths=r.paths,
                    condition=r.condition,
                )
            )
            continue
        if isinstance(r, AtRule) and r.body is not None:
            out.append(
                AtRule(
                    index=r.index,
                    name=r.name,
                    prelude=r.prelude,
                    body=_rewrite_urls_in_rules(
                        r.body, sub_dir=sub_dir, base_dir=base_dir, mode=mode, rootpath=rootpath
                    ),
                )
            )
            continue
        out.append(r)
    return out


def _rewrite_urls_in_text(text: str, sub_dir: Path, base_dir: Path, mode: str, rootpath: str) -> str:
    """Apply `_rewrite_url` to every `url(...)` occurrence in `text`."""

    def repl(m: re.Match[str]) -> str:
        quote = m.group(1)
        path = m.group(2)
        new_path = _rewrite_url(path, sub_dir, base_dir, mode, rootpath)
        return f'url({quote}{new_path}{quote})'

    return _URL_RE.sub(repl, text)


def _apply_url_args_in_rules(rules: list[Node], url_args: str) -> list[Node]:
    """Walk evaluated rules and append `?<url_args>` (or `&<url_args>`)
    to every `url(...)` value. Recurses into nested rulesets and at-rule
    bodies. Pure: returns new nodes. `data:` URLs are exempt."""
    out: list[Node] = []
    for r in rules:
        if isinstance(r, Declaration) and isinstance(r.value, Anonymous):
            new_text = _apply_url_args_in_text(r.value.value, url_args)
            if new_text != r.value.value:
                out.append(
                    Declaration(
                        index=r.index,
                        name=r.name,
                        value=Anonymous(index=r.value.index, value=new_text),
                        important=r.important,
                        variable=r.variable,
                        merge=r.merge,
                    )
                )
                continue
            out.append(r)
            continue
        if isinstance(r, Ruleset):
            out.append(
                Ruleset(
                    index=r.index,
                    selectors=r.selectors,
                    rules=_apply_url_args_in_rules(r.rules, url_args),
                    root=r.root,
                    paths=r.paths,
                    condition=r.condition,
                )
            )
            continue
        if isinstance(r, AtRule) and r.body is not None:
            out.append(
                AtRule(
                    index=r.index,
                    name=r.name,
                    prelude=r.prelude,
                    body=_apply_url_args_in_rules(r.body, url_args),
                )
            )
            continue
        out.append(r)
    return out


def _apply_url_args_in_text(text: str, url_args: str) -> str:
    """Run `_append_url_args` over every `url(...)` occurrence in
    `text`. Used after rewriting / rootpath application so the args
    sit at the very end of the final URL."""

    def repl(m: re.Match[str]) -> str:
        quote = m.group(1)
        path = m.group(2)
        return f'url({quote}{_append_url_args(path, url_args)}{quote})'

    return _URL_RE.sub(repl, text)


def _append_url_args(url_value: str, url_args: str) -> str:
    """Append `?<url_args>` (or `&<url_args>` if there's already a `?`)
    to a single URL string. `data:` URLs are exempt. `#fragment` is
    preserved at the end (args go before the `#`).
    Mirrors less.js's `tree/url.js` urlArgs branch.
    """
    if not url_value or not url_args:
        return url_value
    # less.js's check is `/^\s*data:/` — tolerate leading whitespace.
    if url_value.lstrip().startswith('data:'):
        return url_value
    delimiter = '&' if '?' in url_value else '?'
    suffix = f'{delimiter}{url_args}'
    if '#' in url_value:
        return url_value.replace('#', f'{suffix}#', 1)
    return url_value + suffix


def _rewrite_url(url_value: str, sub_dir: Path, base_dir: Path, mode: str, rootpath: str) -> str:
    """Rewrite a single url-value string. `mode='all'|'local'`."""
    if not url_value:
        return url_value
    if _is_absolute_url(url_value) or url_value.startswith('data:') or url_value.startswith('#'):
        return url_value
    # `local` mode: paths without a leading `.` are NOT rewritten nor
    # prefixed by rootpath; they're still normalised so e.g.
    # `module/path/../relative/path` becomes `module/relative/path`.
    if mode == 'local' and not url_value.startswith('.'):
        return _normalise_url_path(url_value)
    # Compute the path the imported file's url points at, relative to
    # base_dir. Use os.path.relpath against a synthetic full path.
    full = sub_dir / url_value
    try:
        rel = os.path.relpath(str(full), str(base_dir))
    except ValueError:
        return _apply_rootpath(_normalise_url_path(url_value), rootpath)
    rel_posix = rel.replace(os.sep, '/')
    # less.js convention: when the original was a relative path
    # (`./...` or `../...`), and the rewritten result stays within the
    # consuming file's dir (doesn't itself start with `..`), prefix
    # with `./` for explicit relativeness.
    if (url_value.startswith('./') or url_value.startswith('../')) and not rel_posix.startswith('..'):
        rel_posix = './' + rel_posix
    return _apply_rootpath(_normalise_url_path(rel_posix), rootpath)


def _apply_rootpath(path: str, rootpath: str) -> str:
    """Prefix `path` with `rootpath` (which must end with `/` when
    non-empty for join semantics to make sense). Pure-`./` paths
    collapse to rootpath without trailing slash; leading `./` is
    stripped before concatenation.
    """
    if not rootpath:
        return path
    if _is_absolute_url(path) or path.startswith('data:') or path.startswith('#'):
        return path
    from urllib.parse import urljoin

    if path == './':
        return rootpath.rstrip('/')
    if path.startswith('./'):
        path = path[2:]
    return urljoin(rootpath, path)
