"""Server-Sent Events endpoint for /api/graph/events.

Split out of ``http_standalone_endpoints`` (500-line limit, §4.1) — this
was the largest single concern in that module (7 functions, ~170 lines)
and was already internally cohesive: cursor resolution, the
replay-then-tail write loop, and the thin request handler that wires
them together.
"""

from __future__ import annotations


def _resolve_sse_since(handler) -> int:
    """Resolve the SSE replay cursor for /api/graph/events.

    Honours ``Last-Event-ID`` for resume after a flaky connection (spec:
    the value is the ``id:`` of the last event the client saw; we advance
    past it on resume). ``?since=N`` is an additional curl-friendly
    fallback — whichever cursor is larger wins.
    """
    from urllib.parse import parse_qs, urlparse

    last_id_header = (
        handler.headers.get("Last-Event-ID")
        or handler.headers.get("Last-Event-Id")
        or ""
    )
    since = 0
    try:
        since = int(last_id_header) + 1 if last_id_header else 0
    except ValueError:
        since = 0
    qs = parse_qs(urlparse(handler.path).query)
    if "since" in qs:
        try:
            since = max(since, int(qs["since"][0]))
        except (ValueError, IndexError):
            pass
    return since


def _write_sse_batch_events(handler, stream, cursor: int) -> tuple[int, bool]:
    """Flush ``stream``'s events since ``cursor``; returns (new_cursor, ok).

    ``ok=False`` means the client disconnected (BrokenPipe /
    ConnectionReset) and the caller must stop the stream immediately.
    """
    from cortex_viz.server.graph_event_stream import format_event

    for idx, event in stream.subscribe(since=cursor, timeout=15.0):
        try:
            handler.wfile.write(format_event(idx, event))
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return cursor, False
        cursor = idx + 1
    return cursor, True


def _write_sse_done(handler) -> None:
    """Write the terminal ``done`` SSE event once the build has finished."""
    from cortex_viz.server.graph_event_stream import format_done
    from cortex_viz.server.http_standalone_graph import get_build_progress

    prog = get_build_progress()
    try:
        handler.wfile.write(
            format_done(
                total_nodes=prog.get("node_count", 0),
                total_edges=prog.get("edge_count", 0),
            )
        )
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass


def _stream_sse_batches(handler, stream, cursor: int) -> None:
    """Replay-then-tail loop writing SSE batch/done/heartbeat events.

    ``stream.subscribe()`` returns on close-and-drained OR on a 15 s idle
    timeout. On idle timeout we emit an SSE comment (heartbeat) and
    re-subscribe from where we left off, so the connection stays open
    across long pauses (the source-loading phase is ~15-20 s of silence
    before the first batch). Loop exits cleanly when (a) the stream is
    closed and drained, or (b) the client disconnects (BrokenPipe).
    """
    from cortex_viz.server.graph_event_stream import format_heartbeat

    while True:
        cursor, ok = _write_sse_batch_events(handler, stream, cursor)
        if not ok:
            return

        # Build finished AND we've drained every event.
        s = stream.stats()
        if s.get("closed") and cursor >= s.get("count", 0):
            _write_sse_done(handler)
            return

        # Idle timeout — keep the connection alive with a comment.
        # If the client is gone, the write fails and we exit.
        try:
            handler.wfile.write(format_heartbeat())
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def _send_sse_headers(handler) -> None:
    """Write the 200 + SSE header set for /api/graph/events."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")  # disable proxy buffering
    handler.end_headers()


def _write_sse_error(handler, exc: Exception) -> None:
    """Best-effort SSE error event on an already-started chunked response.

    Writing to a possibly-broken pipe is fraught; log-and-close semantics
    only — never raises.
    """
    try:
        handler.wfile.write(
            f"event: error\ndata: {type(exc).__name__}: {exc}\n\n".encode()
        )
        handler.wfile.flush()
    except Exception:
        pass


def serve_graph_events(handler, store=None) -> None:
    """GET /api/graph/events — Server-Sent Events stream of build batches.

    The build worker pushes per-source batches onto an in-memory event
    queue (see ``graph_event_stream``). This handler streams them to a
    single browser connection in real time so the user watches the
    graph grow as the builder produces nodes — first source within a
    second, full graph fills in behind it. No precomputed snapshot is
    required for this to work; it's the live-build channel.

    Wire format (text/event-stream):
        event: batch
        id: <buffer index>
        data: {"label":..,"nodes":[...],"edges":[...],"off":..,"n_total":..}

        event: done
        data: {"total_nodes":N,"total_edges":E}

    The client (``ui/unified/js/graph_event_stream.js``) parses each
    ``batch`` event and calls ``JUG.appendGraphDelta(nodes, edges)``.
    appendGraphDelta dedups by id, so reconnect-and-replay is safe.

    Lazy-kicks the build (ensure_build_started) so opening the SSE
    stream on a cold cache starts the pipeline producing events.
    """
    from cortex_viz.server.graph_event_stream import get_stream
    from cortex_viz.server.http_standalone_graph import ensure_build_started

    # A held SSE stream is a live client: without this, Chrome freezing a
    # background tab stops the 30s stats polls, the idle watchdog sees no
    # request arrivals, and the server shuts down UNDER the open page
    # (2026-06-10 "AST and chain fail" — the port was simply dead).
    from cortex_viz.server.http_standalone_state import (
        stream_closed,
        stream_opened,
    )

    since = _resolve_sse_since(handler)
    stream_opened()
    try:
        ensure_build_started(store)
        _send_sse_headers(handler)
        _stream_sse_batches(handler, get_stream(), since)
    except Exception as e:
        _write_sse_error(handler, e)
    finally:
        stream_closed()
