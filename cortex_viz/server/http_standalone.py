"""Standalone HTTP server — runs as a detached process, survives MCP shutdown.

Starts the unified viz or methodology server, writes the bound URL to
stdout, then serves until the idle timeout fires (10 min with no
requests). Composition-root only: the route table lives here, but every
endpoint body has been extracted to a sibling module so this file stays
inside the 300-line ceiling.

Sibling modules:

* ``http_standalone_state`` — shared caches + touch() watchdog state.
* ``http_standalone_graph`` — workflow-graph cache + discussions.
* ``http_standalone_endpoints`` — /api/sankey, /api/graph, static, diff,
  methodology handler factory.
* ``http_standalone_response`` — JSON response boilerplate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

from cortex_viz.server.http_common import _apply_cors_headers
from cortex_viz.server.http_security import (
    enforce_same_origin_write,
    validate_host_header,
)
from cortex_viz.server.http_standalone_endpoints import (
    serve_discussion_detail,
    serve_discussions,
    serve_file_diff,
    serve_sankey,
    serve_static,
    serve_stats,
)
from cortex_viz.server.http_standalone_state import (
    IDLE_TIMEOUT,
    seconds_since_last_request,
    touch,
)
# Wiki, memory-browser, and causal-chain tabs are NOT bundled in cortex-viz
# (thin-viz extraction): each pulls a full Cortex subsystem (wiki engine,
# recall pipeline, knowledge graph). Their routes return 410 via
# _feature_moved below, pointing callers at the Cortex MCP that owns the data.
import json as _json


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


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTP server — prevents graph builds from blocking static files."""

    daemon_threads = True


def _idle_watchdog(server: HTTPServer) -> None:
    """Shut the server down after ``IDLE_TIMEOUT`` seconds with no requests."""
    while True:
        time.sleep(30)
        if seconds_since_last_request() >= IDLE_TIMEOUT:
            print(
                f"[cortex] Standalone server stopped (idle {IDLE_TIMEOUT}s)",
                file=sys.stderr,
            )
            server.shutdown()
            return


def _get_ui_root() -> Path:
    """Resolve the UI root whether run from the pip install, plugin cache,
    or dev checkout.

    The canonical marker is ``unified-viz.html`` — we require it to exist,
    otherwise the resolver falls through. An empty ``mcp_server/ui/``
    directory (left behind by an earlier sync) previously won this lookup
    and crashed every request when the HTML was missing.
    """
    pkg_dir = Path(__file__).parent.parent
    candidates = [
        pkg_dir / "ui",  # pip-installed layout
        pkg_dir.parent / "ui",  # plugin cache + dev checkout
    ]
    try:
        candidates.append(Path.cwd() / "ui")  # last-resort when cwd is plugin root
    except OSError:
        # The spawning MCP server can sit in a DELETED directory (its
        # plugin-cache root vanishes on every plugin update while the
        # process keeps running); the child inherits that cwd and
        # os.getcwd() raises FileNotFoundError. The cwd candidate is a
        # last resort only — losing it must not kill the server
        # (observed 2026-06-13: open_visualization failed with
        # "Expecting value: line 1 column 1" because the child died here).
        pass
    for ui in candidates:
        if (ui / "unified-viz.html").is_file():
            return ui
    raise RuntimeError(f"UI files not found — looked in {[str(c) for c in candidates]}")


def _get_store():
    """Return the read-only viz store for this standalone process.

    The boundary cut (thin-viz): cortex-viz never instantiates Cortex's
    MemoryStore (writes, schema init, embeddings, the full storage layer).
    It reads Cortex's shared PostgreSQL through MemoryReader, which exposes
    exactly the 14 read methods + dict-row `_conn` the viz routes consume.
    """
    from cortex_viz.infrastructure.memory_read import MemoryReader

    return MemoryReader()


