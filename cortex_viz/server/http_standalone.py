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
from cortex_viz.server.http_standalone_state import (
    IDLE_TIMEOUT,
    seconds_since_last_request,
    touch,
)

# The GET route table + the 410-Gone helper were split into
# ``http_standalone_routes`` (500-line limit). Re-imported here: the
# handler factory dispatches GET through ``_route_unified_get`` and POST
# /api/wiki/save through ``_feature_moved``.
from cortex_viz.server.http_standalone_routes import (
    _feature_moved,
    _route_unified_get,
)


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


def _warm_tile_renderer() -> None:
    """Pre-trigger the datashader/numba JIT with a 1-point render.

    Runs once at startup on a daemon thread. The numba kernels datashader
    compiles on first ``Canvas.points`` take ~3-4 s; doing it here keeps the
    first real tile under the latency bar. Best-effort: any failure (viz-tile
    extra absent) is swallowed — real requests fall back to their own import
    error handling.
    """
    try:
        from cortex_viz.core import tile_renderer

        tile_renderer.render_tile_png(
            [("warm", 0.0, 0.0, "memory")], z=0, x=0, y=0
        )
    except Exception:  # pragma: no cover - best-effort warmup
        pass


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

    # Warm the datashader/numba JIT off the request thread so the FIRST real
    # tile request does not pay one-time kernel compilation (~3-4 s). The LOD
    # data path is already O(1) in N; this removes the only remaining cold cost.
    threading.Thread(target=_warm_tile_renderer, daemon=True).start()

    print(f"[cortex] Standalone {args.type} server at {url}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
