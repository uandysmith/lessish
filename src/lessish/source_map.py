"""Source Maps v3 generation.

The CSS walk itself lives once in `visitors.emit.CssEmitter`; this
module supplies the source-map machinery it plugs into.

* `VLQ` — base64-VLQ encoder.
* `SourceMapBuilder` — accumulates segments per output line, then
  serialises a Source Maps v3 JSON object.
* `_SourceMapper` — the `CssEmitter` mapper hook: turns each emitted
  node position into a segment at selector / declaration / at-rule
  granularity.
* `emit_with_map(...)` — public driver, returns `SourceMapResult`.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .ast_nodes import (
    AtRule,
    Node,
    Ruleset,
)
from .source import Source
from .visitors.emit import CssEmitter

_B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


class VLQ:
    """Base64-VLQ encoder per the Source Maps v3 spec.

    Numbers are first transformed to a sign-bit representation
    (`abs(n) << 1 | sign_bit`), then split into 5-bit chunks LSB-first
    with a continuation bit. Each chunk is base64-encoded.
    """

    @staticmethod
    def encode_int(n: int) -> str:
        if n < 0:
            vlq = ((-n) << 1) | 1
        else:
            vlq = n << 1
        out: list[str] = []
        while True:
            digit = vlq & 0b11111
            vlq >>= 5
            if vlq:
                digit |= 0b100000
            out.append(_B64[digit])
            if not vlq:
                break
        return ''.join(out)

    @staticmethod
    def encode_segment(values: list[int]) -> str:
        return ''.join(VLQ.encode_int(v) for v in values)


@dataclass(slots=True)
class _Segment:
    gen_col: int
    src_idx: int | None
    src_line: int | None
    src_col: int | None
    name_idx: int | None


@dataclass
class SourceMapBuilder:
    """Accumulates segments per output line and serializes a Source
    Maps v3 dict.

    `file` is the rendered CSS filename that ends up in the map's
    `file` field. `sources` are tracked in insertion order; the
    integer index returned by `add_source` becomes the source-index
    in every segment recorded against that source.
    """

    file: str
    source_root: str = ''
    _sources: list[str] = field(default_factory=list)
    _sources_content: list[str | None] = field(default_factory=list)
    _names: list[str] = field(default_factory=list)
    _lines: list[list[_Segment]] = field(default_factory=list)

    def add_source(self, filename: str, content: str | None) -> int:
        for i, existing in enumerate(self._sources):
            if existing == filename:
                # Promote `None` content to actual content if we now have it.
                if self._sources_content[i] is None and content is not None:
                    self._sources_content[i] = content
                return i
        self._sources.append(filename)
        self._sources_content.append(content)
        return len(self._sources) - 1

    def add_name(self, name: str) -> int:
        for i, existing in enumerate(self._names):
            if existing == name:
                return i
        self._names.append(name)
        return len(self._names) - 1

    def add_segment(
        self,
        out_line: int,
        out_col: int,
        src_idx: int | None,
        src_line: int | None,
        src_col: int | None,
        name_idx: int | None = None,
    ) -> None:
        while len(self._lines) <= out_line:
            self._lines.append([])
        line = self._lines[out_line]
        seg = _Segment(out_col, src_idx, src_line, src_col, name_idx)
        # Last-write-wins when two segments share an output column.
        if line and line[-1].gen_col == out_col:
            line[-1] = seg
        else:
            line.append(seg)

    def build_mappings(self) -> str:
        # Per the v3 spec: source_index / source_line / source_col /
        # name_index are emitted as deltas across the whole map;
        # generated column resets at each `;` (output line).
        out: list[str] = []
        prev_src = 0
        prev_src_line = 0
        prev_src_col = 0
        prev_name = 0
        for line_idx, segments in enumerate(self._lines):
            if line_idx > 0:
                out.append(';')
            prev_gen_col = 0
            for i, seg in enumerate(segments):
                if i > 0:
                    out.append(',')
                gen_col = seg.gen_col
                values = [gen_col - prev_gen_col]
                prev_gen_col = gen_col
                if seg.src_idx is not None:
                    assert seg.src_line is not None and seg.src_col is not None
                    values.append(seg.src_idx - prev_src)
                    values.append(seg.src_line - prev_src_line)
                    values.append(seg.src_col - prev_src_col)
                    prev_src = seg.src_idx
                    prev_src_line = seg.src_line
                    prev_src_col = seg.src_col
                    if seg.name_idx is not None:
                        values.append(seg.name_idx - prev_name)
                        prev_name = seg.name_idx
                out.append(VLQ.encode_segment(values))
        return ''.join(out)

    def build(self, *, include_sources_content: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            'version': 3,
            'file': self.file,
            'sources': list(self._sources),
            'names': list(self._names),
            'mappings': self.build_mappings(),
        }
        if self.source_root:
            result['sourceRoot'] = self.source_root
        if include_sources_content:
            # less.js emits `null` for sources whose content we don't have;
            # the JSON shape is an array aligned 1:1 with `sources`.
            result['sourcesContent'] = list(self._sources_content)
        return result

    def build_json(self, *, include_sources_content: bool) -> str:
        # less.js emits compact JSON (no extra spaces).
        return json.dumps(
            self.build(include_sources_content=include_sources_content),
            separators=(',', ':'),
            ensure_ascii=False,
        )


def _node_source(node: Node, root: Source) -> Source:
    """Pick the Source for `node`'s location lookup.

    `@import`-resolved nodes carry a `Node._source` field pointing at
    the sub-file's Source (see `_tag_source` in importer.py). Untagged
    nodes (default `_source=None`) belong to the entry-point source.
    """
    sub = node._source
    return sub if isinstance(sub, Source) else root


class _SourceMapper:
    """Records Source Maps v3 segments for `CssEmitter`.

    `CssEmitter` calls `record(node, out_line, out_col)` at each mappable
    node (selector head, declaration, at-rule head, comment). Here we map
    that output position to the node's source position; `@import`-resolved
    nodes carry their sub-file's Source via `Node._source` (see
    `_node_source`), untagged nodes belong to the entry-point Source.
    """

    def __init__(self, builder: SourceMapBuilder, root_source: Source) -> None:
        self._builder = builder
        self._root_source = root_source

    def record(self, node: Node, out_line: int, out_col: int) -> None:
        idx = getattr(node, 'index', None)
        if idx is None:
            return
        src = _node_source(node, self._root_source)
        loc = src.location_at(idx)
        # less.js uses POSIX-style separators in source paths.
        src_name = src.filename.replace('\\', '/')
        src_idx = self._builder.add_source(src_name, None)
        self._builder.add_segment(
            out_line=out_line,
            out_col=out_col,
            src_idx=src_idx,
            src_line=loc.line - 1,  # source maps are 0-indexed
            src_col=loc.column - 1,
        )


@dataclass(frozen=True)
class SourceMapResult:
    """Bundle of artefacts produced when source-map generation is on.

    `css` is the rendered CSS, including the trailing
    `/*# sourceMappingURL=… */` annotation (unless suppressed via
    `disableSourcemapAnnotation`). `map_json` is the serialized v3
    map. `annotation_url` is the URL embedded in the annotation
    (informational — `None` when annotation was suppressed).
    """

    css: str
    map_json: str
    annotation_url: str | None


def _resolve_opts(opts: bool | dict[str, Any]) -> dict[str, Any]:
    if opts is True:
        return {}
    if isinstance(opts, dict):
        return opts
    raise TypeError(f'source_map must be True or dict, got {type(opts).__name__}')


def _apply_basepath_rootpath(src_path: str, basepath: str, rootpath: str) -> str:
    """less.js: `sourceMapBasepath` strips a leading path prefix from
    each `sources[]` entry; `sourceMapRootpath` prepends a URL prefix.
    Operating on POSIX-style paths to mirror less.js."""
    path = src_path
    if basepath:
        bp = basepath.replace('\\', '/').rstrip('/')
        if path == bp:
            path = ''
        elif path.startswith(bp + '/'):
            path = path[len(bp) + 1 :]
    if rootpath:
        rp = rootpath.replace('\\', '/')
        path = rp + path if path else rp.rstrip('/')
    return path


def _resolve_annotation_url(opts: dict[str, Any], filename: str, map_json: str) -> str | None:
    """Compute the URL embedded in the `/*# sourceMappingURL=… */`
    annotation, honouring less.js's option matrix:

    * `disableSourcemapAnnotation: True` → return None (no annotation).
    * `sourceMapFileInline: True` → embed map as data URI.
    * `sourceMapURL: str` → use that string verbatim.
    * default → `<input stem>.css.map`.
    """
    if opts.get('disableSourcemapAnnotation'):
        return None
    if opts.get('sourceMapFileInline'):
        payload = base64.b64encode(map_json.encode('utf-8')).decode('ascii')
        return 'data:application/json;base64,' + payload
    explicit = opts.get('sourceMapURL')
    if isinstance(explicit, str):
        return explicit
    stem = PurePosixPath(filename.replace('\\', '/')).stem or 'input'
    return stem + '.css.map'


def emit_with_map(
    root: Ruleset,
    source: Source,
    opts: bool | dict[str, Any],
    *,
    filename: str,
    compress: bool,
    strict_units: bool,
    neutralize_escape: bool = False,
    max_output_size: int | None = None,
) -> SourceMapResult:
    """Render `root` to CSS while building a Source Maps v3 map.

    `source` is the entry-point Source (used as the default for nodes
    that aren't tagged with their own imported source). `filename` is
    the *output* CSS filename used in the map's `file` field and as
    the default annotation URL stem.

    The CSS string returned includes the trailing
    `/*# sourceMappingURL=… */` annotation (or its data-URI form for
    `sourceMapFileInline`). It does NOT include the caller's banner —
    the caller is responsible for prepending it.
    """
    opts_dict = _resolve_opts(opts)

    # Compute the *map's* file field — the rendered CSS filename.
    map_file = PurePosixPath(filename.replace('\\', '/')).name
    if map_file.endswith('.less'):
        map_file = map_file[: -len('.less')] + '.css'

    builder = SourceMapBuilder(file=map_file)
    emitter = CssEmitter(
        compress=compress,
        strict_units=strict_units,
        neutralize_escape=neutralize_escape,
        max_output_size=max_output_size,
        mapper=_SourceMapper(builder, source),
    )
    emitter.emit_root(root)
    css_body = emitter.render()

    # Apply basepath/rootpath to the recorded sources after the walk,
    # since segments reference sources by index — manipulating the
    # display path doesn't change the index.
    basepath = opts_dict.get('sourceMapBasepath') or ''
    rootpath = opts_dict.get('sourceMapRootpath') or ''
    if basepath or rootpath:
        builder._sources = [_apply_basepath_rootpath(s, basepath, rootpath) for s in builder._sources]
    # `sourceMapInputFilename` overrides sources[0] specifically — rare,
    # but less.js exposes it for tooling that wants to pin the entry name.
    override = opts_dict.get('sourceMapInputFilename')
    if override and builder._sources:
        builder._sources[0] = override

    include_content = bool(opts_dict.get('outputSourceFiles'))
    if include_content:
        # Populate `sourcesContent` for every source seen during the
        # walk (entry-point plus anything reachable through
        # `node._source`). After basepath/rootpath rewriting the names
        # in `_sources` may not match the raw filenames, so fall back
        # to basename matching when full-path lookup misses.
        text_by_filename: dict[str, str] = {source.filename.replace('\\', '/'): source.text}
        _collect_source_texts(root, text_by_filename)
        new_content: list[str | None] = []
        for src_name in builder._sources:
            text = text_by_filename.get(src_name)
            if text is None:
                base = PurePosixPath(src_name).name
                for k, v in text_by_filename.items():
                    if PurePosixPath(k).name == base:
                        text = v
                        break
            new_content.append(text)
        builder._sources_content = new_content

    map_json = builder.build_json(include_sources_content=include_content)
    annotation_url = _resolve_annotation_url(opts_dict, filename, map_json)
    css = css_body
    if annotation_url is not None:
        css = css + '/*# sourceMappingURL=' + annotation_url + ' */'
    return SourceMapResult(css=css, map_json=map_json, annotation_url=annotation_url)


def _collect_source_texts(root: Ruleset, into: dict[str, str]) -> None:
    """Walk the tree collecting `_source.filename → _source.text` for
    every node that carries an imported source. Used for
    `outputSourceFiles` so `sourcesContent` reflects the @import'd
    files as well as the entry point."""

    def visit(n: Node) -> None:
        sub = n._source
        if isinstance(sub, Source):
            key = sub.filename.replace('\\', '/')
            if key not in into:
                into[key] = sub.text
        if isinstance(n, Ruleset):
            for r in n.rules:
                visit(r)
        elif isinstance(n, AtRule) and n.body is not None:
            for r in n.body:
                visit(r)

    visit(root)
