"""Incremental splitter for the full-graph snapshot JSON wire shape.

The durable snapshot (``snapshot_pg_store``) is ONE gzip(JSON) document:
``{"nodes":[...],"edges":[...],"meta":{...}}``. At the current corpus that
decompresses to ~1.17 GB (measured 2026-07-02, 278,557 nodes / 5,526,064
edges) — beyond what a browser can materialise through a single
``response.json()`` (V8 caps a string well below that), and too large to
``json.loads`` per request server-side without a multi-GB resident spike.

This module splits that document into SEGMENTS — comma-joined runs of
complete top-level array elements — without ever building the parsed tree.
A segment is a valid JSON array body: wrapping it in ``[...]`` parses, and
concatenating all segments of a section with ``,`` reproduces the original
array byte-for-byte. Peak memory is O(chunk + largest carried element).

Splitting at segment (not element) granularity is what makes it fast: one
C-level regex match consumes an entire run of flat elements, so the Python
loop executes per-segment (~1 MB), not per-element (~200 B). The tokenizer
only walks elements the flat fast-path rejects (nested object/array).

Local-reasoning justification (coding-standards §7.2 — custom parsing code):
isolated in this one module, pure function over an iterator of byte chunks,
tested against ``json.loads`` round-trips at hostile chunk boundaries
(tests/test_json_stream_split.py). The input shape is OUR OWN writer's
output (``snapshot_pg_store.write_snapshot`` — compact separators, key
order nodes → edges → meta), asserted at parse time, never guessed.
source (grammar): RFC 8259; strings are the only construct that can carry
structural bytes, and escape handling follows §7.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

# One token = a complete string literal (escape-aware) OR a single
# structural bracket. In the compact wire shape everything between tokens
# is commas, colons, numbers, and true/false/null — none of which can
# contain a quote or bracket byte.
_TOKEN = re.compile(rb'"(?:[^"\\]|\\.)*"|[{}\[\]]')

# A FLAT object (no nested object/array): content is complete string
# literals or non-structural bytes. A `}` inside a string is consumed by
# the string alternation and can never terminate the match; a nested
# `{`/`[` fails the element (the byte class excludes it and no structural
# `}` precedes it) — so an anchored match IS a complete valid element.
# The two alternatives are disjoint on their first byte, so the star is
# deterministic (linear time, no catastrophic backtracking).
_FLAT_EL = rb'\{(?:"(?:[^"\\]|\\.)*"|[^"{}\[\]])*\}'

# A run of consecutive flat elements — one C-level match consumes as many
# complete elements as the buffer holds. The matched span is verbatim
# comma-joined elements: exactly one wire segment.
_FLAT_RUN = re.compile(_FLAT_EL + rb"(?:," + _FLAT_EL + rb")*")

_PREFIX = b'{"nodes":['
_EDGES_SEP = b',"edges":['
_META_SEP = b',"meta":'


class SnapshotSplitError(ValueError):
    """The byte stream does not match the snapshot writer's wire shape."""


def _expect_prefix(buf: bytes, prefix: bytes) -> int:
    """Assert ``buf`` starts with ``prefix``; return the offset after it."""
    if not buf.startswith(prefix):
        raise SnapshotSplitError(
            f"snapshot wire shape mismatch: expected {prefix!r}, "
            f"got {bytes(buf[: len(prefix) + 16])!r}"
        )
    return len(prefix)


def _scan_array(buf: bytes) -> tuple[list[bytes], int, bool]:
    """Extract segments of complete elements from an array-body buffer.

    Returns ``(segments, consumed, closed)`` — ``consumed`` is the offset
    the caller may discard up to (start of any incomplete trailing element,
    re-scanned whole once more bytes arrive); ``closed`` is True when the
    array's terminating ``]`` was consumed.
    """
    segments: list[bytes] = []
    pos = 0
    while True:
        if buf[pos : pos + 1] == b",":
            pos += 1
        head = buf[pos : pos + 1]
        if head == b"]":
            return segments, pos + 1, True
        if head == b"":
            return segments, pos, False  # need more bytes
        if head != b"{":
            raise SnapshotSplitError(
                f"unexpected byte {head!r} at array level (offset {pos})"
            )
        m = _FLAT_RUN.match(buf, pos)
        if m:
            segments.append(buf[pos : m.end()])
            pos = m.end()
            continue
        end = _scan_one_nested(buf, pos)
        if end < 0:
            return segments, pos, False  # incomplete trailing element
        segments.append(buf[pos:end])
        pos = end


def _scan_one_nested(buf: bytes, start: int) -> int:
    """End offset of the complete element starting at ``start``, or -1.

    Token walk for elements the flat fast-path rejected (nested object /
    array, or an incomplete tail). A bare quote in an inter-token gap means
    the tail is inside an unterminated string — every token beyond it is
    phantom structure inside string content, so report incomplete and let
    the caller carry the element until more bytes arrive.
    """
    depth = 0
    pos = start
    for m in _TOKEN.finditer(buf, start):
        if buf.find(b'"', pos, m.start()) != -1:
            return -1
        tok = m.group()
        pos = m.end()
        if tok in (b"{", b"["):
            depth += 1
        elif tok in (b"}", b"]"):
            depth -= 1
            if depth == 0:
                return m.end()
    return -1


def _meta_inner(buf: bytes) -> bytes:
    """Strip ``,"meta":`` and the document's closing ``}`` from the tail."""
    inner = buf[_expect_prefix(buf, _META_SEP) : -1]
    if not inner or not buf.endswith(b"}"):
        raise SnapshotSplitError("malformed meta tail in snapshot stream")
    return inner


def iter_snapshot_segments(
    chunks: Iterable[bytes],
) -> Iterator[tuple[str, bytes]]:
    """Split a decompressed snapshot byte stream into wire segments.

    Yields ``("nodes", segment)`` for runs of the nodes array in order,
    then ``("edges", segment)`` likewise, then exactly one
    ``("meta", raw_json)``. Each segment is a verbatim byte slice holding
    one or more complete comma-joined elements: ``b"[" + segment + b"]"``
    parses as a JSON array, and joining a section's segments with ``,``
    reproduces the original array exactly.

    Raises ``SnapshotSplitError`` when the stream does not match the
    snapshot writer's shape (wrong prefix, truncated document).
    """
    buf = b""
    section = "prefix"  # prefix → nodes → edges_sep → edges → meta_tail
    for chunk in chunks:
        buf += chunk
        progressed = True
        while progressed:
            progressed = False
            if section == "prefix" and len(buf) >= len(_PREFIX):
                buf = buf[_expect_prefix(buf, _PREFIX) :]
                section = "nodes"
                progressed = True
            elif section in ("nodes", "edges"):
                segments, consumed, closed = _scan_array(buf)
                for seg in segments:
                    yield section, seg
                buf = buf[consumed:]
                if closed:
                    section = "edges_sep" if section == "nodes" else "meta_tail"
                    progressed = True
            elif section == "edges_sep" and len(buf) >= len(_EDGES_SEP):
                buf = buf[_expect_prefix(buf, _EDGES_SEP) :]
                section = "edges"
                progressed = True
            # meta_tail accumulates to end-of-stream; emitted below.
    if section != "meta_tail":
        raise SnapshotSplitError(
            f"truncated snapshot stream (stopped in section {section!r})"
        )
    yield "meta", _meta_inner(buf)
