"""Non-wiki endpoint helpers for the standalone HTTP server.

Owns:

* ``serve_sankey`` — /api/sankey dashboard query
* ``serve_graph`` / ``serve_discussions`` / ``serve_discussion_detail``
* ``serve_static`` — sandboxed static-file reader for ``/js/`` + ``/css/``
* ``serve_file_diff`` — thin delegate to ``http_file_diff``

All response shaping flows through ``http_standalone_response`` so the
HTTP boilerplate lives in one place.
"""

from __future__ import annotations

import re
from pathlib import Path

from cortex_viz.server.http_standalone_graph import (
    build_discussion_detail,
    build_discussions_response,
    get_graph_response,
)
from cortex_viz.server.http_standalone_response import (
    send_json_error,
    send_json_ok,
    send_plain_error,
)

# Sankey + HUD-stats endpoints were split into
# ``http_standalone_endpoints_sankey`` (500-line limit). Re-exported so
# ``from cortex_viz.server.http_standalone_endpoints import serve_sankey``
# / ``serve_stats`` (routes module) keep resolving.
from cortex_viz.server.http_standalone_endpoints_sankey import (  # noqa: F401
    serve_sankey,
    serve_stats,
)


def serve_graph(handler, store) -> None:
    """GET /api/graph — cached workflow graph or warming placeholder."""
    try:
        send_json_ok(handler, get_graph_response(store, handler.path))
    except Exception as e:
        send_json_error(handler, e)


def serve_graph_full(handler, store) -> None:
    """GET /api/graph/full — the COMPLETE graph from the durable PG snapshot.

    Serves the persisted full graph (every entity, AST symbol, memory, file,
    command + all edges — the README-hero view) directly from
    ``workflow_graph_snapshot``. Unlike ``/api/graph`` this does NOT read the
    volatile in-process build cache and never lazy-kicks a build, so it is
    stable across the build's rebuild loop: once a build has completed once,
    the snapshot is always available and identical.

    The stored payload is already gzip(JSON); it is streamed verbatim with
    ``Content-Encoding: gzip`` (the browser inflates transparently) — no
    server-side decode/re-encode. When no snapshot exists yet (no build has
    finished since install) returns 503 ``{"reason":"no_snapshot"}`` so the
    client falls back to the progressive ``/api/graph`` path.
    """
    try:
        from cortex_viz.infrastructure import snapshot_pg_store

        snap = snapshot_pg_store.read_latest_snapshot(store)
    except Exception as e:  # pragma: no cover - defensive
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
    payload = snap["payload_gzip"]
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Encoding", "gzip")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "max-age=30")
    handler.send_header("X-Graph-Node-Count", str(snap["node_count"]))
    handler.send_header("X-Graph-Edge-Count", str(snap["edge_count"]))
    handler.end_headers()
    handler.wfile.write(payload)


import threading as _threading

# Serializes the live blast-radius passes — the AP bridge shares ONE event
# loop, so concurrent impact lookups would collide (relationship queries
# silently return 0). One pass at a time; a skipped edit re-triggers on the
# next save.
_impact_lock = _threading.Lock()