_WIKI_DB_OPS = {
    "/api/wiki/page_meta": "page_meta",
    "/api/wiki/concepts": "concepts",
    "/api/wiki/drafts": "drafts",
    "/api/wiki/memos": "memos",
    "/api/wiki/views": "views",
    "/api/wiki/view": "view",
    "/api/wiki/bibliography": "bibliography",
    "/api/wiki/bibliography/read": "bibliography_read",
}


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
    if path_no_qs == "/api/graph":
        # Lazy-kicks the background build on first hit with an empty
        # cache (see http_standalone_graph.py). Must stay AFTER the more
        # specific ``/api/graph/*`` branches above.
        from cortex_viz.server.http_standalone_endpoints import serve_graph

        serve_graph(handler, store)
        return
    if path_no_qs == "/api/memories":
        _feature_moved(handler, "memory-browser", "recall / memory_stats")
        return
    if path_no_qs == "/api/memories/facets":
        _feature_moved(handler, "memory-facets", "recall / memory_stats")
        return
    if path == "/api/discussions" or path.startswith("/api/discussions?"):
        serve_discussions(handler)
    elif path_no_qs.startswith("/api/discussion/"):
        serve_discussion_detail(handler, path_no_qs)
    elif path_no_qs in _WIKI_DB_OPS or path_no_qs.startswith("/api/wiki/"):
        _feature_moved(handler, "wiki", "wiki_read / wiki_list")
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
        # Only add ?v= to paths that don't already have a query string.
        text = _re.sub(
            r'(<script\s+[^>]*src="/js/[^"?]+?)(")',
            r"\1?v=" + cb + r"\2",
            text,
        )
        text = _re.sub(
            r'(<link\s+[^>]*href="/css/[^"?]+?)(")',
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


def _build_unified_handler(ui_root: Path, store) -> type:
    """HTTPHandler factory for the unified viz server."""
    html_path = ui_root / "unified-viz.html"
    js_dir = ui_root / "unified" / "js"
    css_dir = ui_root / "unified"
    vendor_dir = ui_root / "unified" / "vendor"

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 — required for Server-Sent Events. BaseHTTPRequestHandler
        # defaults to HTTP/1.0 which closes the connection after each
        # response, killing any streaming endpoint. Chunked transfer +
        # keep-alive land automatically once protocol_version is 1.1.
        protocol_version = "HTTP/1.1"

        def _guard_host(self) -> bool:
            if validate_host_header(self):
                return True
            self.send_response(421)
            self.end_headers()
            return False

        def do_OPTIONS(self):
            if not self._guard_host():
                return
            touch()
            self.send_response(204)
            _apply_cors_headers(self)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self):
            if not self._guard_host():
                return
            if not enforce_same_origin_write(self):
                self.send_response(403)
                self.end_headers()
                return
            touch()
            if self.path.split("?")[0] == "/api/wiki/save":
                _feature_moved(self, "wiki", "wiki_write")
            else:
                self.send_response(404)
                self.end_headers()

        def do_GET(self):
            if not self._guard_host():
                return
            touch()
            _route_unified_get(self, store, js_dir, css_dir, html_path, vendor_dir)

        def log_message(self, format, *args):
            pass

    return Handler


def _bind_server(handler_cls: type, preferred_port: int) -> HTTPServer:
    """Bind to preferred port, fall back to OS-assigned."""
    for port in [preferred_port, 0]:
        try:
            return _ThreadedHTTPServer(("127.0.0.1", port), handler_cls)
        except OSError:
            if port != 0:
                continue
            raise


def _announce(url: str) -> None:
    """Signal the bound URL to the parent process, then close stdout."""
    print(json.dumps({"url": url, "pid": os.getpid()}))
    sys.stdout.flush()
    sys.stdout.close()


