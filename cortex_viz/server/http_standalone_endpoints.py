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


def serve_dashboard(handler, store) -> None:
    """GET /api/dashboard — memory graph for the atom-shell 3D view.

    Read-only assembly of the entity + memory + heat data the atom-viz
    front-end (``ui/atom-viz.html`` + ``ui/dashboard/js``) polls. Pure
    formatting lives in ``http_dashboard_data``; this only shapes the HTTP
    response.
    """
    try:
        from cortex_viz.server.http_dashboard_data import build_dashboard_data

        send_json_ok(handler, build_dashboard_data(store))
    except Exception as e:
        send_json_error(handler, e)


def serve_graph_full(handler, store) -> None:
    """GET /api/graph/full — the COMPLETE graph from the durable PG snapshot.

    Serves the persisted full graph (every entity, AST symbol, memory, file,
    command + all edges — the README-hero view) directly from the durable
    snapshot store (``snapshot_pg_store``). Unlike ``/api/graph`` this does
    NOT read the volatile in-process build cache and never lazy-kicks a
    build, so it is stable across the build's rebuild loop: once a build
    has completed once, the snapshot is always available and identical.

    A ``json.v1`` row is already gzip(JSON); it is streamed verbatim with
    ``Content-Encoding: gzip`` (the browser inflates transparently) — no
    server-side decode/re-encode. An ``ndjson.v1`` row (current writer —
    see snapshot_pg_store) is stream-transformed back into the single
    JSON document line by line, identity-encoded with ``Connection:
    close``: same bytes-level arrays, no parsing. When no snapshot exists
    yet (no build has finished since install) returns 503
    ``{"reason":"no_snapshot"}`` so the client falls back to the
    progressive ``/api/graph`` path.

    NOTE: at the current corpus this document is ~1.17 GB decompressed —
    browsers cannot ``response.json()`` it. In-browser consumers use
    ``/api/graph/full/stream``; this endpoint remains for curl / scripting
    / MCP consumers that parse with a real JSON library.
    """
    try:
        from cortex_viz.infrastructure import snapshot_pg_store
        from cortex_viz.shared.instance_scope import resolve_instance_scope

        snap = snapshot_pg_store.read_latest_snapshot(
            store, scope=resolve_instance_scope()
        )
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
    if snap.get("format") == snapshot_pg_store.FORMAT_NDJSON_V1:
        from cortex_viz.server.http_standalone_fullstream import (
            serve_full_document_from_ndjson,
        )

        serve_full_document_from_ndjson(handler, snap)
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


# P0/P3 live session-activity endpoints (serve_activity_ingest,
# serve_activity_stream, the P3 blast-radius trigger) were split into
# ``http_standalone_activity`` (500-line limit — P4 node-unification's
# real-path/detail.path plumbing grew this pair past the threshold).
# Re-exported so ``from cortex_viz.server.http_standalone_endpoints import
# serve_activity_ingest`` (routes module) keeps resolving — same precedent
# as the ``http_standalone_endpoints_sankey`` re-export above.
from cortex_viz.server.http_standalone_activity import (  # noqa: F401
    serve_activity_ingest,
    serve_activity_stream,
)


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


# Content types for the shared design-system foundation (ui/shared/*). Kept
# small and explicit — the foundation ships only these kinds.
_SHARED_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".json": "application/json",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".svg": "image/svg+xml",
}


def serve_shared_asset(handler, shared_dir: Path, rel_path: str) -> None:
    """Sandboxed reader for the vendored design-system foundation (``/shared/``).

    Unlike ``serve_static`` (single-level, filename-only) this serves the
    NESTED token/component tree — ``tokens/colors.css``, ``components/core/
    core.css`` — because ``ds.css`` ``@import``s them by relative subpath. The
    depth is the reason for a distinct reader; the traversal guard is the price:
    the resolved path MUST stay inside ``shared_dir`` and be an existing file,
    so a crafted ``../`` can never escape the foundation directory.
    """
    # Reject the obvious attacks before touching the filesystem.
    if not rel_path or "\x00" in rel_path or any(
        part in ("", "..") or part.startswith(".") for part in rel_path.split("/")
    ):
        send_plain_error(handler, 403)
        return
    base = shared_dir.resolve()
    target = (base / rel_path).resolve()
    # Containment check — the resolved path must be within the foundation dir.
    if base != target and base not in target.parents:
        send_plain_error(handler, 403)
        return
    if not target.is_file():
        send_plain_error(handler, 404)
        return
    content_type = _SHARED_CONTENT_TYPES.get(target.suffix.lower(), "text/plain")
    body = target.read_bytes()
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