def _trigger_impact(store, file_path: str) -> None:
    """P3 — spawn a NON-BLOCKING blast-radius pass for an edited file.

    Asks AP for the file's impact (``impact_for_path``), maps it to directional
    nodes/edges (``impact_graph.impact_to_graph``) hung off the same
    ``file:<path>`` node the edit action points to, and emits them onto the
    live activity stream — so the graph shows what the change affects within a
    second of the save. Runs in a daemon thread so the ingest POST returns
    immediately; best-effort, never raises into the hook path.
    """
    if not file_path:
        return

    def _run() -> None:
        import sys

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
                    file=sys.stderr,
                )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[cortex] live impact pass failed: {exc}", file=sys.stderr)
        finally:
            _impact_lock.release()

    _threading.Thread(
        target=_run, name="cortex-impact-pass", daemon=True
    ).start()


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
    import json as _json

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
        # P3 — a file edit/write fires a live blast-radius pass (AP impact →
        # directional edges into the same file node), non-blocking.
        if row.get("action") in ("edit", "write") and row.get("target_kind") == "file":
            _trigger_impact(store, (row.get("target_id") or "")[len("file:"):])
    except Exception as exc:  # pragma: no cover - defensive; never error the hook
        body = _json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    body = _json.dumps({"ok": True, "id": new_id, "action": row["action"]}).encode(
        "utf-8"
    )
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def serve_activity_stream(handler, store) -> None:
    """GET /api/activity/stream — live SSE of session activity nodes/edges.

    Replay-then-tail: on connect, replays the durable ``session_activity`` log
    (``id > ?since``, default 0) so a fresh page paints the actions that
    already happened, then blocks tailing the in-process activity stream for
    new actions. Same wire format + client consumer (``appendGraphDelta``,
    dedup-by-id) as ``/api/graph/events``, so the live spine merges into the
    same graph. The stream is never server-closed (a session is open-ended);
    the loop exits only on client disconnect.
    """
    from urllib.parse import parse_qs, urlparse

    from cortex_viz.core.activity_graph import event_to_graph
    from cortex_viz.infrastructure import activity_store
    from cortex_viz.server.activity_stream import stream as _activity_stream
    from cortex_viz.server.graph_event_stream import (
        format_event,
        format_heartbeat,
    )
    from cortex_viz.server.http_standalone_state import (
        stream_closed,
        stream_opened,
    )

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

        # Replay the durable log (id-keyed; node ids embed the id as seq, so
        # any overlap with the live tail dedups on the client).
        try:
            for row in activity_store.read_recent(store, since_id=since):
                frag = event_to_graph(row)
                handler.wfile.write(format_event(int(row["id"]), {
                    "label": "activity", "nodes": frag["nodes"],
                    "edges": frag["edges"],
                }))
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

        # Tail new emits from the in-process stream (start past its current
        # end so we only deliver actions captured AFTER connect).
        astream = _activity_stream()
        cursor = astream.stats().get("count", 0)
        while True:
            saw_any = False
            for idx, event in astream.subscribe(since=cursor, timeout=15.0):
                try:
                    handler.wfile.write(format_event(idx, event))
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                cursor = idx + 1
                saw_any = True
            if not saw_any:
                try:
                    handler.wfile.write(format_heartbeat())
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
    finally:
        stream_closed()


