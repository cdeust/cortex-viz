"""Shared graph-cache state — the single owner of cross-process globals.

THE LEAF MODULE both processes import. The galaxy build runs in a child
process (``build_process._worker``) whose sinks forward deltas over a queue;
the server process drains them through the appliers. Both the build closure
(``graph_build._run`` / ``_merge``) and the server appliers
(``graph_appliers.apply_*``) mutate the SAME globals — they must live in
exactly ONE module so there is no forked state.

INVARIANT: never copy these globals into another namespace. External writers
and the build closure mutate them via ``state.<name> = ...`` direct attribute
assignment on this imported module object; that is behaviour-identical to the
prior in-module ``global`` pattern (``build_process`` already does
``g._SINK_Q = q``). A re-export alias would bind a stale value and silently
fork the state.

Extracted verbatim from ``http_standalone_graph.py`` (2026-06-14
near-decomposability split; the cache globals are the thin interface).
"""

from __future__ import annotations

import threading

_cached_domain_hub_ids: dict[str, str] = {}

# ── Layout authority singleton ────────────────────────────────────────────
#
# The layout authority is the single owner of (node_id → (x, y)) slot
# emission for the live SSE stream. The build worker pushes node/edge
# deltas into it; the SSE handler at /api/graph/stream subscribes and
# drains. Lazy construction keeps the import graph clean (the authority
# imports from server/, this module imports nothing of it at module
# load) and lets a fresh build_authority() reset the event log once
# per process. Subsequent _kick_background_build calls reset the log
# inside build_authority() too — they don't drop the singleton.
_layout_authority = None


def get_layout_authority():
    """Return the process-wide LayoutAuthority, building it on first use.

    Pre: none.
    Post: returns the same instance for the life of the process.
    """
    global _layout_authority
    if _layout_authority is None:
        from cortex_viz.server.layout_authority import build_authority

        _layout_authority = build_authority()
    return _layout_authority


_graph_cache: dict | None = None
_graph_cache_ts: float = 0.0
# id → node dict over the cumulative cache, maintained incrementally by
# the build's _merge. Backs ``get_node_record`` so /api/graph/node can
# serve the full record for ANY node kind in O(1) — symbols, files,
# domains, skills, … previously returned ``found: false`` because the
# endpoint only resolved memory:/entity: PG ids and the detail panel
# stayed empty (nodes were not browsable, observed 2026-06-12).
_node_index: dict[str, dict] = {}
# id → [(other_id, edge_kind, "out"|"in"), ...] — incremental adjacency
# maintained by _merge. Backs ``get_node_neighbors`` so the detail
# panel's relational sections (symbols defined here, imports, callers)
# come from ONE on-demand call instead of the client joining over its
# full edge copy (the monolithic-load report, 2026-06-12).
_adjacency: dict[str, list] = {}
# Per-source totals from the last build (label -> rows loaded). Memory
# nodes are emitted-then-DISCARDED from the cumulative node array (the
# C5 bounded build), so meta counts derived from kind_counts read 0 for
# memories even though they were built and render via tiles. The header
# stats must show the BUILT total, not the retained one.
_source_totals: dict[str, int] = {}
_graph_build_lock = threading.Lock()

# When set (in the build CHILD process, by build_process._worker), the three
# build sinks — _set_progress, _mark_phase_ready, and _merge's SSE emission —
# forward their payloads onto this multiprocessing.Queue instead of only
# mutating in-process globals. The server process's drain thread replays them
# via apply_progress / apply_delta / apply_graph_replace below. None in the
# server process (normal in-process behaviour).
_SINK_Q = None

# ── Epochs (Lamport protocol) ─────────────────────────────────────────
#
# A build is identified by an integer epoch. The SERVER owns the
# authoritative ``_SERVER_EPOCH``; every applier (apply_delta / apply_progress
# / apply_phase_ready / apply_graph_replace / apply_done) takes an epoch
# argument and returns early when it does not match — so a stale child whose
# drain messages arrive after a roster re-kick can never corrupt the new
# build's state. ``begin_epoch`` is the single server-side reset point.
#
# The CHILD owns ``_BUILD_EPOCH`` (set once by build_process._worker via
# ``set_build_epoch``). ``_forward`` stamps it into index 1 of every message
# tuple, so the drain can epoch-gate without threading the value through every
# sink call.
_SERVER_EPOCH: int = 0
_BUILD_EPOCH: int = 0


def set_build_epoch(epoch: int) -> None:
    """CHILD-side: record the epoch this build child belongs to.

    Pre: called once in the build child before _kick_background_build.
    Post: every subsequent _forward stamps ``epoch`` at message index 1.
    """
    global _BUILD_EPOCH
    _BUILD_EPOCH = int(epoch)


