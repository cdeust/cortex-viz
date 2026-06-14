"""Background galaxy builder — kick + ensure-started entry points.

Extracted verbatim from ``http_standalone_graph.py``'s
``_kick_background_build`` + ``ensure_build_started``. The build body lives in
``graph_build_run.run_build``; the per-phase/progress/fingerprint/layout
helpers live in ``graph_build_helpers`` (shared with ``graph_response``).

The ``_roster_fingerprint`` and ``_set_progress`` re-exports keep
``graph_response`` and any prior ``graph_build.*`` references resolving.

Shared cache state lives in ``graph_cache_state`` (the single owner).
"""

from __future__ import annotations

import threading

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_build_helpers import (  # noqa: F401
    _mark_phase_ready,
    _persist_full_layout,
    _phase_deps_satisfied,
    _register_phase,
    _roster_fingerprint,
    _set_progress,
)
from cortex_viz.server.graph_build_run import run_build


def ensure_build_started(store) -> None:
    """Kick the background galaxy build unless one is running or the
    in-process cache already holds nodes.

    Called once at server launch (http_standalone.main) so the galaxy
    streams in from the start, and again by the phase poller on first
    GRAPH-tab visit. Repeated polls are harmless — start_build refuses
    while a live build child exists.
    """
    if state._graph_cache and state._graph_cache.get("data", {}).get("nodes"):
        return
    # Run the CPU-bound build in a separate PROCESS so it cannot starve the
    # HTTP server thread for the GIL. The build child forwards progress + SSE
    # deltas + the final graph back over a queue (see build_process).
    #
    # The SERVER process must NEVER run the in-process build (_kick_background_build):
    # the igraph DrL layout holds the GIL for tens of seconds, starving the HTTP
    # server thread (measured: spinner 36M→3200 ticks/s during layout). When no
    # store URL is available we cannot spawn the child, so we degrade gracefully
    # rather than run the GIL-hogging build in-process.
    url = getattr(store, "_url", None)
    if url:
        from cortex_viz.server import build_process

        # Record the roster fingerprint NOW so the first /api/graph call
        # does not see a spurious roster_change (server fp starts at () but
        # the real roster is non-empty) and needlessly kill-then-restart the
        # build this call just started.
        state._graph_roster_fingerprint = _roster_fingerprint()
        build_process.start_build(url, None)
    else:
        _set_progress(
            phase="degraded",
            message="build unavailable: no DB url",
        )


def _kick_background_build(store, domain_filter: str | None) -> None:
    """Spawn the two-stage background builder at most once. Stage 1
    (baseline, no AST) finishes in ~5 s and becomes the cached graph
    immediately. Stage 2 (AST sweep) runs afterwards and replaces
    the cache when it completes. Idempotent — the build lock
    collapses overlapping calls."""
    if not state._graph_build_lock.acquire(blocking=False):
        return
    threading.Thread(
        target=run_build,
        args=(store, domain_filter),
        name="cortex-graph-build",
        daemon=True,
    ).start()