def serve_prd(handler, store=None) -> None:
    """GET /api/prd — PRD document/section nodes from discovered artifacts.

    The third bridge's view (prd-spec-generator). Returns
    ``{nodes, edges, available}`` from any ``prd-output/<run>/`` PRDs found on
    disk. ``available`` is False with an empty graph when no PRD has been
    generated yet (the stateless pipeline keeps no standing store) — the UI
    simply shows nothing, no error.
    """
    try:
        from cortex_viz.infrastructure import prd_bridge

        frag = prd_bridge.read_prd_graph()
        send_json_ok(
            handler,
            {
                "available": bool(frag["nodes"]),
                "nodes": frag["nodes"],
                "edges": frag["edges"],
                "meta": {"schema": "prd.v1", "source": "prd-spec-generator"},
            },
        )
    except Exception as e:
        send_json_error(handler, e)


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
    from urllib.parse import parse_qs, urlparse

    from cortex_viz.server.graph_event_stream import (
        format_done,
        format_event,
        format_heartbeat,
        get_stream,
    )
    from cortex_viz.server.http_standalone_graph import (
        ensure_build_started,
        get_build_progress,
    )

    # Honour Last-Event-ID for resume after a flaky connection. Spec
    # says the value is the ``id:`` of the last event the client saw;
    # we advance past it on resume.
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
    # Also allow ?since=N as a fallback (curl-friendly).
    qs = parse_qs(urlparse(handler.path).query)
    if "since" in qs:
        try:
            since = max(since, int(qs["since"][0]))
        except (ValueError, IndexError):
            pass

    # A held SSE stream is a live client: without this, Chrome freezing a
    # background tab stops the 30s stats polls, the idle watchdog sees no
    # request arrivals, and the server shuts down UNDER the open page
    # (2026-06-10 "AST and chain fail" — the port was simply dead).
    from cortex_viz.server.http_standalone_state import (
        stream_closed,
        stream_opened,
    )

    stream_opened()
    try:
        ensure_build_started(store)

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")  # disable proxy buffering
        handler.end_headers()

        stream = get_stream()

        # Replay-then-tail loop. subscribe() returns on close-and-drained
        # OR on a 15 s idle timeout. On idle timeout we emit an SSE
        # comment (heartbeat) and re-subscribe from where we left off,
        # so the connection stays open across long pauses (the source-
        # loading phase is ~15–20 s of silence before the first batch).
        # Loop exits cleanly when (a) the stream is closed and drained,
        # or (b) the client disconnects (BrokenPipe).
        cursor = since
        while True:
            saw_any = False
            for idx, event in stream.subscribe(since=cursor, timeout=15.0):
                try:
                    handler.wfile.write(format_event(idx, event))
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                cursor = idx + 1
                saw_any = True

            s = stream.stats()
            if s.get("closed") and cursor >= s.get("count", 0):
                # Build finished AND we've drained every event.
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
                return

            # Idle timeout — keep the connection alive with a comment.
            # If the client is gone, the write fails and we exit.
            try:
                handler.wfile.write(format_heartbeat())
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            # If we saw nothing AND the stream is still open, loop
            # back into subscribe() to wait for more. This is the
            # source-loading gap (no batches for ~15–20 s while PG
            # queries run).
            if not saw_any:
                continue
    except Exception as e:
        # Best-effort error reporting on an already-started chunked
        # response is fraught; log and close.
        try:
            handler.wfile.write(
                f"event: error\ndata: {type(e).__name__}: {e}\n\n".encode()
            )
            handler.wfile.flush()
        except Exception:
            pass
    finally:
        stream_closed()


def serve_graph_slice(handler) -> None:
    """GET /api/graph/slice?offset=N&limit=M — full-fidelity page of the
    cumulative graph cache.

    Complete-across-continuation: pages slice both nodes and edges by
    [offset : offset+limit] with totals and ``done``; the union of all
    pages equals the full cache (never a lossy cap — user direction
    2026-06-12). Primary consumer: the ``query_workflow_graph`` MCP
    handler in the Cortex server process, which drains pages from the
    LIVE viz instance instead of rebuilding the graph per call.
    """
    from urllib.parse import parse_qs, urlparse

    from cortex_viz.server.http_standalone_graph import get_graph_slice

    try:
        qs = parse_qs(urlparse(handler.path).query)

        def _int(name: str, default: int) -> int:
            try:
                return int(qs[name][0])
            except (KeyError, IndexError, ValueError):
                return default

        send_json_ok(handler, get_graph_slice(_int("offset", 0), _int("limit", 20000)))
    except Exception as e:
        send_json_error(handler, e)


def serve_graph_progress(handler, store=None) -> None:
    """GET /api/graph/progress — background-build progress snapshot.

    Also lazily kicks the background build if it hasn't started (see
    ``ensure_build_started``): the graph-tab poller hits this endpoint,
    so this is what starts the build when the user opens the Graph view.
    """
    from cortex_viz.server.http_standalone_graph import (
        ensure_build_started,
        get_build_progress,
    )

    try:
        ensure_build_started(store)
        send_json_ok(handler, get_build_progress())
    except Exception as e:
        send_json_error(handler, e)


