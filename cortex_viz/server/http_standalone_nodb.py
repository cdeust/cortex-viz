"""No-DB degradation surface for the standalone HTTP server.

When the composition root (``http_standalone.main``) resolves no store —
explicit ``--no-db`` / ``CORTEX_VIZ_NO_DB=1``, or the startup probe found
PostgreSQL unreachable (``infrastructure.db_probe``) — the server still
binds and serves the Trace view fully (session JSONL + git, no DB). This
module owns everything that degradation needs on the HTTP side:

  * ``requires_store(path)``       — is this GET route DB-backed?
  * ``serve_db_unavailable(...)``  — the honest 503 those routes return
    instead of a psycopg stack trace (same idiom as the routes module's
    ``_feature_moved`` 410).
  * ``serve_capabilities(...)``    — ``GET /api/capabilities``: the one
    signal the frontend reads at boot to grey out the five DB-backed
    views (``ui/unified/js/capabilities.js``).

Kept out of ``http_standalone_routes`` so that module stays a pure
dispatch table (its own header contract).
"""

from __future__ import annotations

from cortex_viz.server.http_standalone_response import send_json_ok

CORTEX_INSTALL_URL = "https://github.com/cdeust/Cortex"

# GET routes whose endpoint consumes the PG store (each maps to a
# ``serve_*(handler, store)`` call site in ``http_standalone_routes``, or
# to a background build the store feeds). Everything NOT listed here —
# /api/trace/*, /api/discussions*, /api/prd, /api/capabilities, static
# assets, the HTML shell — serves without a database.
#
# /api/graph/node is deliberately ABSENT: the Trace detail panel resolves
# file paths through it, and its store reads are hasattr-guarded (see
# ``_resolve_node_record``), so with no store it degrades to the cached
# build index (empty in no-DB mode → ``found: false``), never an error.
_DB_BACKED_EXACT = frozenset(
    {
        "/api/dashboard",
        "/api/graph",
        "/api/graph/full",
        "/api/graph/full/stream",
        "/api/graph/progress",
        "/api/graph/events",
        "/api/memories",
        "/api/memories/facets",
        "/api/skills",
        "/api/stats",
        "/api/sankey",
        "/api/file-diff",
        "/api/recompute_layout",
        "/api/quadtree",
        "/api/activity/stream",
    }
)
_DB_BACKED_PREFIXES = ("/api/wiki/", "/api/tile/")


def requires_store(path_no_qs: str) -> bool:
    """True iff ``path_no_qs`` routes to a store-consuming endpoint."""
    if path_no_qs in _DB_BACKED_EXACT:
        return True
    return path_no_qs.startswith(_DB_BACKED_PREFIXES)


def serve_db_unavailable(handler, feature: str) -> None:
    """Reply 503 for a DB-backed route while running in no-DB mode.

    503 (not 404/410): the feature exists and lights up as soon as a
    Cortex PostgreSQL is reachable — a temporary service condition, not
    a moved or missing resource.
    """
    import json as _json

    body = _json.dumps(
        {
            "error": "db_unavailable",
            "feature": feature,
            "detail": (
                "cortex-viz is running in no-DB mode (Trace only). "
                f"Install Cortex ({CORTEX_INSTALL_URL}) and point "
                "DATABASE_URL at its PostgreSQL to enable this view."
            ),
        }
    ).encode()
    handler.send_response(503)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def serve_capabilities(handler, store) -> None:
    """GET /api/capabilities — what this server instance can serve.

    The frontend fetches this once at boot: ``db: false`` greys out the
    five DB-backed view tabs and shows the "works without Cortex" panel
    (``capabilities.js``). Trace is always ``true`` — it reads session
    JSONL + git only.
    """
    has_db = store is not None
    send_json_ok(
        handler,
        {
            "db": has_db,
            "mode": "full" if has_db else "trace-only",
            "views": {
                "trace": True,
                "graph": has_db,
                "brain": has_db,
                "knowledge": has_db,
                "wiki": has_db,
                "board": has_db,
            },
            "cortex_install_url": CORTEX_INSTALL_URL,
        },
    )


__all__ = [
    "CORTEX_INSTALL_URL",
    "requires_store",
    "serve_db_unavailable",
    "serve_capabilities",
]
