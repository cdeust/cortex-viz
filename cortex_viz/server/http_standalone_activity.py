"""Live session-activity endpoints (P0 spine ingest/replay + P3 blast-radius
trigger).

Split out of ``http_standalone_endpoints.py`` (which crossed the 500-line
project ceiling) — mirrors the existing precedent of splitting a cohesive
endpoint group into its own module (``http_standalone_endpoints_sankey``,
``http_standalone_wiki``, ``http_standalone_trace``). Owns:

* ``serve_activity_ingest`` — POST /api/activity
* ``serve_activity_stream`` — GET /api/activity/stream
* ``_trigger_impact``       — the P3 live blast-radius pass, fired from ingest

All response shaping flows through this module's own tiny JSON/SSE helpers
(HTTP boilerplate only — no business logic), matching
``http_standalone_response``'s role for the rest of the server.
"""

from __future__ import annotations

import json as _json
import sys as _sys
import threading as _threading
from urllib.parse import parse_qs, urlparse

# Serializes the live blast-radius passes — the AP bridge shares ONE event
# loop, so concurrent impact lookups would collide (relationship queries
# silently return 0). One pass at a time; a skipped edit re-triggers on the
# next save.
_impact_lock = _threading.Lock()


def _run_impact_pass(file_path: str) -> None:
    """The actual blast-radius pass, run on ``_trigger_impact``'s daemon
    thread. Never raises into the hook path — best-effort."""
    if not _impact_lock.acquire(blocking=False):
        return  # a pass is already running; the next edit re-triggers
    try:
        from cortex_viz.core.impact_graph import impact_to_graph
        from cortex_viz.server.activity_stream import stream as _astream
        from cortex_viz.server.trace_impact import impact_for_path

        imp = impact_for_path(file_path)
        if not imp:
            return
        frag = impact_to_graph(file_path, imp)
        if frag["nodes"] or frag["edges"]:
            _astream().emit("activity", frag["nodes"], frag["edges"])
            print(
                f"[cortex] live impact: {len(frag['nodes'])}N {len(frag['edges'])}E "
                f"for {file_path}",
                file=_sys.stderr,
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[cortex] live impact pass failed: {exc}", file=_sys.stderr)
    finally:
        _impact_lock.release()


def _trigger_impact(store, file_path: str) -> None:
    """P3 — spawn a NON-BLOCKING blast-radius pass for an edited file.

    Asks AP for the file's impact (``impact_for_path``), maps it to directional
    nodes/edges (``impact_graph.impact_to_graph``) hung off the same
    ``file:<hash>`` node the edit action points to (P4 node-unification —
    see ``core.activity_paths``), and emits them onto the live activity
    stream — so the graph shows what the change affects within a second of
    the save. Runs in a daemon thread so the ingest POST returns
    immediately; best-effort, never raises into the hook path.
    """
    _ = store  # accepted for call-site symmetry / future use; unused today
    if not file_path:
        return
    _threading.Thread(
        target=_run_impact_pass, args=(file_path,),
        name="cortex-impact-pass", daemon=True,
    ).start()


def _send_json(handler, status: int, payload: dict) -> None:
    body = _json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _maybe_trigger_impact(store, row: dict) -> None:
    """P3 fires only for a file edit/write — see ``_trigger_impact``'s
    docstring. AP needs a REAL filesystem path (target_id is now an opaque
    ``file:<hash>`` since P4 node-unification); the canonical absolute path
    survives in ``detail.path`` (set by
    ``core.activity_graph._file_action``) precisely for this."""
    if row.get("action") not in ("edit", "write") or row.get("target_kind") != "file":
        return
    real_path = (row.get("detail") or {}).get("path") or ""
    if real_path:
        _trigger_impact(store, real_path)


def serve_activity_ingest(handler, store) -> None:
    """POST /api/activity — ingest ONE captured Claude action (hook event).

    The producer is a Claude Code hook (``activity_capture.py``), not a
    browser — host-guarded to 127.0.0.1 but not same-origin. Body is the raw
    hook payload ``{tool_name, tool_input, tool_response, cwd, session_id,
    ts, event_type}``. We normalize it to the activity taxonomy, append it to
    the durable ``session_activity`` log, and emit its directional nodes/edges
    onto the live activity stream so every open ``/api/activity/stream``
    subscriber paints it within ~1 s. Fire-and-forget: a malformed or
    actionless event returns 204 and never errors the hook.
    """
    from cortex_viz.core.activity_graph import event_to_graph, normalize_event
    from cortex_viz.infrastructure import activity_store
    from cortex_viz.server.activity_stream import stream as _activity_stream

    try:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        event = _json.loads(raw or b"{}")
    except (ValueError, OSError):
        handler.send_response(400)
        handler.end_headers()
        return
    row = normalize_event(event)
    if row is None:
        handler.send_response(204)
        handler.end_headers()
        return
    try:
        new_id = activity_store.record_activity(store, row)
        row["seq"] = new_id
        frag = event_to_graph(row)
        _activity_stream().emit("activity", frag["nodes"], frag["edges"])
        _maybe_trigger_impact(store, row)
    except Exception as exc:  # pragma: no cover - defensive; never error the hook
        _send_json(handler, 200, {"ok": False, "error": str(exc)})
        return
    _send_json(handler, 200, {"ok": True, "id": new_id, "action": row["action"]})


def _replay_log(handler, store, since: int) -> bool:
    """Replay the durable ``session_activity`` log from ``since``. Returns
    ``False`` on client disconnect (caller stops immediately)."""
    from cortex_viz.core.activity_graph import event_to_graph
    from cortex_viz.infrastructure import activity_store
    from cortex_viz.server.graph_event_stream import format_event

    try:
        for row in activity_store.read_recent(store, since_id=since):
            frag = event_to_graph(row)
            handler.wfile.write(format_event(int(row["id"]), {
                "label": "activity", "nodes": frag["nodes"], "edges": frag["edges"],
            }))
        handler.wfile.flush()
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


def _write_or_stop(handler, payload: bytes) -> bool:
    """One SSE write; ``False`` signals the caller to stop (disconnect)."""
    try:
        handler.wfile.write(payload)
        handler.wfile.flush()
        return True
    except (BrokenPipeError, ConnectionResetError):
        return False


def _tail_live(handler, cursor: int) -> None:
    """Tail new emits from the in-process activity stream past ``cursor``
    (the replay's end) until the client disconnects. Never returns on a
    live connection — a session is open-ended."""
    from cortex_viz.server.activity_stream import stream as _activity_stream
    from cortex_viz.server.graph_event_stream import format_event, format_heartbeat

    astream = _activity_stream()
    while True:
        saw_any = False
        for idx, event in astream.subscribe(since=cursor, timeout=15.0):
            if not _write_or_stop(handler, format_event(idx, event)):
                return
            cursor = idx + 1
            saw_any = True
        if not saw_any and not _write_or_stop(handler, format_heartbeat()):
            return


def serve_activity_stream(handler, store) -> None:
    """GET /api/activity/stream — live SSE of session activity nodes/edges.

    Replay-then-tail: on connect, replays the durable ``session_activity`` log
    (``id > ?since``, default 0) so a fresh page paints the actions that
    already happened (``_replay_log``), then blocks tailing the in-process
    activity stream for new actions (``_tail_live``). Same wire format +
    client consumer (``appendGraphDelta``, dedup-by-id) as
    ``/api/graph/events``, so the live spine merges into the same graph. The
    stream is never server-closed (a session is open-ended); the loop exits
    only on client disconnect.
    """
    from cortex_viz.server.activity_stream import stream as _activity_stream
    from cortex_viz.server.http_standalone_state import stream_closed, stream_opened

    qs = parse_qs(urlparse(handler.path).query)
    since = 0
    if "since" in qs:
        try:
            since = int(qs["since"][0])
        except (ValueError, IndexError):
            since = 0

    stream_opened()
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        if not _replay_log(handler, store, since):
            return
        astream = _activity_stream()
        cursor = astream.stats().get("count", 0)
        _tail_live(handler, cursor)
    finally:
        stream_closed()


__all__ = ["serve_activity_ingest", "serve_activity_stream"]
