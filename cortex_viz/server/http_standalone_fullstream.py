"""GET /api/graph/full/stream — the durable snapshot as NDJSON frames.

Why this endpoint exists: the single-document ``/api/graph/full`` response
crossed ~1.17 GB decompressed (2026-07-02, 278,557 nodes / 5,526,064
edges). A browser cannot ``response.json()`` a body that size — V8 refuses
the backing string — so the unified galaxy and the /brain view silently
fell back to the progressive build path, which sits behind a multi-hour
DrL bake on a fresh server: "the graph does not load". Same totality,
different delivery: many small frames a client ingests through a bounded
queue (ui/unified/js/graph_stream_loader.js).

Wire shape (``application/x-ndjson``, one JSON document per line)::

    {"node_total":N,"edge_total":E,...}     header — totals first, so the
                                            client can size its progress
    {"nodes":[...]}                         repeated, ≤ ~1 MB per line
    {"edges":[...]}                         repeated
    {"meta":{...}}                          single (position varies by format)
    {"done":true}                           terminator

Serving cost by stored format:

* ``ndjson.v1`` — pure decompress-and-forward of the stored frames; no
  parsing at all (the writer framed them — snapshot_pg_store).
* ``json.v1`` (legacy row) — split on the fly by
  ``shared.json_stream_split`` (~12–15 MB/s, so ~90 s for the current
  corpus, streamed progressively). One-time compat path until the next
  build persists ndjson.v1.

The response is identity-encoded with ``Connection: close`` (the body ends
when the socket does) — with chunked framing unavailable from
BaseHTTPRequestHandler, close-delimiting is the HTTP/1.1-correct way to
stream an unknown length. Localhost-only traffic; gzip re-encode would buy
nothing but CPU.
"""

from __future__ import annotations

import gzip
import io
import sys
import time

# Frames from a legacy json.v1 row are coalesced to this floor so a
# nested-element-heavy section doesn't degrade into thousands of tiny
# lines. ~512 KB parses in a few ms client-side while keeping frame
# overhead negligible (same order as the writer's own frames).
_MIN_LINE_BYTES = 512 * 1024

_READ_CHUNK = 1 << 20  # 1 MiB decompressed per read


def _write_line(handler, line: bytes) -> bool:
    """Write one NDJSON line; False when the client disconnected."""
    try:
        handler.wfile.write(line)
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


def _stream_ndjson_row(handler, payload_gzip: bytes) -> bool:
    """Forward stored ndjson.v1 frames verbatim (decompress only)."""
    gz = gzip.GzipFile(fileobj=io.BytesIO(payload_gzip))
    while True:
        chunk = gz.read(_READ_CHUNK)
        if not chunk:
            return True
        if not _write_line(handler, chunk):
            return False


def _stream_legacy_row(handler, snap: dict) -> bool:
    """Split a legacy json.v1 single-document row into frames on the fly."""
    from cortex_viz.shared.json_stream_split import iter_snapshot_segments

    header = (
        b'{"node_total":' + str(snap["node_count"]).encode()
        + b',"edge_total":' + str(snap["edge_count"]).encode() + b"}\n"
    )
    if not _write_line(handler, header):
        return False

    gz = gzip.GzipFile(fileobj=io.BytesIO(snap["payload_gzip"]))

    def _chunks():
        while True:
            chunk = gz.read(_READ_CHUNK)
            if not chunk:
                return
            yield chunk

    pending: list[bytes] = []
    pending_bytes = 0
    pending_section = ""

    def _flush() -> bool:
        nonlocal pending, pending_bytes
        if not pending:
            return True
        line = (
            b'{"' + pending_section.encode() + b'":['
            + b",".join(pending) + b"]}\n"
        )
        pending, pending_bytes = [], 0
        return _write_line(handler, line)

    for section, segment in iter_snapshot_segments(_chunks()):
        if section == "meta":
            if not _flush():
                return False
            if not _write_line(handler, b'{"meta":' + segment + b"}\n"):
                return False
            continue
        if section != pending_section:
            if not _flush():
                return False
            pending_section = section
        pending.append(segment)
        pending_bytes += len(segment)
        if pending_bytes >= _MIN_LINE_BYTES and not _flush():
            return False
    return _flush()


