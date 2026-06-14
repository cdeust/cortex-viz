"""Memory-browser HTTP endpoints (Knowledge + Board views).

GET /api/memories         — keyset-paged, filterable memory list
GET /api/memories/facets  — aggregate filter facets (chips)

These read the shared Cortex Postgres directly (cortex-viz is the live bridge,
not a 410 stub). All SQL lives in ``infrastructure.memory_browse``; this module
is the thin composition root that parses the query string and serializes.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from cortex_viz.infrastructure import memory_browse
from cortex_viz.server.http_standalone_response import send_json_error, send_json_ok


def _query_params(handler) -> dict[str, str]:
    """Flatten the request query string to a single-value dict."""
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items() if v}


def serve_memories(handler, store) -> None:
    """GET /api/memories — one keyset page of memories (Knowledge + Board)."""
    try:
        send_json_ok(
            handler, memory_browse.list_memories_page(store, _query_params(handler))
        )
    except Exception as e:  # pragma: no cover - defensive
        send_json_error(handler, e)


def serve_memory_facets(handler, store) -> None:
    """GET /api/memories/facets — aggregate filter facets."""
    try:
        send_json_ok(handler, memory_browse.memory_facets(store))
    except Exception as e:  # pragma: no cover - defensive
        send_json_error(handler, e)


__all__ = ["serve_memories", "serve_memory_facets"]
