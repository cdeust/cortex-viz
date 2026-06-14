"""Route dispatch table for the unified standalone viz server.

Split out of ``http_standalone.py`` (was 554 lines) to respect the
500-line file limit. Holds the GET dispatch (``_route_unified_get``), the
wiki DB-op path map, and the 410-Gone helper for memory-domain features
that live in the Cortex MCP, not cortex-viz. Composition-root glue — every
endpoint body is in a sibling module; this only routes.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from cortex_viz.server.http_standalone_endpoints import (
    serve_discussion_detail,
    serve_discussions,
    serve_file_diff,
    serve_sankey,
    serve_static,
    serve_stats,
)

def _feature_moved(handler, feature: str, use_instead: str) -> None:
    """Reply 410 Gone for a tab whose data lives in the Cortex memory MCP.

    cortex-viz is the visualization MCP; memory-browser / wiki / causal-chain
    are memory-domain features served by Cortex. This keeps the boundary
    honest instead of silently 404-ing or importing the subsystem back in.
    """
    body = _json.dumps(
        {
            "error": "feature_not_in_viz",
            "feature": feature,
            "detail": (
                f"'{feature}' is a Cortex memory feature, not bundled in "
                f"cortex-viz. Use {use_instead} via the Cortex MCP."
            ),
        }
    ).encode()
    handler.send_response(410)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _route_unified_get(
    handler,
    store,
    js_dir: Path,
    css_dir: Path,
    html_path: Path,
    vendor_dir: Path | None = None,
) -> None:
    """Resolve a GET request for the unified server."""
    path = handler.path
    path_no_qs = path.split("?")[0]
    if path_no_qs == "/api/trace/domains":
        from cortex_viz.server.http_standalone_trace import serve_trace_domains

        serve_trace_domains(handler)
        return
    if path_no_qs == "/api/trace/sessions":
        from cortex_viz.server.http_standalone_trace import serve_trace_sessions

        serve_trace_sessions(handler)
        return
    if path_no_qs == "/api/trace/chain":
        from cortex_viz.server.http_standalone_trace import serve_trace_chain

        serve_trace_chain(handler)
        return
    if path_no_qs == "/api/trace/file":
        from cortex_viz.server.http_standalone_trace import serve_trace_file

        serve_trace_file(handler)
        return
    if path_no_qs == "/api/trace/impact":
        from cortex_viz.server.http_standalone_trace import serve_trace_impact

        serve_trace_impact(handler)
        return
    if path_no_qs == "/api/graph/node":
        from cortex_viz.server.http_standalone_endpoints import serve_graph_node

        serve_graph_node(handler, store)
        return
    if path_no_qs == "/api/graph/chain":
        _feature_moved(handler, "causal-chain", "get_causal_chain")
        return
    if path_no_qs == "/api/graph/progress":
        from cortex_viz.server.http_standalone_endpoints import serve_graph_progress

        serve_graph_progress(handler, store)
        return
    if path_no_qs == "/api/graph/events":
        from cortex_viz.server.http_standalone_endpoints import serve_graph_events

        serve_graph_events(handler, store)
        return
    if path_no_qs == "/api/graph/phase":
        from cortex_viz.server.http_standalone_endpoints import serve_graph_phase

        serve_graph_phase(handler)
        return
    if path_no_qs == "/api/graph/slice":
        from cortex_viz.server.http_standalone_endpoints import serve_graph_slice

        serve_graph_slice(handler)
        return
    if path_no_qs == "/api/prd":
        # Third bridge: PRD document/section nodes from on-disk artifacts.
        from cortex_viz.server.http_standalone_endpoints import serve_prd

        serve_prd(handler, store)
        return
    if path_no_qs == "/api/activity/stream":
        # Live SSE of session-activity nodes/edges (replay-then-tail).
        from cortex_viz.server.http_standalone_endpoints import serve_activity_stream

        serve_activity_stream(handler, store)
        return
    if path_no_qs == "/api/graph/full":
        # Durable full graph from the PG snapshot — stable across the build
        # rebuild loop, no lazy-kick. Must stay BEFORE the bare ``/api/graph``.
        from cortex_viz.server.http_standalone_endpoints import serve_graph_full

        serve_graph_full(handler, store)
        return
    if path_no_qs == "/api/graph":
        # Lazy-kicks the background build on first hit with an empty
        # cache (see http_standalone_graph.py). Must stay AFTER the more
        # specific ``/api/graph/*`` branches above.
        from cortex_viz.server.http_standalone_endpoints import serve_graph

        serve_graph(handler, store)
        return
    if path_no_qs == "/api/memories/facets":
        from cortex_viz.server.http_standalone_memories import serve_memory_facets

        serve_memory_facets(handler, store)
        return
    if path_no_qs == "/api/memories":
        # Keyset-paged memory browser (Knowledge + Board views), read straight
        # from the shared Cortex PG — cortex-viz is the live bridge.
        from cortex_viz.server.http_standalone_memories import serve_memories

        serve_memories(handler, store)
        return
    if path == "/api/discussions" or path.startswith("/api/discussions?"):
        serve_discussions(handler)
    elif path_no_qs.startswith("/api/discussion/"):
        serve_discussion_detail(handler, path_no_qs)
    elif path_no_qs.startswith("/api/wiki/"):
        from cortex_viz.server.http_standalone_wiki import serve_wiki

        serve_wiki(handler, store, path_no_qs)
    elif path_no_qs == "/api/stats":
        serve_stats(handler, store)
    elif path == "/api/sankey" or path.startswith("/api/sankey?"):
        serve_sankey(handler, store)
    elif path.startswith("/api/file-diff?"):
        serve_file_diff(handler)
    elif path_no_qs == "/api/recompute_layout":
        # GET-triggered for v1 simplicity (no body to receive). Runs
        # synchronously; the response carries timing + the new
        # layout_version. PR 2 moves this off the request thread.
        from cortex_viz.handlers.recompute_layout import serve as serve_recompute

        serve_recompute(handler, store)
    elif path_no_qs.startswith("/api/tile/") and path_no_qs.endswith(".png"):
        from cortex_viz.handlers.tile_handler import serve as serve_tile

        serve_tile(handler, store)
    elif path_no_qs == "/api/quadtree":
        from cortex_viz.handlers.quadtree_handler import serve as serve_quadtree

        serve_quadtree(handler, store)
    elif path.startswith("/js/") and path_no_qs.endswith(".js"):
        serve_static(handler, js_dir, path_no_qs[4:], "application/javascript")
    elif path.startswith("/css/") and path_no_qs.endswith(".css"):
        serve_static(handler, css_dir, path_no_qs[5:], "text/css")
    elif (
        path.startswith("/vendor/")
        and path_no_qs.endswith(".js")
        and vendor_dir is not None
    ):
        # Vendored third-party JS (deck.gl, apache-arrow, flatbush, …).
        # Served from ui/unified/vendor/ so the tilemap view doesn't
        # break when the CDN is unreachable (offline dev, sandboxed
        # environments, unpkg outages). The tilemap loader falls back
        # to the CDN URL when this 404s, so removing files here only
        # degrades to the older behaviour.
        serve_static(
            handler, vendor_dir, path_no_qs[len("/vendor/") :], "application/javascript"
        )
    else:
        # Cache-bust every local JS/CSS load in the HTML so hard-reloads
        # actually fetch fresh code. Without this, Chrome / Safari will
        # happily reuse the old graph.js / polling.js that was cached
        # on the first visit even when the server is serving new bytes.
        raw = html_path.read_bytes()
        import re as _re
        import time as _time

        cb = str(int(_time.time()))
        text = raw.decode("utf-8", errors="replace")
        # REPLACE any existing ?v=… (and add one where missing) with a fresh
        # per-load timestamp. The HTML ships many tags pinned to a build SHA
        # (?v=117ece5); the old regex skipped those (it stopped at the `?`), so
        # the browser cached them forever and never saw edited JS — the user
        # ended up testing stale code across reloads. Matching the `.js`/`.css`
        # path and swallowing any trailing query fixes both cases.
        text = _re.sub(
            r'(<script\s+[^>]*src="/js/[^"?]+\.js)(?:\?[^"]*)?(")',
            r"\1?v=" + cb + r"\2",
            text,
        )
        text = _re.sub(
            r'(<link\s+[^>]*href="/css/[^"?]+\.css)(?:\?[^"]*)?(")',
            r"\1?v=" + cb + r"\2",
            text,
        )
        body = text.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store, must-revalidate")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Expires", "0")
        handler.end_headers()
        handler.wfile.write(body)