def _iter_ndjson_lines(payload_gzip: bytes):
    """Yield stored NDJSON frames one line at a time (decompress only)."""
    gz = gzip.GzipFile(fileobj=io.BytesIO(payload_gzip))
    tail = b""
    while True:
        chunk = gz.read(_READ_CHUNK)
        if not chunk:
            break
        tail += chunk
        lines = tail.split(b"\n")
        tail = lines.pop()
        yield from lines
    if tail:
        yield tail


def serve_full_document_from_ndjson(handler, snap: dict) -> None:
    """Reassemble an ndjson.v1 row into the legacy single-JSON document.

    Serves the exact ``{"nodes":[...],"edges":[...],"meta":{...}}`` shape
    ``/api/graph/full`` has always returned, by stripping each stored
    frame's wrapper (``{"nodes":[`` … ``]}``) and splicing the array bodies
    — byte-level string ops, no JSON parsing. Identity-encoded with
    ``Connection: close`` (length unknown up front).
    """
    import json as _json

    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "max-age=30")
    handler.send_header("Connection", "close")
    handler.send_header("X-Graph-Node-Count", str(snap["node_count"]))
    handler.send_header("X-Graph-Edge-Count", str(snap["edge_count"]))
    handler.end_headers()

    meta_raw = b"{}"
    section = ""  # "" → "nodes" → "edges"
    for line in _iter_ndjson_lines(snap["payload_gzip"]):
        if line.startswith(b'{"nodes":['):
            body = line[len(b'{"nodes":[') : -len(b"]}")]
            new_section = "nodes"
        elif line.startswith(b'{"edges":['):
            body = line[len(b'{"edges":[') : -len(b"]}")]
            new_section = "edges"
        else:
            # Header frame — carries meta (and totals, already in headers).
            try:
                meta_raw = _json.dumps(
                    _json.loads(line).get("meta") or {}, separators=(",", ":")
                ).encode()
            except ValueError:
                pass
            continue
        prefix = b""
        if new_section != section:
            prefix = (
                b'{"nodes":[' if new_section == "nodes"
                else (b'],"edges":[' if section == "nodes" else b'{"nodes":[],"edges":[')
            )
            section = new_section
        elif body:
            prefix = b","
        if not _write_line(handler, prefix + body):
            return
    closer = {
        "": b'{"nodes":[],"edges":[],"meta":' + meta_raw + b"}",
        "nodes": b'],"edges":[],"meta":' + meta_raw + b"}",
        "edges": b'],"meta":' + meta_raw + b"}",
    }[section]
    _write_line(handler, closer)


def serve_graph_full_stream(handler, store) -> None:
    """GET /api/graph/full/stream — see module docstring for the wire shape."""
    from cortex_viz.infrastructure import snapshot_pg_store

    try:
        snap = snapshot_pg_store.read_latest_snapshot(store)
    except Exception as e:  # pragma: no cover - defensive
        from cortex_viz.server.http_standalone_response import send_json_error

        send_json_error(handler, e)
        return
    if snap is None:
        body = b'{"status":"warming","reason":"no_snapshot"}'
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.send_header("Cache-Control", "max-age=30")
    handler.send_header("Connection", "close")
    handler.send_header("X-Graph-Node-Count", str(snap["node_count"]))
    handler.send_header("X-Graph-Edge-Count", str(snap["edge_count"]))
    handler.end_headers()

    t0 = time.monotonic()
    if snap.get("format") == snapshot_pg_store.FORMAT_NDJSON_V1:
        ok = _stream_ndjson_row(handler, snap["payload_gzip"])
    else:
        ok = _stream_legacy_row(handler, snap)
    if ok:
        _write_line(handler, b'{"done":true}\n')
    print(
        f"[cortex] full-graph stream ({snap.get('format')}) "
        f"{'completed' if ok else 'client dropped'} in "
        f"{time.monotonic() - t0:.1f}s",
        file=sys.stderr,
    )