def serve_graph_phase(handler) -> None:
    """GET /api/graph/phase?name=<L0|L1|…|L6:proj|L6_CROSS>

    Returns only the nodes + edges produced by that phase plus its
    ``ready`` flag and dependency list. The client appends the
    payload to its live scene when ``ready=true``; until then the
    client skips it (guarantees it never appends an edge whose
    endpoint is in a later phase).

    Per-project keys contain a colon (``L6:Cortex``) — the browser
    url-encodes that as ``L6%3ACortex``, so we MUST percent-decode
    before lookup or every L6:<proj> fetch returns an empty payload.
    """
    from urllib.parse import unquote

    from cortex_viz.server.http_standalone_graph import get_phase_payload

    try:
        name = ""
        offset = 0
        limit: int | None = None
        if "?" in handler.path:
            for p in handler.path.split("?", 1)[1].split("&"):
                if p.startswith("name="):
                    name = unquote(p[5:])
                elif p.startswith("offset="):
                    try:
                        offset = int(p[7:])
                    except ValueError:
                        pass
                elif p.startswith("limit="):
                    try:
                        limit = int(p[6:])
                    except ValueError:
                        pass
        send_json_ok(handler, get_phase_payload(name, offset=offset, limit=limit))
    except Exception as e:
        send_json_error(handler, e)


def _lod_cell_bbox(level: int, cx: int, cy: int) -> tuple[float, float, float, float]:
    """World bbox of LOD cell ``(level, cx, cy)`` in [-1, 1] coords.

    span = 2 / 2^level; min = -1 + index * span. source: cortex-viz-scaling.md
    DECISION 4 (same dyadic decomposition the aggregator bins into).
    """
    span = 2.0 / (1 << level)
    min_x = -1.0 + cx * span
    min_y = -1.0 + cy * span
    return (min_x, min_y, min_x + span, min_y + span)


def _resolve_lod_cell(store, node_id: str) -> dict:
    """Drill a coarse ``lod:L:cx:cy`` dot to the REAL nodes in its cell.

    Pre: ``node_id`` is ``lod:<level>:<cx>:<cy>``. Post: returns the real
    ``workflow_graph_layout`` node ids whose raw coords fall in the cell's world
    bbox (a bbox pick over the FINE layout), plus the count. A coarse cell at a
    deep level holds few raw nodes; this terminates at real node ids.
    """
    parts = node_id.split(":")
    if len(parts) != 4:
        return {"id": node_id, "kind": "lod", "found": False, "error": "bad_lod_id"}
    try:
        level, cx, cy = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return {"id": node_id, "kind": "lod", "found": False, "error": "bad_lod_id"}

    from cortex_viz.infrastructure import layout_pg_store

    min_x, min_y, max_x, max_y = _lod_cell_bbox(level, cx, cy)
    raw = layout_pg_store.read_positions_in_bbox(
        store, min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y
    )
    members = [{"id": rid, "x": rx, "y": ry, "kind": rk} for rid, rx, ry, rk in raw]
    return {
        "id": node_id,
        "kind": "lod",
        "found": bool(members),
        "level": level,
        "cell": [cx, cy],
        "bbox": [min_x, min_y, max_x, max_y],
        "member_count": len(members),
        "members": members,
    }


