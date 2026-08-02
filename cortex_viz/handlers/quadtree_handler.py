"""GET /api/quadtree — gzipped Arrow IPC of every node's (id, x, y, kind).

The client builds a quadtree (e.g. flatbush) from this payload to
resolve hover/click locally in O(log N) without a server roundtrip.
``id`` and ``kind`` are dictionary-encoded so the wire size is
dominated by two Float32 columns at 1M nodes ≈ 8 MB raw / ~3-4 MB
gzipped.

Constant-memory invariant (harbor Phase C6)
--------------------------------------------
The response is streamed end-to-end so peak RAM is O(chunk_size), not
O(node_count). The plumbing is a chain of file-like wrappers, each
holding at most one chunk + the gzip window:

    record batch  ->  ipc stream writer
                  ->  pa.PythonFile
                  ->  gzip.GzipFile (compresslevel=6)
                  ->  _ChunkWriter (HTTP/1.1 chunked frames)
                  ->  handler.wfile (socket)

A ``flush`` after every batch pushes one chunk's worth of gzip output
through the chain and out the socket before the next chunk is read from
PG. The previous implementation buffered the WHOLE Arrow frame in a
``pa.BufferOutputStream`` and then made a second full copy via
``gzip.compress`` — peak ≈ 2× payload, growing without bound in node
count. That violated the same invariant the harbor plan removes at every
other read boundary.

Protocol: the standalone server sets ``protocol_version = "HTTP/1.1"``
(see ``http_standalone._build_unified_handler``), so we use
``Transfer-Encoding: chunked`` — the same framing the live layout SSE
stream (``graph_stream.serve``) already uses via
``layout_authority_wire.chunk_wrap`` / ``format_terminator``. No
Content-Length is possible because the gzipped length is unknown until
the last byte is compressed.

Empty-layout handling: with streaming we must choose 200-vs-503 BEFORE
sending headers. We peek the first chunk (``next(iterator)``); if the
iterator is exhausted the layout is empty and we send 503 ``no_layout``.
Otherwise we send 200 + the peeked chunk + the rest.
"""

from __future__ import annotations

import gzip
import io
import sys

from cortex_viz.server.http_standalone_response import (
    send_json_capability_unavailable,
    send_json_warming,
)
from cortex_viz.server.layout_authority_wire import chunk_wrap, format_terminator

# gzip compression level. Pre-existing (not retuned) — the original
# handler used compresslevel=6 against gzip.compress.
# source: prior quadtree_handler implementation (git history); level 6 is
# also the zlib/gzip stdlib default (Python docs, zlib.Z_DEFAULT_COMPRESSION).
_GZIP_LEVEL = 6


class _ChunkWriter(io.RawIOBase):
    """File-like sink that wraps each write in an HTTP/1.1 chunked frame.

    Pre:
      - ``wfile`` is the handler's socket buffer; headers (including
        ``Transfer-Encoding: chunked``) have already been sent.
    Post:
      - every non-empty ``write(b)`` emits exactly one ``chunk_wrap(b)``
        frame and flushes, so bytes leave the process incrementally.
      - the zero-length terminator is NOT emitted here; the caller writes
        ``format_terminator()`` after the gzip trailer (close order).

    Empty writes are dropped: ``chunk_wrap`` rejects empty payloads, and
    GzipFile issues a final empty-ish flush on close that must not emit a
    premature zero chunk (which would terminate the stream early).
    """

    def __init__(self, wfile) -> None:
        super().__init__()
        self._wfile = wfile

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        n = len(b)
        if n:
            self._wfile.write(chunk_wrap(bytes(b)))
            self._wfile.flush()
        return n