def _forward(msg: tuple) -> None:
    """Push a sink message to the parent if running in the build child.

    The child's ``_BUILD_EPOCH`` is stamped into index 1 of every message
    (``(kind, epoch, *payload)``) so the server drain can drop stale-epoch
    messages from a child that outlived its build. The caller passes the
    message WITHOUT the epoch field (``(kind, *payload)``); _forward inserts
    it — one place owns the wire shape.
    """
    q = _SINK_Q
    if q is not None:
        try:
            q.put((msg[0], _BUILD_EPOCH, *msg[1:]))
        except Exception:  # pragma: no cover - queue closed during shutdown
            pass


# Persistent dedup state for apply_delta — O(batch) per delta, NOT O(N).
# Rebuilding a seen-set over all accumulated nodes on every delta made the
# drain thread O(N^2) and pinned the server CPU (it just moved the GIL hog
# from the build to the drainer). Reset by apply_graph_replace.
_applied_node_ids: set = set()

# The drain thread mutates _graph_cache while HTTP threads read it via
# /api/graph. To avoid a torn read (a request seeing a half-updated dict),
# the appliers build the new cache PRIVATELY and swap the _graph_cache
# reference under this lock — readers either see the old dict or the new
# one, never an in-progress mutation.
_apply_lock = threading.Lock()

# Fingerprint of the ap_graphs roster at the time of the last build.
# When it changes (a new project just finished indexing) the cache is
# invalidated so the next request rebuilds and the user sees the new
# symbols appear live.
_graph_roster_fingerprint: tuple = ()

# ── Build state machine ──
#
# Each phase has an explicit READY flag. A phase is published only
# after every phase it depends on is READY. The client re-fetches
# ``/api/graph`` only when ``phase_seq`` increments, so it never sees
# a cache that lists an edge whose endpoint node belongs to a not-yet
# published phase.
#
# Phase dependency graph:
#
#   L0 (domains)         ← no prerequisites
#   L1 (skills/hooks/…)  ← L0 ready
#   L2 (tool_hubs)       ← L1 ready  (tool_hubs belong to a domain
#                                     via in_domain edges that
#                                     reference the domain node)
#   L3 (files)           ← L2 ready  (files attach to tool hubs)
#   L4 (discussions)     ← L3 ready  (discussion→file edges)
#   L5 (memories)        ← L0 ready  (memory→domain only)
#   L6 (AST symbols)     ← L3 ready  (symbol→file defined_in edges)
#   L6 edges             ← L6 ready  (all symbols first, then edges)
#
# The publish function below refuses to publish a phase whose
# prerequisites aren't satisfied — that makes rendering order safe
# at the STATE level, not at the render level.
PHASES = {
    "L0": {"deps": [], "ready": False, "label": "L0 domains"},
    "L1": {"deps": ["L0"], "ready": False, "label": "L1 Claude setup"},
    "L2": {"deps": ["L1"], "ready": False, "label": "L2 tools"},
    "L3": {"deps": ["L2"], "ready": False, "label": "L3 files"},
    "L4": {"deps": ["L3"], "ready": False, "label": "L4 discussions"},
    "L5": {"deps": ["L0"], "ready": False, "label": "L5 memories"},
    # L6 phases are added dynamically per project at build start:
    #   "L6:<proj_name>"  → that project's AST symbols + intra-project edges
    #   "L6_CROSS"        → cross-project symbol edges (deps = all L6:<proj>)
}

_build_progress: dict = {
    "phase": "idle",
    "phase_seq": 0,  # increments on every state transition
    "pct": 0.0,
    "message": "",
    "baseline_ready": False,
    "full_ready": False,
    "node_count": 0,
    "edge_count": 0,
    "started_at": 0.0,
    "elapsed": 0.0,
    "phases": {k: v["ready"] for k, v in PHASES.items()},
}
_build_progress_lock = threading.Lock()

# Per-phase node/edge buffers. ``_merge`` writes into here in addition
# to the cumulative ``_graph_cache``. The ``/api/graph/phase`` endpoint
# returns ``_phase_payloads[key]`` so the client APPENDS the phase's
# deltas to its scene instead of rebuilding. Once a phase is READY its
# payload is immutable — no more writes land in it.
_phase_payloads: dict[str, dict] = {
    k: {"nodes": [], "edges": []}
    for k in (
        "L0",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    )
}


def graph_cache_data() -> dict | None:
    return _graph_cache["data"] if _graph_cache else None
