"""`data-uri()` and the file-introspection helpers (`image-size`,
`image-width`, `image-height`).

`data-uri('mime', 'path')` reads the file at `path` (relative to the
calling .less file's directory) and inlines its content as a
`url("data:<mime>,<content>")` value. When the mime is binary
(`;base64` suffix or sniffed by extension), the content is base64-
encoded; for text-like mimes (`text/...`, `image/svg+xml`) less.js
URL-encodes the raw bytes instead. Mime is sniffed from the path
extension when the first arg is omitted.

The `image-*` functions decode a PNG/JPEG's intrinsic pixel dimensions
without pulling in Pillow or any other dep — just enough byte-level
parsing for the PNG IHDR chunk and JPEG SOF marker.
"""

from __future__ import annotations

import base64
import re as _re_module
import struct
from pathlib import Path
from urllib.parse import quote

from ..ast_nodes import Anonymous, Dimension, Node, Quoted, Url
from ..context import EvalContext
from ..errors import ArgumentError, SecurityError
from . import register
from ._helpers import quoted_value, unwrap

# Characters preserved verbatim by `_escape_url_fallback`. Anything not
# in this set (notably `<`, `>`, `"`, `'`, `\`, control chars) is
# percent-encoded so an attacker-controlled path or fragment can neither
# break out of the surrounding CSS `url("…")` string nor close the
# enclosing HTML `<style>` block when the compiled CSS is inlined.
# Includes the typical URL syntax chars and path delimiters so legitimate
# paths round-trip readably.
_URL_FALLBACK_SAFE = '/?#[]@!$&()*+,;=._~-:'


def _escape_url_fallback(s: str) -> str:
    """Percent-encode anything in `s` that could break out of the
    surrounding CSS string or an enclosing HTML `<style>` block.
    Applied to all user-controlled segments (path, fragment, mime)
    before they land inside the final `url("…")` value. Deliberately
    stricter than less.js, which emits the raw value: the parity with
    less.js's output stops being safe once untrusted Less is in scope.
    """
    return quote(s, safe=_URL_FALLBACK_SAFE)


# Filename extension → (mime, is_base64). Mirrors less.js's
# `lib/less/functions/data-uri.js` mimeLookup table.
_MIME_TABLE: dict[str, tuple[str, bool]] = {
    '.jpg': ('image/jpeg', True),
    '.jpeg': ('image/jpeg', True),
    '.png': ('image/png', True),
    '.gif': ('image/gif', True),
    '.webp': ('image/webp', True),
    '.svg': ('image/svg+xml', False),
    '.html': ('text/html', False),
    '.htm': ('text/html', False),
    '.txt': ('text/plain', False),
    '.css': ('text/css', False),
    '.js': ('application/javascript', False),
}


@register('data-uri')
def fn_data_uri(args: list[Node], ctx: EvalContext) -> Node:
    if not args or len(args) > 2:
        raise ArgumentError('data-uri() expects 1 or 2 arguments')
    if len(args) == 2:
        mime_full = quoted_value(args[0], fn_name='data-uri').value
        path_str = quoted_value(args[1], fn_name='data-uri').value
    else:
        mime_full = ''
        path_str = quoted_value(args[0], fn_name='data-uri').value

    # `image.jpg#fragment` — split off the fragment so it can travel
    # past the encoded payload unchanged.
    fragment = ''
    if '#' in path_str:
        path_str, fragment = path_str.split('#', 1)
        fragment = '#' + fragment

    _check_io_allowed(path_str, ctx, fn_name='data-uri')
    file_path = _resolve_path(path_str, ctx)
    suffix = Path(path_str).suffix.lower()

    if mime_full:
        base64_flag = mime_full.endswith(';base64')
        mime = mime_full
    else:
        guessed_mime, base64_flag = _MIME_TABLE.get(suffix, ('application/octet-stream', True))
        mime = guessed_mime + (';base64' if base64_flag else '')

    # `path_str`, `fragment`, and (when user-supplied) `mime` all flow
    # from the Less source straight into the final `url("…")` payload.
    # Escape them so untrusted input cannot break the CSS string or
    # the enclosing HTML `<style>` block. See `_escape_url_fallback`.
    safe_path = _escape_url_fallback(path_str)
    safe_fragment = _escape_url_fallback(fragment)
    safe_mime = _escape_url_fallback(mime)

    if file_path is None or not file_path.exists():
        # less.js falls back to plain `url(<path>)` on missing file.
        return Url(index=args[0].index, value=f'"{safe_path}{safe_fragment}"')

    raw = file_path.read_bytes()
    if base64_flag:
        encoded = base64.b64encode(raw).decode('ascii')
    else:
        # less.js applies `encodeURIComponent`, which keeps `!~*'()` and
        # alphanumerics. Match by passing those as `safe`.
        text = raw.decode('utf-8')
        encoded = quote(text, safe="!~*'()", encoding='utf-8')

    return Url(index=args[0].index, value=f'"data:{safe_mime},{encoded}{safe_fragment}"')


