"""``/api/graph`` response builder + query parser.

Extracted verbatim from ``http_standalone_graph.py``. Reads the shared cache
(``graph_cache_state``), routes (re)builds to the child PROCESS
(``build_process``), and never blocks.
"""

from __future__ import annotations

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_appliers import get_build_progress
from cortex_viz.server.graph_build import _roster_fingerprint


def parse_graph_query(path: str) -> dict:
    """Parse ``/api/graph`` query string into domain/batch/batch_size."""
    result: dict = {"domain_filter": None, "batch": 0, "batch_size": 0}
    if "?" not in path:
        return result
    for p in path.split("?", 1)[1].split("&"):
        if p.startswith("domain="):
            result["domain_filter"] = p[7:]
        elif p.startswith("batch="):
            try:
                result["batch"] = int(p[6:])
            except ValueError:
                pass
        elif p.startswith("batch_size="):
            try:
                result["batch_size"] = int(p[11:])
            except ValueError:
                pass
    return result


def get_graph_response(store, path: str) -> dict:
    """Return whatever's in the cache instantly; never block.

    First visit on a fresh server: kicks off the background builder
    and returns an empty placeholder. The client shows a progress
    bar driven by ``/api/graph/progress`` and re-fetches this
    endpoint once ``baseline_ready`` or ``full_ready`` flips true.

    Tight coupling rule: we NEVER kick a rebuild if a build is
    currently running OR the last-completed cache matches the
    current roster fingerprint. Without this guard, the per-phase
    progress polling + /api/graph round-trips were racing into
    repeated rebuilds — each one restarting the AST loop from
    project 0. The only legitimate reason to re-kick is a roster
    change (a new project appeared).
    """
    from cortex_viz.server import build_process

    params = parse_graph_query(path)
    domain_filter = params["domain_filter"]
    current_fp = _roster_fingerprint()
    roster_changed = current_fp != state._graph_roster_fingerprint
    # "In progress" is now a LIVE build CHILD, not the server's in-process
    # build lock (the server never runs the GIL-hogging in-process build).
    build_in_progress = build_process._is_alive()
    cache_has_data = bool(
        state._graph_cache
        and state._graph_cache.get("data")
        and state._graph_cache.get("data", {}).get("nodes")
    )

    # Never re-kick while a build child is alive — it owns the AST loop, and
    # double-triggering would reset all phase state mid-stream.
    # Also never re-kick if we already have a populated graph whose roster
    # hasn't changed — it's still current.
    if build_in_progress or (cache_has_data and not roster_changed):
        if cache_has_data:
            return state._graph_cache["data"]
        # Build running but no data yet — return placeholder.
        return {
            "nodes": [],
            "edges": [],
            "clusters": [],
            "meta": {
                "schema": "workflow_graph.v1",
                "node_count": 0,
                "edge_count": 0,
                "stage": "building",
                "progress": get_build_progress(),
            },
        }

    # Roster changed (a new project indexed): the prior build's cache is
    # stale. Kill the current child (if any) so its epoch is retired, then
    # start a fresh epoch. begin_epoch (inside start_build) resets state.
    if roster_changed:
        state._graph_roster_fingerprint = current_fp
        build_process.kill_current_build()

    # Route the (re)build to the child PROCESS — never run the GIL-hogging
    # in-process build in the SERVER process. On roster_changed we still
    # return the existing cache immediately below (never block on the kick).
    url = getattr(store, "_url", None)
    if url:
        build_process.start_build(url, domain_filter)

    # If there's any cache at all (stale TTL or prior domain), return
    # it — better than an empty graph. Otherwise placeholder.
    if state._graph_cache and state._graph_cache.get("data"):
        return state._graph_cache["data"]

    return {
        "nodes": [],
        "edges": [],
        "clusters": [],
        "meta": {
            "schema": "workflow_graph.v1",
            "node_count": 0,
            "edge_count": 0,
            "stage": "building",
            "progress": get_build_progress(),
        },
    }