def _auto_enable_ap() -> None:
    """ADR-0046 — discover the AP binary location at standalone startup.

    AP enrichment is on by default (MemorySettings.AP_ENABLED); this
    function only locates the binary and publishes it via
    ``CORTEX_AP_COMMAND`` so the bridge can spawn it. No-op when the
    binary isn't installed — ``APBridge.connect()`` then fails quietly
    and the native AST source fills the L6 ring.

    Side effects (written to ``os.environ`` of THIS process only):
      * ``CORTEX_AP_COMMAND`` = JSON spec pointing at a built binary
      * ``CORTEX_AP_GRAPH_PATH`` = ``~/.cortex/ap_graph/graph`` when
        a prior index exists. If missing, a background index is kicked
        off against ``CLAUDE_PROJECT_DIR`` (or cwd) so the next
        reload shows the AST layer.
    """
    bin_path = None
    if not os.environ.get("CORTEX_AP_COMMAND"):
        dev = (
            Path.home()
            / "Developments/anthropic-partnership/automatised-pipeline"
            / "target/release/automatised-pipeline"
        )
        if dev.is_file() and os.access(dev, os.X_OK):
            bin_path = str(dev)
        else:
            import shutil as _sh

            bin_path = _sh.which("automatised-pipeline")
    if bin_path is None and not os.environ.get("CORTEX_AP_COMMAND"):
        return
    if bin_path and not os.environ.get("CORTEX_AP_COMMAND"):
        os.environ["CORTEX_AP_COMMAND"] = json.dumps(
            {"command": bin_path, "args": []},
        )

    # 2026-05-17 (user direction): the AST roster indexer must NOT
    # auto-fire at server startup. It walks every git repo under the
    # ecosystem root and runs analyze_codebase on each — ~30 minutes
    # of pinned CPU that blocked HTTP requests via GIL contention.
    # Gated behind CORTEX_AP_AUTO_INDEX=1 so existing automation that
    # depends on this can still opt in.
    if os.environ.get("CORTEX_AP_AUTO_INDEX") != "1":
        return

    # Multi-project roster. ``~/.cortex/ap_graphs/<project>/graph`` is
    # one LadybugDB per git repo under the ecosystem root
    # (``~/Developments/anthropic-partnership/``, 2026-06-10 layout).
    # The resolver (ap_bridge.resolve_graph_paths) sweeps them all so
    # the visualization shows every indexed project at once. We kick
    # off a background indexer that walks the roster sequentially
    # (AP is single-client per process) sorted by mtime so the
    # user's most-recently-touched projects appear first and later
    # ones fade in as they finish.
    roster_root = Path.home() / ".cortex" / "ap_graphs"
    roster_root.mkdir(parents=True, exist_ok=True)

    def _bg_index():
        try:
            import asyncio as _asyncio

            from cortex_viz.infrastructure.ap_bridge import APBridge

            projects_root = Path.home() / "Developments" / "anthropic-partnership"
            projects = [
                p
                for p in projects_root.iterdir()
                if p.is_dir() and (p / ".git").exists()
            ]
            # Most-recently-touched first.
            projects.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            async def _run():
                b = APBridge()
                try:
                    for proj in projects:
                        outdir = roster_root / proj.name
                        graph_file = outdir / "graph"
                        # Graph already indexed. We still attempt a
                        # resolve_graph pass because older Cortex
                        # versions only ran index_codebase — their
                        # graphs have zero Calls_* / Imports_* rows.
                        # resolve_graph is idempotent: when edges are
                        # already present it no-ops quickly.
                        if graph_file.exists() and graph_file.stat().st_size > 10000:
                            try:
                                await b.call(
                                    "resolve_graph",
                                    {"graph_path": str(graph_file)},
                                )
                            except Exception:
                                pass
                            continue
                        outdir.mkdir(parents=True, exist_ok=True)
                        try:
                            # analyze_codebase runs index + resolve + cluster
                            # in one pass so Calls_* / Imports_* / Extends_*
                            # / Implements_* rel tables get populated. Using
                            # index_codebase alone leaves those tables empty
                            # and the viz filter has nothing to match.
                            await b.analyze_codebase(
                                str(proj),
                                output_dir=str(outdir),
                                language="auto",
                            )
                        except Exception:
                            # Any single failure must not break the roster
                            # — the user still wants the other projects.
                            continue
                finally:
                    await b.close()

            _asyncio.run(_run())
        except Exception:
            pass

    threading.Thread(target=_bg_index, name="ap-bg-index", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cortex standalone HTTP server")
    # The ``methodology`` type was removed in Gap 10 — its handler
    # imported ``build_methodology_graph`` (never existed) so it could
    # never start. The MCP tool ``get_methodology_graph`` covers the
    # same need without the broken HTTP surface.
    parser.add_argument("--type", required=True, choices=["unified"])
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    # _auto_enable_ap() only resolves the AP binary path; the
    # per-project analyze_codebase roster walk is opt-in via
    # CORTEX_AP_AUTO_INDEX=1 (it pinned CPU for 30+ min otherwise).
    _auto_enable_ap()

    ui_root = _get_ui_root()
    store = _get_store()
    handler_cls = _build_unified_handler(ui_root, store)

    # Kick the galaxy build at launch (user direction 2026-06-12) so the
    # phase loader streams the graph in from the start instead of waiting
    # for the first GRAPH-tab visit. Non-blocking: ensure_build_started
    # spawns _kick_background_build on a daemon thread and acquires the
    # build lock non-blocking, so serve_forever starts immediately.
    from cortex_viz.server.http_standalone_graph import ensure_build_started

    ensure_build_started(store)

    server = _bind_server(handler_cls, args.port)
    bound_port = server.server_address[1]
    url = f"http://127.0.0.1:{bound_port}"
    # Register this instance (pid + ACTUAL bound port) so launchers can
    # reuse it instead of respawning, and can find it even when binding
    # fell back to an OS-assigned port (see viz_instance docstring).
    from cortex_viz.server.viz_instance import write_instance

    write_instance(bound_port)
    _announce(url)

    threading.Thread(
        target=_idle_watchdog,
        args=(server,),
        daemon=True,
    ).start()

    print(f"[cortex] Standalone {args.type} server at {url}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