@register('image-size')
def fn_image_size(args: list[Node], ctx: EvalContext) -> Node:
    width, height = _image_dimensions(args, ctx, fn_name='image-size')
    return Anonymous(index=args[0].index, value=f'{width}px {height}px')


@register('image-width')
def fn_image_width(args: list[Node], ctx: EvalContext) -> Node:
    width, _ = _image_dimensions(args, ctx, fn_name='image-width')
    return Dimension(index=args[0].index, value=width, unit='px')


@register('image-height')
def fn_image_height(args: list[Node], ctx: EvalContext) -> Node:
    _, height = _image_dimensions(args, ctx, fn_name='image-height')
    return Dimension(index=args[0].index, value=height, unit='px')


def _check_io_allowed(path_str: str, ctx: EvalContext, *, fn_name: str) -> None:
    """Raise `SecurityError` when the active importer's `file_io`
    policy forbids reading `path_str`. `deny` rejects unconditionally;
    `jail` rejects absolute paths up front so the message is precise
    (the jail-relative test in `_resolve_path` covers the relative
    case via `existence`-style filtering).
    """
    importer = getattr(ctx, 'importer', None)
    # Fail closed: an absent / attr-less importer (hand-built EvalContext,
    # e.g. a direct unit-test call that bypasses the pipeline) falls back
    # to the secure `jail` policy, not `allow`. The public pipeline always
    # wires a real importer, so this default only affects code paths that
    # skip it.
    policy = getattr(importer, 'file_io', 'jail')
    if policy == 'deny':
        raise SecurityError(f"file_io='deny' refuses {fn_name}({path_str!r})")
    if policy == 'jail' and Path(path_str).is_absolute():
        raise SecurityError(f"file_io='jail' refuses absolute path {path_str!r} in {fn_name}()")


def _jail_allows(resolved: Path, ctx: EvalContext) -> bool:
    """True when `resolved` is permitted by the active `file_io` policy.
    Delegates to `Importer.jail_allows` so the jail boundary lives in
    exactly one place and a `jail`-mode `data-uri()` can't escape to
    files outside `base_dir`/`paths` via `..` segments that survive
    `.resolve()`. With no importer wired (hand-built EvalContext) the
    read is allowed — the pipeline always supplies a real importer."""
    importer = getattr(ctx, 'importer', None)
    allows = getattr(importer, 'jail_allows', None)
    if allows is None:
        return True
    return bool(allows(resolved))


def _resolve_path(path_str: str, ctx: EvalContext) -> Path | None:
    """Resolve `path_str` to a real file using the same lookup chain
    `@import` uses: source-relative first, then each `paths[]` entry
    in turn. Returns `None` only when no candidate exists on disk.

    In `jail` mode candidates that resolve outside the importer's
    pre-computed roots are filtered out before the existence check —
    a `..`-traversal hit then surfaces as a normal "file not found"
    fallback (less.js's behaviour for missing data-uri targets).
    """
    p = Path(path_str)
    if p.is_absolute():
        if p.exists() and _jail_allows(p, ctx):
            return p
        return None
    base = Path(ctx.source.filename).parent if ctx.source and ctx.source.filename else Path()
    primary = (base / p).resolve()
    if primary.exists() and _jail_allows(primary, ctx):
        return primary
    importer = getattr(ctx, 'importer', None)
    if importer is not None:
        for search_dir in getattr(importer, 'paths', ()):
            search_path = Path(search_dir)
            if not search_path.is_absolute():
                search_path = (base / search_path).resolve()
            candidate = (search_path / p).resolve()
            if candidate.exists() and _jail_allows(candidate, ctx):
                return candidate
    # Caller checks `.exists()` too — return the source-relative
    # resolution as a default for downstream error messages. In jail
    # mode we suppress this fallback when it would escape the jail, so
    # callers see a clean "not found" instead of a leaked path.
    if not _jail_allows(primary, ctx):
        return None
    return primary


def _image_dimensions(args: list[Node], ctx: EvalContext, *, fn_name: str) -> tuple[int, int]:
    if len(args) != 1:
        raise ArgumentError(f'{fn_name}() expects 1 argument')
    arg = unwrap(args[0])
    if not isinstance(arg, Quoted):
        raise ArgumentError(f'{fn_name}() expected a string, got {type(arg).__name__}')
    _check_io_allowed(arg.value, ctx, fn_name=fn_name)
    file_path = _resolve_path(arg.value, ctx)
    if file_path is None or not file_path.exists():
        raise ArgumentError(f"{fn_name}: file '{arg.value}' not found")
    raw = file_path.read_bytes()
    dims = _decode_png(raw) or _decode_jpeg(raw) or _decode_svg(raw)
    if dims is None:
        raise ArgumentError(f"{fn_name}: unsupported image format for '{arg.value}'")
    return dims