def serve_graph_node(handler, store) -> None:
    """GET /api/graph/node?id=<node_id> — full record for one node.

    The phase payload carries only 6 fields per node (id/kind/domain_id/
    x/y/size) so the galaxy loads in ~30 ms. The rich detail panel fetches
    the full record on click via this endpoint (on-demand drill) instead
    of bloating the base graph. Resolves ``memory:<pg_id>`` and
    ``entity:<pg_id>`` ids to their PG rows; other kinds return the id
    parsed into {kind, label}. source: design 2026-05-31 — top-25k galaxy
    + on-demand cold-tail drill.
    """
    from urllib.parse import unquote

    try:
        node_id = ""
        if "?" in handler.path:
            for p in handler.path.split("?", 1)[1].split("&"):
                if p.startswith("id="):
                    node_id = unquote(p[3:])
        if not node_id:
            send_json_ok(handler, {"error": "missing id"})
            return

        kind, _, raw = node_id.partition(":")

        # Coarse LOD dot drill-down: ``lod:L:cx:cy`` resolves to the REAL nodes
        # in that cell via a raw-layout bbox pick. source: cortex-viz-scaling.md
        # DECISION 4.
        if kind == "lod":
            send_json_ok(handler, _resolve_lod_cell(store, node_id))
            return

        record: dict | None = None
        if kind == "memory" and raw.isdigit() and hasattr(store, "get_memory"):
            record = store.get_memory(int(raw))
        elif kind == "entity" and raw.isdigit() and hasattr(store, "get_entity_by_id"):
            record = store.get_entity_by_id(int(raw))

        # Fallback for every kind without a PG row (symbol, file,
        # domain, skill, hook, tool_hub, discussion, command, mcp):
        # serve the full cached node from the build's id index. Without
        # this, only memory:/entity: ids ever resolved and the detail
        # panel stayed empty for the rest of the galaxy — nodes were
        # not browsable (observed 2026-06-12).
        from cortex_viz.server.http_standalone_graph import (
            get_node_neighbors,
            get_node_record,
        )

        if record is None:
            record = get_node_record(node_id)

        # Neighborhood ON DEMAND — the panel's relational sections
        # (symbols defined here, imports, callers) render from THIS
        # response, never from a client-side join over the full edge
        # copy (the monolithic-load report, 2026-06-12). Paged via
        # ?n_offset / ?n_limit; complete across continuation.
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(handler.path).query)

        def _qint(name: str, default: int) -> int:
            try:
                return int(qs[name][0])
            except (KeyError, IndexError, ValueError):
                return default

        nb = get_node_neighbors(
            node_id, offset=_qint("n_offset", 0), limit=_qint("n_limit", 500)
        )

        send_json_ok(
            handler,
            {
                "id": node_id,
                "kind": kind or "unknown",
                "found": record is not None,
                "record": record or {},
                "neighbors": nb["neighbors"],
                "neighbor_total": nb["total"],
                "neighbor_next_offset": nb["next_offset"],
            },
        )
    except Exception as e:
        send_json_error(handler, e)


def serve_discussions(handler) -> None:
    """GET /api/discussions — paginated session list."""
    try:
        send_json_ok(handler, build_discussions_response(handler.path))
    except Exception as e:
        send_json_error(handler, e)


def serve_discussion_detail(handler, path_no_qs: str) -> None:
    """GET /api/discussion/<session_id> — single-session transcript."""
    try:
        session_id = path_no_qs.rsplit("/", 1)[-1]
        send_json_ok(handler, build_discussion_detail(session_id))
    except Exception as e:
        send_json_error(handler, e)


def serve_static(handler, base_dir: Path, filename: str, content_type: str) -> None:
    """Sandboxed read-only static-file reader for ``/js/`` and ``/css/``.

    Security: strip directory components, reject hidden files / null
    bytes / non-alphanumeric names, match against a directory-listing
    whitelist so the user-supplied path never drives the filesystem
    read.
    """
    safe_name = Path(filename).name
    if (
        not safe_name
        or safe_name.startswith(".")
        or "\x00" in safe_name
        or not re.match(r"^[\w][\w.\-]*$", safe_name)
    ):
        send_plain_error(handler, 403)
        return
    resolved_base = base_dir.resolve()
    actual_files = {f.name: f for f in resolved_base.iterdir() if f.is_file()}
    if safe_name not in actual_files:
        send_plain_error(handler, 404)
        return
    body = actual_files[safe_name].read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type + "; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def serve_file_diff(handler) -> None:
    """Thin delegate to ``http_file_diff.serve_file_diff``."""
    from cortex_viz.server.http_file_diff import serve_file_diff as _serve

    _serve(handler)


# ``build_methodology_handler`` removed in Gap 10 — it imported a
# symbol (``build_methodology_graph``) that never existed in
# ``graph_builder.py``, so ``http_standalone --type methodology`` was
# broken-on-start. The MCP tool ``get_methodology_graph`` now covers
# the same use case without a separate HTTP surface.
