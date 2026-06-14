"""Phase / progress / fingerprint / layout helpers for the background build.

Extracted verbatim from ``http_standalone_graph.py``. These are the small
build-side sinks shared by ``graph_build`` (the kick + ensure_build_started),
``graph_build_run`` (the build body), and ``graph_response`` (roster check).

All shared mutable state lives in ``graph_cache_state`` (the single owner);
the helpers mutate it via ``state.X`` / direct attribute assignment.
"""

from __future__ import annotations

import os
import sys

from cortex_viz.server import graph_cache_state as state


def _roster_fingerprint() -> tuple:
    """Return a tuple describing the current ap_graphs roster
    (``(path, size, mtime)`` for each graph directory). When this
    tuple changes — a new project has been indexed externally — the
    visualisation cache is invalidated so the next request rebuilds
    and the user sees the new symbols appear live."""
    from cortex_viz.infrastructure.ap_bridge import resolve_graph_paths

    fp: list[tuple] = []
    for p in resolve_graph_paths():
        try:
            st = os.stat(p)
            fp.append((p, int(st.st_mtime), int(st.st_size)))
        except OSError:
            continue
    return tuple(fp)


def _set_progress(**kw) -> None:
    with state._build_progress_lock:
        state._build_progress.update(kw)
    state._forward(("progress", dict(kw)))


def _register_phase(key: str, deps: list[str], label: str) -> None:
    """Add a dynamic phase at build time (per-project L6 phases +
    cross-project edges phase). Idempotent — if the phase already
    exists its deps/label are overwritten and ready is reset."""
    state.PHASES[key] = {"deps": list(deps), "ready": False, "label": label}
    state._phase_payloads[key] = {"nodes": [], "edges": []}
    with state._build_progress_lock:
        state._build_progress.setdefault("phases", {})[key] = False


def _phase_deps_satisfied(phase_key: str) -> bool:
    """Return True iff every prerequisite phase of ``phase_key`` is
    already ``ready``. The build worker calls this before publishing a
    phase so the cache never contains an edge whose endpoint node
    lives in an unpublished phase."""
    spec = state.PHASES.get(phase_key)
    if not spec:
        return True
    return all(state.PHASES[d]["ready"] for d in spec["deps"])


def _mark_phase_ready(phase_key: str) -> None:
    """Flip the phase's ``ready`` flag and bump ``phase_seq`` so the
    client knows there's a new consistent snapshot to pull."""
    if phase_key not in state.PHASES:
        return
    state.PHASES[phase_key]["ready"] = True
    with state._build_progress_lock:
        state._build_progress["phase_seq"] = (
            state._build_progress.get("phase_seq", 0) + 1
        )
        state._build_progress["phases"] = {
            k: v["ready"] for k, v in state.PHASES.items()
        }
        phase_seq = state._build_progress["phase_seq"]
    # Forward the phase-ready transition so the SERVER flips PHASES[key].ready
    # too (the child's PHASES dict does not cross the process boundary).
    # apply_phase_ready takes phase_seq=max, so it is order-tolerant.
    state._forward(("phase_ready", phase_key, phase_seq))


def _persist_full_layout(store) -> dict:
    """Compute + persist the FULL DrL layout over the finalised graph.

    Pre-conditions:
        * ``_graph_cache["data"]`` holds the complete post-build graph
          (every node + edge — backbone, memories, symbols, entities).
        * ``store`` is a PgMemoryStore-shaped reader exposing ``batch_pool``
          (the build child's own MemoryReader — its own pools, own GIL).
    Post-conditions:
        * ``workflow_graph_layout`` holds one (id, x, y, kind) row per node id
          in the finalised graph — NO cap, NO ray-placement substitution.
        * Idempotent: if the topology fingerprint already matches the persisted
          layout, no recompute runs (skip-if-fresh, via run_recompute).

    This is the AUTHORITATIVE layout for the tile-pyramid + quadtree path
    (the genuine-scaling default renderer). It runs in the build CHILD process
    after the full graph is assembled, so the O(N^1.3) DrL pass never holds the
    HTTP server's GIL. We reuse ``handlers.recompute_layout.run_recompute``,
    which owns the full extract→fingerprint→layout→persist orchestration, so
    this module stays a thin composition root with no duplicated layout logic.

    Returns the run_recompute status dict (never raises — defensive: a layout
    failure must not abort the build, the legacy baked coords still render).
    """
    try:
        from cortex_viz.handlers.recompute_layout import run_recompute

        result = run_recompute(store)
        print(
            f"[cortex] full layout persisted: {result.get('node_count')} nodes"
            f" in {result.get('elapsed_ms')}ms"
            f" (cached={result.get('cached')}, status={result.get('status')})",
            file=sys.stderr,
        )
        return result
    except Exception as _exc:  # pragma: no cover - defensive
        print(f"[cortex] full layout persist skipped: {_exc}", file=sys.stderr)
        return {"status": "error", "reason": "exception", "detail": str(_exc)}