def _decode_png(buf: bytes) -> tuple[int, int] | None:
    """Return (width, height) for a PNG, or None if `buf` isn't a PNG.
    The IHDR chunk (first chunk after the 8-byte signature) carries
    width/height as big-endian 32-bit ints at offsets 16 and 20.
    """
    if len(buf) < 24 or buf[:8] != b'\x89PNG\r\n\x1a\n' or buf[12:16] != b'IHDR':
        return None
    width, height = struct.unpack('>II', buf[16:24])
    return int(width), int(height)


# Segments whose content shouldn't be matched as live SVG markup:
# comments, CDATA sections, and DTD prologs. Stripped before the
# `<svg ...>` search so a `<svg>` literal hidden inside any of them
# can't trick the dimension probe. (No XML parser here on purpose —
# `xml.etree.ElementTree` is vulnerable to billion-laughs entity
# expansion and requires well-formed input; we only need two
# attributes off the root and want graceful degradation on truncated
# / malformed SVG. Matches the regex-based approach used by less.js's
# `image-size` library.)
_SVG_PROLOG_RE = _re_module.compile(
    r'<!--.*?-->'  # comment
    r'|<!\[CDATA\[.*?\]\]>'  # CDATA section
    r'|<\?[^>]*\?>'  # processing instruction (e.g. `<?xml ...?>`)
    r'|<!DOCTYPE\b[^>\[]*(?:\[[^\]]*\])?\s*>',  # DOCTYPE incl. internal subset
    _re_module.DOTALL,
)
_SVG_TAG_RE = _re_module.compile(r'<svg\b([^>]*)>', _re_module.IGNORECASE | _re_module.DOTALL)
_SVG_LENGTH_RE = _re_module.compile(r'^\s*([0-9]+(?:\.[0-9]+)?|\.[0-9]+)\s*(px)?\s*$', _re_module.IGNORECASE)


def _extract_svg_attr(attrs: str, name: str) -> str | None:
    """Pull `name="..."` / `name='...'` / `name=value` off an `<svg>`
    tag's attribute span. Returns the raw attribute body or `None` if
    absent.
    """
    pattern = _re_module.compile(
        rf'\b{name}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s/>]+))',
        _re_module.IGNORECASE,
    )
    m = pattern.search(attrs)
    if m is None:
        return None
    return m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))


def _parse_svg_length(value: str) -> int | None:
    """Convert an SVG `<length>` attribute to integer pixels.
    Accepts bare numbers and explicit `px`; rejects `em`/`%`/`pt`/etc.
    less.js's image-size pipeline only resolves px-shaped SVG roots,
    and silently truncating `2em` to `2` (the old regex's behaviour)
    is a footgun.
    """
    m = _SVG_LENGTH_RE.match(value)
    if m is None:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def _decode_svg(buf: bytes) -> tuple[int, int] | None:
    """Pull `width` / `height` attributes off the top-level `<svg>`
    element.

    XML-shaped prologs (comments, CDATA, processing instructions,
    DOCTYPE) are stripped first so a `<svg>` literal hidden inside
    any of them can't poison the dimension probe; only bare-number
    / `px` lengths are accepted. Returns `None` for any other shape
    — caller surfaces the standard "unsupported image format"
    diagnostic.

    Intentionally regex-based rather than `xml.etree.ElementTree`-based:
    we only need two scalar attributes off the root, full XML parsing
    would build a DOM we throw away, ET is vulnerable to entity-expansion
    bombs without the `defusedxml` dep, and ET fails on truncated /
    malformed SVG where regex degrades to "couldn't find a number".
    Matches the approach less.js's `image-size` library uses.
    """
    try:
        text = buf.decode('utf-8', errors='replace')
    except Exception:
        return None
    text = _SVG_PROLOG_RE.sub('', text)
    m = _SVG_TAG_RE.search(text)
    if m is None:
        return None
    attrs = m.group(1)
    width_raw = _extract_svg_attr(attrs, 'width')
    height_raw = _extract_svg_attr(attrs, 'height')
    if width_raw is None or height_raw is None:
        return None
    width = _parse_svg_length(width_raw)
    height = _parse_svg_length(height_raw)
    if width is None or height is None:
        return None
    return width, height


def _decode_jpeg(buf: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments looking for a Start-Of-Frame marker (SOF0..3,
    SOF5..7, SOF9..11, SOF13..15) and read its 16-bit height / width.
    """
    if len(buf) < 4 or buf[:2] != b'\xff\xd8':
        return None
    i = 2
    n = len(buf)
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while i < n - 1:
        if buf[i] != 0xFF:
            return None
        # Skip padding 0xFF bytes.
        while i < n and buf[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        marker = buf[i]
        i += 1
        if marker in (0xD8, 0xD9):  # SOI / EOI carry no payload.
            continue
        if i + 2 > n:
            return None
        segment_len = struct.unpack('>H', buf[i : i + 2])[0]
        if marker in sof_markers:
            if i + 7 > n:
                return None
            height, width = struct.unpack('>HH', buf[i + 3 : i + 7])
            return int(width), int(height)
        i += segment_len
    return None