def _stream_chunks(handler, schema, first_chunk, rest, pa, ipc) -> None:
    """Stream ``first_chunk`` then ``rest`` as a gzipped chunked Arrow IPC.

    Pre:
      - 200 + Transfer-Encoding:chunked headers already sent.
      - ``first_chunk`` is a non-empty list of ``(id, x, y, kind)`` tuples.
      - ``rest`` is the remaining iterator of such lists.
    Post:
      - one Arrow record batch per chunk is written through
        gzip -> chunked frames; bytes leave the socket per batch.
      - close order is arrow-writer -> gzip(trailer) -> zero terminator.
    Invariant:
      - at most one chunk's record batch + the gzip window is resident.
    """
    sink = _ChunkWriter(handler.wfile)
    gz = gzip.GzipFile(mode="wb", compresslevel=_GZIP_LEVEL, fileobj=sink)
    pf = pa.PythonFile(gz, mode="w")
    writer = ipc.new_stream(pf, schema)
    try:
        # itertools.chain would re-import; a tiny generator keeps the
        # peeked first chunk first without materializing ``rest``.
        def _all():
            yield first_chunk
            yield from rest

        for chunk in _all():
            batch = pa.record_batch(
                {
                    "id": pa.array([r[0] for r in chunk]).dictionary_encode(),
                    "x": pa.array([r[1] for r in chunk], type=pa.float32()),
                    "y": pa.array([r[2] for r in chunk], type=pa.float32()),
                    "kind": pa.array([r[3] for r in chunk]).dictionary_encode(),
                },
                schema=schema,
            )
            writer.write_batch(batch)
            # Push this batch through gzip and out the socket before the
            # next chunk is read from PG — this is what makes peak RAM
            # O(chunk), not O(node_count).
            pf.flush()
            gz.flush()
    finally:
        # Close order is load-bearing: the Arrow writer must finalize the
        # IPC stream into gzip, THEN gzip writes its trailer, THEN we emit
        # the zero-length chunk terminator. Reordering corrupts the frame.
        writer.close()
        pf.flush()
        gz.close()
        handler.wfile.write(format_terminator())
        handler.wfile.flush()


def serve(handler, store) -> None:
    """Stream the full node layout as gzipped Arrow IPC, constant-memory.

    Pre:
      - ``handler`` is a BaseHTTPRequestHandler with HTTP/1.1 protocol.
      - ``store`` exposes the workflow_graph_layout table.
    Post:
      - 200 ``{"status":"unavailable","capability":"viz-tile"}`` when the
        optional extra is not installed — a supported configuration, so
        the client degrades to ``fallback`` instead of failing (#90), OR
      - 503 ``no_layout`` if the layout table is empty (decided before
        any 200 headers are sent), OR
      - 200 chunked gzipped Arrow stream of every (id, x, y, kind) row.
    """
    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc

        from cortex_viz.infrastructure import layout_pg_store
    except ImportError as exc:
        print(
            f"[cortex] /api/quadtree unavailable — viz-tile extra absent: {exc}",
            file=sys.stderr,
        )
        send_json_capability_unavailable(
            handler,
            capability="viz-tile",
            reason="extra_not_installed",
            fallback="/api/graph",
        )
        return

    schema = pa.schema(
        [
            ("id", pa.dictionary(pa.int32(), pa.string())),
            ("x", pa.float32()),
            ("y", pa.float32()),
            ("kind", pa.dictionary(pa.int32(), pa.string())),
        ]
    )

    # Peek the first chunk so emptiness is decided BEFORE headers go out.
    # Once we send 200 + Transfer-Encoding:chunked we can no longer switch
    # to a not-ready response — the response line is already committed.
    #
    # An empty layout table means the first build has not persisted
    # positions yet, so this is a "not ready" state (202), not a fault:
    # the client's self-healing path reads ``reason`` and drives
    # /api/recompute_layout (#90).
    chunks = layout_pg_store.iter_positions_chunked(store)
    try:
        first_chunk = next(chunks)
    except StopIteration:
        send_json_warming(handler, "no_layout")
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "application/vnd.apache.arrow.stream")
    handler.send_header("Content-Encoding", "gzip")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.send_header("Cache-Control", "max-age=60")
    handler.end_headers()

    _stream_chunks(handler, schema, first_chunk, chunks, pa, ipc)
