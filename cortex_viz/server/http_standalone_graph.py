"""Graph cache and discussion-page builders for the standalone server.

Extracted from ``http_standalone.py`` to keep that file inside the
project-mandated 300-line ceiling. The module owns:

* graph-cache state (lock, domain-hub id map, roster fingerprint)
* the PHASE STATE MACHINE driving the progressive graph build
  (L0 domains → L1 setup → L2 tools → L3 files → L4 discussions
  → L5 memories → L6:<proj> per-project symbols → L6_CROSS)
* ``_kick_background_build`` — the background-thread builder
* ``get_graph_response`` — cache read, returns partial data while
  build is in progress, never re-kicks a running build
* ``get_phase_payload`` — per-phase nodes/edges delta for the
  ``/api/graph/phase`` append-only client loader
* ``build_discussions_response`` — paginated discussion listing
* ``build_discussion_detail`` — single-session detail
* ``parse_discussion_params`` / ``parse_graph_query`` — query-string parsers

All I/O stays behind the existing infrastructure imports; the only
layer-relevant addition is that this module lives in ``server/`` and
composes ``handlers/workflow_graph`` + ``core/graph_builder_discussions``,
which matches the rules for server → handlers/core wiring.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
import traceback

from cortex_viz.shared.hash import simple_hash

from cortex_viz.server.http_standalone_state import (
    CONVERSATIONS_CACHE_TTL,
    get_cached_conversations_state,
    set_cached_conversations_state,
)

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

# (No _DELTA_STREAM_CAP: the baseline blob rides the out-of-band graph_file,
# only naturally-bounded L6 batches stream — see _merge below.)
#
# Per-project wall-clock ceiling for the L6 AST load (tree-sitter parse +
# AP bridge round-trip). A project that exceeds this is marked ready and
# skipped so the build always reaches "done" rather than hanging forever.
# source: measured — a healthy multi-thousand-file repo resolves in <60s via
# the AP bridge; 180s = 3× that headroom, generous for a large codebase while
# still bounding the build. Replaces the prior no-timeout path that could hang
# the child indefinitely on a wedged AP subprocess.
_L6_PROJECT_TIMEOUT_S = 180.0


def _forward(msg: tuple) -> None:
    """Push a sink message to the parent if running in the build child."""
    q = _SINK_Q
    if q is not None:
        try:
            q.put(msg)
        except Exception:  # pragma: no cover - queue closed during shutdown
            pass


def _progress_snapshot() -> dict:
    with _build_progress_lock:
        return dict(_build_progress)


# ── Server-process appliers (called by build_process._drain) ──────────
#
# The drain thread mutates _graph_cache while HTTP threads read it via
# /api/graph. To avoid a torn read (a request seeing a half-updated dict),
# the appliers build the new cache PRIVATELY and swap the _graph_cache
# reference under this lock — readers either see the old dict or the new
# one, never an in-progress mutation.
_apply_lock = threading.Lock()


def apply_progress(snap: dict) -> None:
    with _build_progress_lock:
        # phase_seq must be monotone: an out-of-order drain (or a stale
        # forwarded snapshot) must never roll the client's seq backwards,
        # or it would re-fetch an older graph. Take the max of current and
        # incoming.
        incoming_seq = snap.get("phase_seq")
        _build_progress.update(snap)
        if incoming_seq is not None:
            _build_progress["phase_seq"] = max(
                _build_progress.get("phase_seq", 0), incoming_seq
            )


# Persistent dedup state for apply_delta — O(batch) per delta, NOT O(N).
# Rebuilding a seen-set over all accumulated nodes on every delta made the
# drain thread O(N^2) and pinned the server CPU (it just moved the GIL hog
# from the build to the drainer). Reset by apply_graph_replace.
_applied_node_ids: set = set()


def apply_delta(stage: str, slim_nodes: list, edges: list) -> None:
    """Append a forwarded build delta into the server-process cache AND
    re-emit it to the live /api/graph/events subscribers. O(batch).

    Atomic publish: build the new node/edge lists privately, then swap the
    _graph_cache reference under _apply_lock. /api/graph readers see either
    the pre-delta dict or the post-delta dict, never a torn intermediate.
    """
    global _graph_cache, _graph_cache_ts
    with _apply_lock:
        old = _graph_cache["data"] if _graph_cache else None
        new_nodes = list(old["nodes"]) if old else []
        new_edges = list(old["edges"]) if old else []
        new_meta = dict(old["meta"]) if old and old.get("meta") else {}
        fresh = []
        for n in slim_nodes:
            nid = n[0] if isinstance(n, list) else n.get("id")
            if nid and nid not in _applied_node_ids:
                new_nodes.append(n)
                _applied_node_ids.add(nid)
                fresh.append(n)
        new_edges.extend(edges)
        new_meta["node_count"] = len(new_nodes)
        new_meta["edge_count"] = len(new_edges)
        cur = {
            "nodes": new_nodes,
            "edges": new_edges,
            "links": new_edges,
            "meta": new_meta,
        }
        _graph_cache = {"data": cur, "domain_filter": None}
        _graph_cache_ts = time.monotonic()
    try:
        from cortex_viz.server import graph_event_stream as _ev

        if fresh:
            _ev.emit(stage, fresh, [], chunk=1000)
    except Exception:  # pragma: no cover - defensive
        pass


def apply_graph_replace(data: dict) -> None:
    """Install the authoritative full-record graph from the finished child.

    Atomic publish: swap the _graph_cache reference under _apply_lock so a
    concurrent /api/graph read never observes the cache mid-replacement.
    """
    global _graph_cache, _graph_cache_ts
    with _apply_lock:
        _graph_cache = {"data": data, "domain_filter": None}
        _graph_cache_ts = time.monotonic()
        _applied_node_ids.clear()
        for n in data.get("nodes", []):
            nid = n[0] if isinstance(n, list) else n.get("id")
            if nid:
                _applied_node_ids.add(nid)


def graph_cache_data() -> dict | None:
    return _graph_cache["data"] if _graph_cache else None


# Fingerprint of the ap_graphs roster at the time of the last build.
# When it changes (a new project just finished indexing) the cache is
# invalidated so the next request rebuilds and the user sees the new
# symbols appear live.
_graph_roster_fingerprint: tuple = ()

# Build progress — updated by the background builder, read by the
# ``/api/graph/progress`` endpoint so the WASM client can show a
# progress bar instead of a silent spinner.
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


def parse_discussion_params(path: str) -> dict:
    """Parse ``/api/discussions`` query string."""
    result: dict = {"project": None, "batch": 0, "batch_size": 500}
    if "?" not in path:
        return result
    for p in path.split("?", 1)[1].split("&"):
        if p.startswith("project="):
            result["project"] = p[8:]
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


def _compute_memory_vitals(store) -> dict:
    """Aggregate consolidation-stage counts, mean heat, and store-type split."""
    memories = store.get_hot_memories(min_heat=0.0, limit=0)
    stages: dict[str, int] = {}
    heats: list[float] = []
    episodic = 0
    semantic = 0
    for m in memories:
        s = m.get("consolidation_stage", "labile")
        stages[s] = stages.get(s, 0) + 1
        heats.append(m.get("heat", 0))
        if m.get("store_type") == "episodic":
            episodic += 1
        elif m.get("store_type") == "semantic":
            semantic += 1
    return {
        "consolidation_pipeline": stages,
        "mean_heat": round(sum(heats) / max(len(heats), 1), 4),
        "total_memories": len(memories),
        "episodic": episodic,
        "semantic": semantic,
    }


def _session_counts_from_profiles(profiles: dict) -> dict[str, int]:
    """Extract per-domain session counts from a profiles.json payload."""
    out: dict[str, int] = {}
    for did, ddata in (profiles.get("domains") or {}).items():
        out[did] = ddata.get("sessionCount", 0)
    return out


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


def get_build_progress() -> dict:
    with _build_progress_lock:
        snap = dict(_build_progress)
        if snap.get("started_at"):
            snap["elapsed"] = time.monotonic() - snap["started_at"]
    return snap


def get_node_record(node_id: str) -> dict | None:
    """Full cached record for any node id, or ``None`` when unknown.

    O(1) against the incremental ``_node_index``. Used by
    ``/api/graph/node`` as the fallback for kinds that have no PG row
    (symbol, file, domain, skill, hook, tool_hub, discussion, …) so the
    detail panel can enrich every clicked node.
    """
    return _node_index.get(node_id)


def get_node_neighbors(node_id: str, offset: int = 0, limit: int = 500) -> dict:
    """One node's neighborhood, served on demand.

    Returns ``{"neighbors": [[other_id, other_kind, other_label,
    edge_kind, direction], ...], "total", "offset", "next_offset"}``.
    Bounded per page, complete across continuation (next_offset is
    None once drained) — high-degree hubs (a domain node carries tens
    of thousands of in_domain edges) page instead of truncating.
    Default page mirrors the MCP tool's 500-row default.
    """
    rows = _adjacency.get(node_id, [])
    total = len(rows)
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    page = rows[offset : offset + limit]
    out = []
    for other_id, ekind, direction in page:
        other = _node_index.get(other_id) or {}
        out.append(
            [
                other_id,
                other.get("kind") or other.get("type"),
                other.get("label"),
                ekind,
                direction,
            ]
        )
    nxt = offset + limit
    return {
        "neighbors": out,
        "total": total,
        "offset": offset,
        "next_offset": nxt if nxt < total else None,
    }


# ── Slim wire projection (graphify-informed, 2026-06-12) ──────────────
#
# The SSE stream used to ship every node's FULL record — measured
# 259 bytes/item, 107 MB for the complete galaxy replay (414k items).
# The renderer only consumes: id, kind, domain_id, x, y, label, color,
# heat, extra_domain_ids (verified consumer audit: workflow_graph.js
# nodeColor/labelOf fall back to palette/id; filters read domain_id +
# extra_domain_ids; memory/entity weighting reads heat). Everything
# else — path, symbol_type, memory metadata, edge confidence/reason —
# is detail-panel data served on demand by /api/graph/node.
#
# Plain JSON positional arrays, deliberately NO enum tables and NO
# index↔id mapping layer (user direction: light JSON without a mapper
# — the codec class of solution is what kept breaking). Fixed layout:
#   node: [id, kind, domain_id, x, y, label, color, heat, extra_ids]
#   edge: [source, target, kind, weight]
# Absent values are null; the client decoder skips nulls so the
# renderer's existing fallbacks engage.


def _round4(v):
    """Coordinates ride the wire at 4 decimals. The DrL layout emits
    unit-scale doubles (observed 0.6026883210462267 — 18 chars); 1e-4
    resolution is sub-pixel even on a 4k-wide render of the unit
    square, and the rounding alone removes ~2 MB from the full replay
    (measured 2026-06-12: 45,871 baked coordinate pairs)."""
    return round(v, 4) if isinstance(v, float) else v


def _slim_node(n: dict) -> list:
    """THE wire record: id, kind, x, y — nothing else. No labels, no
    colors, no domain ids, no metadata, and the stream carries NO
    edges at all (user direction 2026-06-12: the planetarium renders
    every dot from id+position alone; every other byte — neighbors,
    labels, details — is a query through the on-demand endpoints and
    MCP tools, fetched only when asked)."""
    return [
        n.get("id"),
        n.get("kind") or n.get("type"),
        _round4(n.get("x")),
        _round4(n.get("y")),
    ]


def _place_around(anchor_x: float, anchor_y: float, key: str) -> tuple[float, float]:
    """Deterministic position near an anchor, in the layout engine's
    [-1, 1] world coordinates.

    Gives L6 symbols (and AP-only files) server-side coordinates so the
    wire carries a position for EVERY node and the client never has to
    force-simulate them — the plan is "server positions, client draws".
    The DrL bake covers only the baseline; symbols are placed on a
    deterministic ray around their parent file's baked coordinate.

    Distance derivation (no invented constants): the client renderer
    seeded symbols 30–150 px past their file on a ~1200 px viewport
    (workflow_graph.js symbol seeding), i.e. 2.5–12.5 % of the view.
    The world span is 2.0 ([-1,1]), so the same visual ratio is
    0.05–0.25 world units. Angle and distance both derive from the
    DJB2 hash of the node id — same input, same position, every build.
    """
    h = int(simple_hash(key), 16)
    angle = (h % 3600) / 3600.0 * 2.0 * math.pi
    dist = 0.05 + ((h >> 12) % 1000) / 1000.0 * 0.20
    return (
        round(anchor_x + math.cos(angle) * dist, 4),
        round(anchor_y + math.sin(angle) * dist, 4),
    )


def get_graph_slice(offset: int = 0, limit: int = 20000) -> dict:
    """Paginated FULL-fidelity page of the cumulative graph cache.

    The complete-across-continuation contract (no silent truncation —
    user direction 2026-06-12): each page slices BOTH nodes and edges
    by ``[offset : offset+limit]`` and reports totals; ``done`` flips
    once the window covers ``max(node_total, edge_total)``. The union
    of all pages equals the full cache. ``phase_seq`` keys consumer-
    side memoisation (a changed seq means the build published more).
    """
    cache = _graph_cache
    data = cache.get("data", {}) if cache else {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    node_total = len(nodes)
    edge_total = len(edges)
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    with _build_progress_lock:
        phase_seq = _build_progress.get("phase_seq", 0)
        full_ready = bool(_build_progress.get("full_ready"))
    return {
        "nodes": nodes[offset : offset + limit],
        "edges": edges[offset : offset + limit],
        "node_total": node_total,
        "edge_total": edge_total,
        "offset": offset,
        "limit": limit,
        "done": (offset + limit) >= max(node_total, edge_total),
        "phase_seq": phase_seq,
        "full_ready": full_ready,
    }


def ensure_build_started(store) -> None:
    """Kick the background galaxy build unless one is running or the
    in-process cache already holds nodes.

    Called once at server launch (http_standalone.main) so the galaxy
    streams in from the start, and again by the phase poller on first
    GRAPH-tab visit. Repeated polls are harmless —
    _kick_background_build acquires the build lock non-blocking and
    returns if a build is already running.
    """
    if _graph_cache and _graph_cache.get("data", {}).get("nodes"):
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

        build_process.start_build(url, None)
    else:
        _set_progress(
            phase="degraded",
            message="build unavailable: no DB url",
        )


def _set_progress(**kw) -> None:
    with _build_progress_lock:
        _build_progress.update(kw)
    _forward(("progress", dict(kw)))


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


def _register_phase(key: str, deps: list[str], label: str) -> None:
    """Add a dynamic phase at build time (per-project L6 phases +
    cross-project edges phase). Idempotent — if the phase already
    exists its deps/label are overwritten and ready is reset."""
    PHASES[key] = {"deps": list(deps), "ready": False, "label": label}
    _phase_payloads[key] = {"nodes": [], "edges": []}
    with _build_progress_lock:
        _build_progress.setdefault("phases", {})[key] = False


_PHASE_KINDS: dict[str, set[str]] = {
    "L0": {"domain"},
    # L1 = structural setup layer (~190 nodes: skills, hooks, agents, MCPs).
    # "command" is Bash-execution telemetry (5878 nodes) — NOT setup.
    # Commands belong in L2 alongside tool_hubs via command_in_hub edges.
    "L1": {"skill", "hook", "agent", "mcp"},
    "L2": {"tool_hub", "command"},
    "L3": {"file"},
    "L4": {"discussion"},
    "L5": {"memory"},
}


def get_phase_payload(key: str, offset: int = 0, limit: int | None = None) -> dict:
    spec = PHASES.get(key)
    pl = _phase_payloads.get(key, {"nodes": [], "edges": []})
    nodes = pl.get("nodes", [])
    edges = pl.get("edges", [])

    # Fallback: _phase_payloads is empty when the streaming builder
    # populates _graph_cache directly instead of the phase cache.
    # Extract the relevant kind-slice from the full cache so the
    # phase endpoint always returns useful data.
    if not nodes and key in _PHASE_KINDS:
        cache = _graph_cache
        if cache:
            cache_data = (
                cache.get("data") if isinstance(cache.get("data"), dict) else cache
            )
            all_nodes: list = (
                cache_data.get("nodes", []) if isinstance(cache_data, dict) else []
            )
            all_edges: list = (
                cache_data.get("edges", []) if isinstance(cache_data, dict) else []
            )
            allowed_kinds = _PHASE_KINDS[key]
            nodes = [
                n
                for n in all_nodes
                if (n.get("kind") or n.get("type")) in allowed_kinds
            ]
            if nodes:
                node_ids = {n["id"] for n in nodes}
                # Edge scoping — AND, not OR.
                #
                # Symptom: "[lod] L0 cortex +20N +484347E" — L0 returns 20
                # domain nodes but 484,347 edges (all edges in the graph).
                #
                # Root cause: with an OR predicate every edge that merely
                # *touches* a phase node is included. L0 nodes are the ~20
                # domain hubs, and nearly every node in the graph carries an
                # ``in_domain`` edge pointing TO its domain hub. OR therefore
                # matched all those edges → the entire edge set.
                #
                # Fix: a phase payload must carry only edges INTERNAL to the
                # phase — both endpoints inside this phase's node set. Under
                # the client's append model (lod.js → appendGraphDelta), a
                # cross-phase parent edge (e.g. tool_hub -> domain) is owned
                # by the CHILD phase, which the client appends on top of the
                # already-loaded parent phase; the dedup sets in graph.js
                # keep repeats a no-op. So no edge is lost by requiring both
                # endpoints here, and L0 collapses back to its ~20 structural
                # domain-to-domain edges.
                edges = [
                    e
                    for e in all_edges
                    if e.get("source") in node_ids and e.get("target") in node_ids
                ]

    node_total = len(nodes)
    edge_total = len(edges)
    if limit is not None:
        nodes = nodes[offset : offset + limit]
        edges = edges[offset : offset + limit]
    done = limit is None or (offset + limit) >= max(node_total, edge_total)
    return {
        "phase": key,
        "ready": bool((spec and spec["ready"]) or node_total),
        "deps": spec["deps"] if spec else [],
        "nodes": nodes,
        "edges": edges,
        "node_total": node_total,
        "edge_total": edge_total,
        "offset": offset,
        "limit": limit,
        "done": done,
    }


def _phase_deps_satisfied(phase_key: str) -> bool:
    """Return True iff every prerequisite phase of ``phase_key`` is
    already ``ready``. The build worker calls this before publishing a
    phase so the cache never contains an edge whose endpoint node
    lives in an unpublished phase."""
    spec = PHASES.get(phase_key)
    if not spec:
        return True
    return all(PHASES[d]["ready"] for d in spec["deps"])


def _mark_phase_ready(phase_key: str) -> None:
    """Flip the phase's ``ready`` flag and bump ``phase_seq`` so the
    client knows there's a new consistent snapshot to pull."""
    if phase_key not in PHASES:
        return
    PHASES[phase_key]["ready"] = True
    with _build_progress_lock:
        _build_progress["phase_seq"] = _build_progress.get("phase_seq", 0) + 1
        _build_progress["phases"] = {k: v["ready"] for k, v in PHASES.items()}
    _forward(("progress", _progress_snapshot()))


def _kick_background_build(store, domain_filter: str | None) -> None:
    """Spawn the two-stage background builder at most once. Stage 1
    (baseline, no AST) finishes in ~5 s and becomes the cached graph
    immediately. Stage 2 (AST sweep) runs afterwards and replaces
    the cache when it completes. Idempotent — the build lock
    collapses overlapping calls."""
    if not _graph_build_lock.acquire(blocking=False):
        return

    def _run():
        global _graph_roster_fingerprint

        # ── Incremental merge state ──
        # Dedup sets + kind tallies persist across _merge calls instead
        # of being rebuilt from the whole cumulative cache per batch.
        # The rebuild made every 200-node L6 batch O(cache): at ~200k
        # nodes / 480k edges × ~3,350 batches the build thread pinned
        # the GIL for hours and starved every HTTP request — the page
        # froze and node clicks timed out (observed 2026-06-12).
        seen_n: set = set()
        seen_e: set = set()
        kind_counts: dict[str, int] = {}

        def _merge(new_nodes, new_edges, stage, pct, message, phase_key=None, **flags):
            """Append ``new_nodes`` + ``new_edges`` into the cumulative
            cache AND into the phase-scoped buffer, then emit the
            actually-added delta on the live SSE stream
            (``/api/graph/events``) — the single real-time delivery
            path the browser renders from.

            Incremental: O(batch) per call via the closure dedup state
            above. Emitting only the added items keeps the SSE replay
            buffer free of duplicates (the client dedups anyway, but
            re-streaming the whole baseline doubled the wire traffic).
            """
            global _graph_cache, _graph_cache_ts, _cached_domain_hub_ids
            cur = (
                _graph_cache["data"]
                if _graph_cache
                else {"nodes": [], "edges": [], "links": [], "meta": {}}
            )
            added_nodes: list[dict] = []
            added_edges: list[dict] = []
            for n in new_nodes:
                nid = n.get("id")
                if nid and nid not in seen_n:
                    cur["nodes"].append(n)
                    seen_n.add(nid)
                    added_nodes.append(n)
                    _node_index[nid] = n
                    k = n.get("kind") or n.get("type") or ""
                    kind_counts[k] = kind_counts.get(k, 0) + 1
                    if k == "domain":
                        dk = n.get("label") or n.get("domain") or ""
                        if dk:
                            _cached_domain_hub_ids[dk] = nid
            for e in new_edges:
                key = (e.get("source"), e.get("target"), e.get("kind"))
                if key not in seen_e:
                    cur["edges"].append(e)
                    seen_e.add(key)
                    added_edges.append(e)
                    s, t, ek = key
                    if s and t:
                        _adjacency.setdefault(s, []).append((t, ek, "out"))
                        _adjacency.setdefault(t, []).append((s, ek, "in"))
            cur["links"] = cur["edges"]
            cur.setdefault("meta", {})
            cur["meta"]["stage"] = stage
            cur["meta"]["node_count"] = len(cur["nodes"])
            cur["meta"]["edge_count"] = len(cur["edges"])
            cur["meta"]["schema"] = "workflow_graph.v1"
            cur["meta"]["domain_count"] = kind_counts.get("domain", 0)
            # memory_count: when CORTEX_VIZ_MEMORY_LIMIT > 0 the builder now
            # RETAINS the capped memory nodes, so kind_counts["memory"] is
            # the true in-galaxy count (== _source_totals["memories"] when
            # bounded). On the unbounded path the nodes are still discarded
            # and only _source_totals carries the built total. max() is
            # correct in both cases. source: bounded retention (workflow_graph.py).
            cur["meta"]["memory_count"] = max(
                kind_counts.get("memory", 0), _source_totals.get("memories", 0)
            )
            cur["meta"]["discussion_count"] = kind_counts.get("discussion", 0)
            # "Entity" in the legend covers every non-domain, non-memory
            # knowledge node (files, symbols, tools, commands, agents,
            # skills, hooks, discussions, MCPs). Compute as the sum.
            cur["meta"]["entity_count"] = (
                len(cur["nodes"])
                - kind_counts.get("domain", 0)
                - kind_counts.get("memory", 0)
            )
            # Copy — the finalisation step adds derived keys (symbols,
            # ast_edges, …) to meta["counts"]; they must not leak back
            # into the incremental per-kind tallies.
            cur["meta"]["counts"] = dict(kind_counts)
            # Per-phase delta buffer for ``GET /api/graph/phase?name=…``
            # (diagnostic endpoint). added_* are globally deduped above,
            # so a plain extend keeps the buffer duplicate-free.
            if phase_key and phase_key in _phase_payloads:
                buf = _phase_payloads[phase_key]
                buf["nodes"].extend(added_nodes)
                buf["edges"].extend(added_edges)
            _graph_cache = {"data": cur, "domain_filter": domain_filter}
            _graph_cache_ts = time.monotonic()
            # ── Live SSE emission — the delivery path ──
            # Every _merge (skeleton, baseline, every L6 batch) pushes
            # its added delta onto the event stream the moment it lands
            # in the cache, projected to the slim wire format (see
            # _slim_node) — the full records stay in the
            # cache for /api/graph/node and /api/graph/slice.
            _slim_added = [_slim_node(n) for n in added_nodes] if added_nodes else []
            # In-process SSE emit — the real-time delivery path WHEN the build
            # runs in the server process. In the build CHILD (_SINK_Q set) this
            # stream has no subscribers (the SSE handler lives in the server),
            # so emitting here is pure wasted CPU; the server re-emits via
            # apply_delta when it drains the forwarded delta. Guard it off.
            if _SINK_Q is None:
                try:
                    if _slim_added:
                        _events.emit(stage, _slim_added, [], chunk=1000)
                except Exception as _exc:  # pragma: no cover - defensive
                    print(
                        f"[cortex] sse stream emission error: {_exc}",
                        file=sys.stderr,
                    )
            # Forward NATURALLY-BOUNDED deltas to the server process for live
            # SSE. The baseline merge is one giant O(N) blob (~10^5 nodes) — it
            # must NOT stream over the queue (it would choke the feeder and
            # re-pin a core); it rides the final graph_file out-of-band, like
            # the full graph. Only the L6 per-batch deltas (~200 nodes, _BATCH)
            # stream, so no message on the queue is ever O(N) — no cap needed.
            if (
                _SINK_Q is not None
                and stage != "baseline"
                and (_slim_added or added_edges)
            ):
                _forward(("delta", stage, _slim_added, added_edges))
            _set_progress(
                phase=stage,
                pct=pct,
                message=message,
                node_count=len(cur["nodes"]),
                edge_count=len(cur["edges"]),
                **flags,
            )

        try:
            from cortex_viz.handlers.workflow_graph import (
                build_workflow_graph,
            )

            _graph_roster_fingerprint = _roster_fingerprint()
            _set_progress(
                phase="starting",
                pct=0.01,
                message="loading layer definitions…",
                started_at=time.monotonic(),
                baseline_ready=False,
                full_ready=False,
                node_count=0,
                edge_count=0,
            )

            # Seed the cache fresh so the per-layer _merge writes land
            # on an empty graph. The incremental hub-id map is rebuilt
            # by _merge as domain nodes arrive — clear the previous
            # run's entries so a removed domain can't leak in.
            global _graph_cache
            _graph_cache = {
                "data": {"nodes": [], "edges": [], "links": [], "meta": {}},
                "domain_filter": domain_filter,
            }
            _cached_domain_hub_ids.clear()
            _node_index.clear()
            _adjacency.clear()
            # Reset per-phase state so a rebuild starts clean — phases
            # flip ready→pending and buffers empty. Otherwise stale L6
            # nodes from a prior run leak into the new publish and the
            # client's dedup masks missing content.
            # NOTE: per-project L6 phases (added dynamically later)
            # will be registered fresh by _register_phase, which also
            # resets their ready flags — so we only need to flip the
            # FIXED phases here.
            for _k in list(PHASES):
                PHASES[_k]["ready"] = False
            for _k in list(_phase_payloads):
                _phase_payloads[_k]["nodes"].clear()
                _phase_payloads[_k]["edges"].clear()
            # Drop dynamic L6 phases from the previous run — they'll
            # be re-added below after graph_paths is resolved.
            for _k in list(PHASES):
                if _k.startswith("L6:") or _k == "L6_CROSS":
                    PHASES.pop(_k, None)
                    _phase_payloads.pop(_k, None)
            with _build_progress_lock:
                _build_progress["phase_seq"] = 0
                _build_progress["phases"] = {k: False for k in PHASES}
            # ── Per-layer streaming build ──
            # Each layer is published the instant its data is ready:
            #   L0  domains (the hubs)
            #   L1  Claude-Code setup: skills, hooks, commands, agents
            #   L2  tools (tool_hub nodes)
            #   L3  files (+ tool→file, command→file, discussion→file)
            #   L4  discussions
            #   L5  memories
            #   L6  AST symbols — streamed per project, per batch of 200
            # Ordering matches the user's requested reveal.
            # L0 + L1 + L2 + L3 + L4 + L5 all come from one
            # ``build_workflow_graph`` call; we can't easily partition
            # those. So we run baseline first (fast — a few seconds)
            # and merge the whole thing, then tag the phase.
            # AST/AP (the slow L6 ring) is deferred from the baseline via
            # ``defer_native_ast=True`` on the build call below — that flag
            # already gates Phase 4 (see ``build_workflow_graph``), so the
            # AST loaders never run during the baseline regardless of AP
            # enablement. We deliberately do NOT mutate
            # ``CORTEX_MEMORY_AP_ENABLED`` here: that global env write
            # disabled AP process-wide for the whole multi-minute build, so
            # every interactive ``/api/trace/impact`` and AST query returned
            # ``ap_disabled`` while a build was in flight (the impact diagram
            # was broken exactly when the user was exploring). Global mutable
            # state — refused per coding-standards §7.2.

            # ── Baseline producer (2026-05-27, revised) ──
            #
            # build_workflow_graph runs the source loads (PG queries) and
            # builds the structural graph. We surface per-source progress
            # via on_source_loaded so /api/graph/progress shows the work
            # in flight ("loaded 107043 memories") instead of a silent
            # spinner, and we defer the native tree-sitter AST parse
            # (defer_native_ast=True) — that parse was 58.6 s of a 99 s
            # build; AST symbols arrive via the L6 AP loop below instead.
            #
            # We do NOT use the per-source on_batch push here: routing
            # every node/edge of the huge memories batch (107k nodes +
            # 107k edges) through the LayoutAuthority synchronously in the
            # build thread pinned a core for minutes with no SSE consumer
            # attached. Instead we take the returned dict and publish it
            # in ONE _merge into the cumulative cache, then flip the
            # baseline phases ready. The client renders from the cache
            # (the unified-viz phase poller's baseline-ready fallback
            # fetches /api/graph). The on_batch / LayoutAuthority SSE
            # path remains available for the streaming_canvas renderer
            # once its large-batch performance is addressed.
            _stream_pct = {"v": 0.02}  # progress monotone within 0.02–0.28

            # Live event stream — every per-source batch lands here as
            # chunked SSE events the moment the builder emits it. The
            # first visit's browser subscribes to /api/graph/events and
            # appendGraphDelta's each event, so the user watches the
            # graph grow instead of waiting for the full ingest to
            # finish. RESET on every kicked build so a previous build's
            # tail events don't leak into this run's subscribers.
            from cortex_viz.server import graph_event_stream as _events

            _events.reset()
            _source_totals.clear()

            from cortex_viz.handlers.workflow_graph import _node_to_dict

            def _on_source_loaded(label: str, count: int) -> None:
                _source_totals[label] = count
                _stream_pct["v"] = min(0.28, _stream_pct["v"] + 0.02)
                _set_progress(
                    phase=f"loading {label}",
                    pct=_stream_pct["v"],
                    message=f"loaded {count} {label}",
                )

            # The raw per-source streams repeat items heavily — one
            # discussion node was emitted 3,782 times by its source
            # (measured 2026-06-12); the cache dedups on merge, but
            # _on_batch bypasses the cache, so the wire paid for every
            # repeat. Each id/edge-key goes over the live wire ONCE
            # from _on_batch; the post-bake baseline _merge re-emission
            # (which carries the coordinates the live copies lack) is
            # NOT filtered by these sets, so every node still arrives
            # at most twice: once live, once coordinated.
            _wire_seen_n: set = set()

            def _on_batch(label: str, nodes_objs, edges_objs) -> None:
                """Push per-source batch onto the SSE event queue, in the
                slim wire projection, deduped at the wire level.

                Intentionally no _merge per item — the cumulative cache
                is populated by ONE _merge after build completion. These
                live batches carry null x/y (layout runs after the
                load); the post-bake baseline _merge re-emits the same
                ids WITH coordinates and the client backfills positions
                on the deduped nodes.
                """
                if not nodes_objs and not edges_objs:
                    return
                n_slim = []
                for n in nodes_objs:
                    d = _node_to_dict(n)
                    nid = d.get("id")
                    if nid and nid not in _wire_seen_n:
                        _wire_seen_n.add(nid)
                        n_slim.append(_slim_node(d))
                if n_slim:
                    _events.emit(label, n_slim, [], chunk=1000)

            # ── Stage 1: skeleton (≪1 s) → first paint ──
            # stage="skeleton" loads only skills + hooks, no memories, no
            # tool_events, no AST. The builder still produces the domain
            # hubs (via _ensure_domain on every node's domain_id), so the
            # client immediately sees the structural backbone instead of
            # waiting ~1–3 min for the full ingest on a large DB.
            try:
                skeleton = build_workflow_graph(
                    store,
                    domain_filter=domain_filter,
                    stage="skeleton",
                    defer_native_ast=True,
                )
            except Exception as _exc:  # pragma: no cover - defensive
                print(
                    f"[cortex] skeleton build failed: {_exc}",
                    file=sys.stderr,
                )
                skeleton = {"nodes": [], "edges": [], "meta": {}}

            _merge(
                skeleton.get("nodes", []),
                skeleton.get("edges", []),
                stage="skeleton",
                pct=0.05,
                message=(
                    f"skeleton: {len(skeleton.get('nodes', []))} nodes / "
                    f"{len(skeleton.get('edges', []))} edges"
                ),
                phase_key=None,
                baseline_ready=True,
            )
            for _phase_key in ("L0", "L1"):
                _mark_phase_ready(_phase_key)

            # ── Stage 2: full baseline (load + ingest the heavy sources) ──
            # Replaces the cumulative cache with the full graph. The client
            # already painted the skeleton; this fills it in.
            # Default UNCAPPED (user direction 2026-06-12): the 25k
            # hottest-memory cap was a workaround for the fat-JSON wire
            # where every record shipped whole; with the slim wire,
            # on-demand detail, and slim-dict retention in the builder
            # (workflow_graph.py memory loop) the full corpus is tens
            # of MB, and a hard cap is a truncation of the record.
            # CORTEX_VIZ_MEMORY_LIMIT > 0 remains available as an
            # explicit opt-in subset for constrained machines.
            try:
                _mem_limit = int(os.environ.get("CORTEX_VIZ_MEMORY_LIMIT", "0"))
            except ValueError:
                _mem_limit = 0
            baseline = build_workflow_graph(
                store,
                domain_filter=domain_filter,
                stage="full",
                on_source_loaded=_on_source_loaded,
                on_batch=_on_batch,
                defer_native_ast=True,
                memory_limit=_mem_limit,
            )

            # ── Bake layout coordinates BEFORE the baseline merge ──
            # The merge's SSE emission snapshots each node into the slim
            # wire tuple, so coordinates must exist at emission time.
            # (The bake used to run after the merge and mutate the dicts
            # the event buffer shared by reference — replay clients saw
            # coords, live clients never did. Bake-before-merge gives
            # both the same coordinated baseline.) One DrL pass (OpenOrd;
            # ~0.8 s for 34k nodes, measured 2026-05-31); on failure the
            # client still settles its own live layout, just slower.
            try:
                from cortex_viz.core import layout_engine

                _bl_nodes = baseline.get("nodes", [])
                _bl_edges = baseline.get("edges", [])
                # DrL over the STRUCTURAL BACKBONE only. DrL (OpenOrd) is
                # superlinear in node count (measured: 15k nodes ≈ 12s, 58k
                # nodes ≈ 58s — and it holds the GIL the whole time). Feeding
                # it every non-memory node (~58k) is what froze the build.
                # The backbone — domains, tool hubs, files — is the skeleton
                # that actually needs a force-directed layout (~10-15k nodes,
                # ≈12s). Everything else (memories AND the other non-backbone
                # nodes: symbols, discussions, commands, agents, skills, …)
                # gets deterministic O(1) ray placement around its anchor,
                # exactly like the L6 symbols. source: superlinear DrL cost,
                # OpenOrd (Martin et al., SPIE 2011); cap measured 2026-06-14.
                _BACKBONE_KINDS = {"domain", "tool_hub", "file"}

                def _node_kind(_n: dict) -> str:
                    return _n.get("kind") or _n.get("type") or ""

                _ids = [
                    n["id"]
                    for n in _bl_nodes
                    if n.get("id") and _node_kind(n) in _BACKBONE_KINDS
                ]
                _id_set = set(_ids)
                _edge_pairs = []
                for _e in _bl_edges:
                    _s = _e.get("source")
                    _t = _e.get("target")
                    if isinstance(_s, dict):
                        _s = _s.get("id")
                    if isinstance(_t, dict):
                        _t = _t.get("id")
                    if _s and _t and _s != _t and _s in _id_set and _t in _id_set:
                        _edge_pairs.append((_s, _t))
                _coords = layout_engine.layout(_ids, _edge_pairs)
                _pos = {nid: (x, y) for nid, x, y in _coords}
                # Backbone nodes get their baked DrL coordinate.
                for _n in _bl_nodes:
                    _xy = _pos.get(_n.get("id"))
                    if _xy is not None:
                        _n["x"], _n["y"] = _xy[0], _xy[1]
                # Everything else (memories + non-backbone structural nodes:
                # symbols, discussions, commands, agents, skills, …) gets
                # deterministic O(1) ray placement around its domain hub's
                # baked coord — same path as the L6 symbols. Nodes whose
                # domain hub didn't get a coord fall back to a ray around the
                # origin so EVERY node still carries a server-side position.
                _placed_ray = 0
                for _n in _bl_nodes:
                    if _n.get("id") in _id_set:
                        continue  # backbone — already baked
                    _hub = _pos.get(_n.get("domain_id")) or (0.0, 0.0)
                    _n["x"], _n["y"] = _place_around(
                        _hub[0], _hub[1], str(_n.get("id"))
                    )
                    _placed_ray += 1
                _placed_mem = _placed_ray
                print(
                    f"[cortex] layout baked: {len(_coords)} structural coords"
                    f" + {_placed_mem} memory rays for {len(_bl_nodes)} nodes",
                    file=sys.stderr,
                )
            except Exception as _exc:  # pragma: no cover - defensive
                print(
                    f"[cortex] layout bake skipped: {_exc}",
                    file=sys.stderr,
                )

            _merge(
                baseline.get("nodes", []),
                baseline.get("edges", []),
                stage="baseline",
                pct=0.30,
                message=(
                    f"baseline: {len(baseline.get('nodes', []))} nodes / "
                    f"{len(baseline.get('edges', []))} edges"
                ),
                phase_key=None,
            )
            for _phase_key in ("L0", "L1", "L2", "L3", "L4", "L5"):
                _mark_phase_ready(_phase_key)
            _set_progress(
                phase="baseline_ready",
                pct=0.30,
                message=(
                    f"baseline ready: {len(baseline.get('nodes', []))} nodes / "
                    f"{len(baseline.get('edges', []))} edges"
                ),
                baseline_ready=True,
            )

            # NO _events.close() here. Closing the stream at baseline
            # made every SSE subscriber receive ``done`` and disconnect
            # BEFORE a single L6 symbol streamed — the galaxy froze at
            # the baseline scale forever (observed 2026-06-12). The
            # stream closes exactly once, in the finally block below,
            # after the FULL build (through L6_CROSS) or on error.

            # L6 — AST per project, per 200-symbol batch.
            from cortex_viz.core.workflow_graph_palette import (
                SYMBOL_COLOR_DEFAULT,
                SYMBOL_COLORS,
            )
            from cortex_viz.core.workflow_graph_schema import (
                NodeIdFactory,
                edge_provenance_defaults,
            )
            from cortex_viz.infrastructure.ap_bridge import (
                is_enabled as _ap_enabled,
                resolve_graph_paths,
            )
            from cortex_viz.infrastructure.workflow_graph_source_ast import (
                WorkflowGraphASTSource,
            )

            if not _ap_enabled():
                _set_progress(
                    phase="full_ready",
                    pct=1.0,
                    message=f"ready: {len(baseline.get('nodes', []))} nodes "
                    "(AP disabled)",
                    full_ready=True,
                )
                # finally block closes the SSE stream.
                return

            # File-path → file-id map for DEFINED_IN edge resolution.
            file_id_by_path: dict[str, str] = {}
            for n in baseline.get("nodes", []):
                if n.get("kind") == "file":
                    p = n.get("path") or ""
                    fid = n.get("id")
                    if p and fid:
                        file_id_by_path[p] = fid
                        parts = p.split("/")
                        for i in range(1, len(parts)):
                            file_id_by_path.setdefault("/".join(parts[i:]), fid)

            ast_source = WorkflowGraphASTSource()
            graph_paths = resolve_graph_paths()
            total = max(len(graph_paths), 1)
            import hashlib
            import json as _json
            from pathlib import Path as _Path

            _BATCH = 200

            # ── Per-project AST cache ──
            # AP parses tree-sitter once per project and writes the
            # result into LadybugDB at ``~/.cortex/ap_graphs/<proj>/graph``.
            # Cortex then queries AP to pull the symbols + edges back out
            # for visualization. When nothing has changed in the underlying
            # graph files, the second-query result is identical — so we
            # cache it to disk and short-circuit the AP round-trip entirely.
            #
            # Key = SHA-256 of the graph directory's (path, size, mtime)
            # triples for every file inside. The instant any AP file
            # changes (re-index happened) the key differs and we refetch.
            _CACHE_DIR = _Path.home() / ".claude" / "methodology" / "ast_cache"
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)

            def _graph_signature(gp_: str) -> str:
                root = _Path(gp_)
                if not root.exists():
                    return ""
                h = hashlib.sha256()
                # Walk deterministically so the signature is stable.
                for f in sorted(root.rglob("*")):
                    if not f.is_file():
                        continue
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    rel = str(f.relative_to(root))
                    h.update(rel.encode())
                    h.update(str(st.st_size).encode())
                    h.update(str(int(st.st_mtime)).encode())
                return h.hexdigest()[:16]

            def _cache_path(proj_name_: str) -> _Path:
                return _CACHE_DIR / f"{proj_name_}.json"

            def _cache_load(proj_name_: str, sig_: str):
                p = _cache_path(proj_name_)
                if not p.is_file() or not sig_:
                    return None
                try:
                    data = _json.loads(p.read_text())
                except Exception:
                    return None
                if data.get("signature") != sig_:
                    return None
                return data.get("symbols") or [], data.get("edges") or []

            def _cache_store(
                proj_name_: str, sig_: str, syms_: list, edgs_: list
            ) -> None:
                if not sig_:
                    return
                try:
                    _cache_path(proj_name_).write_text(
                        _json.dumps(
                            {
                                "signature": sig_,
                                "symbols": syms_,
                                "edges": edgs_,
                            }
                        )
                    )
                except Exception:
                    pass

            async def _load_with_timeout(gp_):
                # Finite per-project ceiling so one wedged AP subprocess (or a
                # pathological repo) cannot hang the whole build. On timeout
                # asyncio raises TimeoutError, which the caller's except below
                # turns into "mark this project's phase ready + continue" — the
                # build always reaches "done" and the other projects still load.
                import asyncio as _asyncio

                async def _load():
                    syms = await ast_source._load_symbols_async(gp_, [])
                    edgs = await ast_source._load_edges_async(gp_, [])
                    return syms, edgs

                return await _asyncio.wait_for(_load(), timeout=_L6_PROJECT_TIMEOUT_S)

            # L6 runs ONE PHASE PER PROJECT so the graph grows
            # project-by-project: finish indexing project A → publish
            # its symbol nodes + intra-project edges as phase
            # ``L6:A`` → client appends → next project. Cross-project
            # edges (rare: an ``imports`` pointing at a symbol that
            # lives in a different project's AST) are batched into
            # ``L6_CROSS`` at the very end when every project phase
            # is ready.
            _proj_names: list[str] = []
            for gp in graph_paths:
                pn = str(gp).rsplit("/", 3)[-2] if "/" in str(gp) else str(gp)
                _proj_names.append(pn)
                _register_phase(
                    f"L6:{pn}",
                    deps=["L3"],
                    label=f"L6 {pn} symbols",
                )
            _register_phase(
                "L6_CROSS",
                deps=[f"L6:{pn}" for pn in _proj_names],
                label="L6 cross-project edges",
            )

            # Track which symbols exist per-project so we can route
            # each edge into the right phase. An edge is "intra" iff
            # both endpoints are symbols indexed in THIS project.
            proj_symbol_ids: dict[str, set] = {pn: set() for pn in _proj_names}
            cross_edges: list[dict] = []

            for i, gp in enumerate(graph_paths):
                proj_name = _proj_names[i]
                phase_key = f"L6:{proj_name}"
                if not _phase_deps_satisfied(phase_key):
                    continue  # waiting for L3 — shouldn't happen here

                # Tight coupling with AP: if the underlying LadybugDB
                # graph hasn't changed (signature match), we already
                # know the answer — load from disk, skip the AP call.
                sig = _graph_signature(gp)
                cached = _cache_load(proj_name, sig)
                if cached is not None:
                    syms, edgs = cached
                    _set_progress(
                        phase=f"L6 {i + 1}/{total} {proj_name}",
                        pct=0.30 + 0.65 * ((i + 1) / total),
                        message=f"{proj_name}: cached ({len(syms)} symbols)",
                    )
                else:
                    try:
                        syms, edgs = ast_source._loop_owner.run(_load_with_timeout(gp))
                    except Exception as exc:
                        print(
                            f"[cortex] L6 project {proj_name} skipped: "
                            f"{type(exc).__name__}: {exc}",
                            file=sys.stderr,
                        )
                        _set_progress(
                            phase=f"L6 {i + 1}/{total} {proj_name}",
                            pct=0.30 + 0.65 * ((i + 1) / total),
                            message=f"{proj_name}: error — {type(exc).__name__}",
                        )
                        _mark_phase_ready(phase_key)
                        continue
                    # Persist for the next run.
                    _cache_store(proj_name, sig, list(syms), list(edgs))

                # Each symbol belongs to ITS PROJECT's domain — not the
                # global hub. The L0 phase emits domain ids as
                # ``domain:<kebab-case-label>`` (see
                # ``shared.project_ids.domain_id_from_label``); we match
                # that slugging here so symbol→domain routing lines up
                # with the existing domain nodes in the cache.
                from cortex_viz.shared.project_ids import (
                    domain_id_from_label,
                )

                proj_slug = domain_id_from_label(proj_name) or proj_name
                proj_domain_id = f"domain:{proj_slug}"

                proj_nodes: list[dict] = []
                proj_edges: list[dict] = []

                # Every AST-indexed file is also a REAL file that can
                # be read/edited by Claude tools — same entity as an
                # L3 file. If L3 didn't see this file (never touched
                # during a tool call), emit it as a project-scoped
                # file node here so the symbol has a parent to attach
                # to and the file appears in the domain's file ring.
                ap_file_paths: set[str] = set()
                for sym in syms:
                    fp_ = sym.get("file_path") or ""
                    if fp_:
                        ap_file_paths.add(fp_)
                # Anchor for this project's coordinate placement: the
                # domain hub's baked coordinate (the DrL pass covered
                # the baseline, which includes every SESSION domain).
                _hub = _node_index.get(proj_domain_id) or {}
                _hub_xy = (
                    (_hub.get("x"), _hub.get("y"))
                    if _hub.get("x") is not None and _hub.get("y") is not None
                    else None
                )
                if _hub_xy is None:
                    # AP-only project (indexed code with no session
                    # history): no baseline domain node exists, so the
                    # placement chain (hub -> files -> symbols) dead-
                    # ended and 90,225 symbols shipped with NO
                    # coordinates (measured on the wire 2026-06-13) —
                    # the client fell back to simulation mode. Place
                    # the project hub deterministically on the outer
                    # ring: the DrL bake normalises to <=~0.91
                    # (layout_engine 0.55-span padding), so radius 0.9
                    # sits at the layout's edge; DJB2(domain id) sets
                    # the angle so projects spread.
                    _h = int(simple_hash(proj_domain_id), 16)
                    _ang = (_h % 3600) / 3600.0 * 2.0 * math.pi
                    _hub_xy = (
                        round(0.9 * math.cos(_ang), 4),
                        round(0.9 * math.sin(_ang), 4),
                    )
                    if not _hub:
                        proj_nodes.append(
                            {
                                "id": proj_domain_id,
                                "kind": "domain",
                                "type": "domain",
                                "label": proj_slug or proj_name,
                                "domain_id": proj_domain_id,
                                "domain": proj_slug,
                                "x": _hub_xy[0],
                                "y": _hub_xy[1],
                            }
                        )
                    else:
                        # Exists but never placed — set coordinates on
                        # the cached record so the chain below resolves.
                        _hub["x"], _hub["y"] = _hub_xy

                for fp_ in ap_file_paths:
                    if file_id_by_path.get(fp_):
                        continue
                    fid = NodeIdFactory.file_id(fp_)
                    file_id_by_path[fp_] = fid
                    # Also register every path-tail variant so the
                    # later symbol → file lookup still works when AP
                    # and L3 disagree on absolute vs relative paths.
                    parts = fp_.split("/")
                    for i in range(1, len(parts)):
                        file_id_by_path.setdefault("/".join(parts[i:]), fid)
                    _fnode = {
                        "id": fid,
                        "kind": "file",
                        "type": "file",
                        "label": fp_.rsplit("/", 1)[-1],
                        "path": fp_,
                        "domain_id": proj_domain_id,
                        "domain": proj_slug,
                    }
                    if _hub_xy is not None:
                        _fnode["x"], _fnode["y"] = _place_around(
                            _hub_xy[0], _hub_xy[1], fid
                        )
                    proj_nodes.append(_fnode)
                    # Bind the file to its domain so L3-layout places
                    # it in the domain's file ring.
                    proj_edges.append(
                        {
                            "source": fid,
                            "target": proj_domain_id,
                            "kind": "in_domain",
                            "type": "in_domain",
                            "weight": 1.0,
                        }
                    )

                # Coordinates of the files created in THIS loop — they
                # are not in _node_index until the merge, but their
                # symbols are placed right below.
                _local_file_xy = {
                    n["id"]: (n["x"], n["y"])
                    for n in proj_nodes
                    if n.get("kind") == "file" and n.get("x") is not None
                }

                def _file_xy(fid_: str | None) -> tuple[float, float] | None:
                    if not fid_:
                        return None
                    cached = _node_index.get(fid_)
                    if (
                        cached
                        and cached.get("x") is not None
                        and cached.get("y") is not None
                    ):
                        return (cached["x"], cached["y"])
                    return _local_file_xy.get(fid_)

                for sym in syms:
                    qn = sym.get("qualified_name") or ""
                    fp = sym.get("file_path") or ""
                    if not qn:
                        continue
                    sid = NodeIdFactory.symbol_id(fp, qn)
                    proj_symbol_ids[proj_name].add(sid)
                    stype = str(sym.get("symbol_type") or "function")
                    _snode = {
                        "id": sid,
                        "kind": "symbol",
                        "type": "symbol",
                        "label": qn.rsplit("::", 1)[-1] or qn,
                        "color": SYMBOL_COLORS.get(stype, SYMBOL_COLOR_DEFAULT),
                        "path": fp,
                        "symbol_type": stype,
                        "domain_id": proj_domain_id,
                        "domain": proj_slug,
                    }
                    # Server-side position: ray around the parent file's
                    # coordinate (baked L3 file or just-placed L6 file),
                    # falling back to the domain hub. Every node on the
                    # wire carries a position — the client draws, it
                    # does not simulate.
                    _axy = _file_xy(file_id_by_path.get(fp)) or _hub_xy
                    if _axy is not None:
                        _snode["x"], _snode["y"] = _place_around(_axy[0], _axy[1], sid)
                    proj_nodes.append(_snode)
                    parent = file_id_by_path.get(fp)
                    if parent:
                        # Gap 6: shared provenance defaults.
                        di_conf, di_reason = edge_provenance_defaults("defined_in")
                        proj_edges.append(
                            {
                                "source": sid,
                                "target": parent,
                                "kind": "defined_in",
                                "type": "defined_in",
                                "weight": 1.0,
                                "confidence": di_conf,
                                "reason": di_reason,
                            }
                        )
                for e in edgs:
                    sf = e.get("src_file") or ""
                    sn = e.get("src_name") or ""
                    df = e.get("dst_file") or ""
                    dn = e.get("dst_name") or ""
                    if not df or not dn:
                        continue
                    did = NodeIdFactory.symbol_id(df, dn)
                    kind = e.get("kind") or "calls"
                    if kind == "imports":
                        sid = file_id_by_path.get(sf)
                        if not sid:
                            continue
                    else:
                        if not sf or not sn:
                            continue
                        sid = NodeIdFactory.symbol_id(sf, sn)
                    # Gap 6: single source-of-truth defaults.
                    conf, reason_v = edge_provenance_defaults(
                        kind,
                        ap_confidence=e.get("confidence"),
                        ap_reason=e.get("reason"),
                    )
                    edge = {
                        "source": sid,
                        "target": did,
                        "kind": kind,
                        "type": kind,
                        "weight": 1.0,
                        "confidence": conf,
                        "reason": reason_v,
                    }
                    # Intra-project iff both endpoints (where they are
                    # symbols) belong to THIS project. For `imports`
                    # the source is a file id, always "intra" once we
                    # see it here.
                    src_ok = kind == "imports" or sid in proj_symbol_ids[proj_name]
                    tgt_ok = did in proj_symbol_ids[proj_name]
                    if src_ok and tgt_ok:
                        proj_edges.append(edge)
                    else:
                        cross_edges.append(edge)

                # Stream this project's nodes in batches (smooth fade-in),
                # then its intra-project edges at the end.
                # No pacing between batches. The wait_for_clear(1.0)
                # consult that used to sit here throttled against the
                # LayoutAuthority's overload flag — but the authority
                # has NO consumer (/api/graph/stream is not routed), so
                # once tripped the flag never cleared and EVERY batch
                # burned the full 1 s timeout: ~3,350 batches ≈ an hour
                # of pure sleep, indistinguishable from a deadlock
                # (observed 2026-06-12). SSE chunking (emit chunk=1000)
                # is the pacing now.
                for bstart in range(0, len(proj_nodes), _BATCH):
                    chunk_nodes = proj_nodes[bstart : bstart + _BATCH]
                    _merge(
                        chunk_nodes,
                        [],
                        stage=f"L6 {i + 1}/{total} {proj_name}",
                        pct=0.30 + 0.65 * ((i + 1) / total),
                        message=(f"{proj_name}: +{len(chunk_nodes)} symbols"),
                        phase_key=phase_key,
                    )
                # Intra-project edges land in the same project phase,
                # but only AFTER all its nodes — the client's dangling-
                # edge filter handles any slack.
                if proj_edges:
                    _merge(
                        [],
                        proj_edges,
                        stage=f"L6 {i + 1}/{total} {proj_name}",
                        pct=0.30 + 0.65 * ((i + 1) / total),
                        message=(f"{proj_name}: +{len(proj_edges)} AST edges"),
                        phase_key=phase_key,
                    )
                _mark_phase_ready(phase_key)

            # Cross-project edges — deps on every L6:<proj> phase.
            if not _phase_deps_satisfied("L6_CROSS"):
                return
            for bstart in range(0, len(cross_edges), 2000):
                chunk = cross_edges[bstart : bstart + 2000]
                _merge(
                    [],
                    chunk,
                    stage="L6 cross-edges",
                    pct=min(0.99, 0.95 + 0.04 * (bstart / max(len(cross_edges), 1))),
                    message=(
                        f"cross-project edges: +{len(chunk)} "
                        f"({bstart + len(chunk)}/{len(cross_edges)})"
                    ),
                    phase_key="L6_CROSS",
                )
            _mark_phase_ready("L6_CROSS")

            # Done.
            cur = _graph_cache["data"]
            counts = cur["meta"].get("counts") or {}
            counts["symbols"] = sum(
                1 for n in cur["nodes"] if n.get("kind") == "symbol"
            )
            counts["ast_edges"] = sum(
                1
                for e in cur["edges"]
                if (e.get("kind") or "")
                in ("defined_in", "calls", "imports", "member_of")
            )
            # Knowledge-graph entities + their MEMORY→ENTITY links
            # (ADR-0046 Gap 10 wiring). Counted at the finalisation step
            # so the stat panel's ``entities`` and
            # ``memory_entity_edges`` fields stay in sync with what the
            # renderer actually shows.
            counts["entities"] = sum(
                1 for n in cur["nodes"] if n.get("kind") == "entity"
            )
            counts["memory_entity_edges"] = sum(
                1 for e in cur["edges"] if (e.get("kind") or "") == "about_entity"
            )
            cur["meta"]["counts"] = counts
            _set_progress(
                phase="full_ready",
                pct=1.0,
                message=(
                    f"ready: {len(cur['nodes'])} nodes ({counts['symbols']} symbols)"
                ),
                full_ready=True,
                node_count=len(cur["nodes"]),
                edge_count=len(cur["edges"]),
            )
        except Exception as exc:  # pragma: no cover
            print(f"[cortex] background build error: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            _set_progress(
                phase="error",
                message=f"{type(exc).__name__}: {exc}",
            )
        finally:
            # Single end-of-stream terminator — success, AP-disabled
            # early return, or error. Subscribers drain whatever was
            # emitted, receive ``done``, and close. close() is
            # idempotent; the buffer survives for late-subscriber
            # replay until the next build's reset().
            try:
                from cortex_viz.server import graph_event_stream as _ev

                _ev.close()
            except Exception:
                pass
            _graph_build_lock.release()

    threading.Thread(target=_run, name="cortex-graph-build", daemon=True).start()


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
    global _graph_roster_fingerprint
    params = parse_graph_query(path)
    domain_filter = params["domain_filter"]
    current_fp = _roster_fingerprint()
    roster_changed = current_fp != _graph_roster_fingerprint
    build_in_progress = _graph_build_lock.locked()
    cache_has_data = bool(
        _graph_cache
        and _graph_cache.get("data")
        and _graph_cache.get("domain_filter") == domain_filter
    )

    # Never re-kick while a build is running — the background thread
    # owns the AST loop, and double-triggering it would reset all
    # phase state mid-stream.
    # Also never re-kick if we already have a completed graph whose
    # roster hasn't changed — it's still current.
    if build_in_progress or (cache_has_data and not roster_changed):
        if cache_has_data:
            return _graph_cache["data"]
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

    # Route the (re)build to the child PROCESS — never run the GIL-hogging
    # in-process build in the SERVER process. On roster_changed we still
    # return the existing cache immediately below (never block on the kick).
    url = getattr(store, "_url", None)
    if url:
        from cortex_viz.server import build_process

        build_process.start_build(url, domain_filter)

    # If there's any cache at all (stale TTL or prior domain), return
    # it — better than an empty graph. Otherwise placeholder.
    if _graph_cache and _graph_cache.get("data"):
        return _graph_cache["data"]

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


def _get_cached_conversations() -> list[dict]:
    """Shared cache wrapper — refreshes via ``discover_conversations``."""
    cached, ts = get_cached_conversations_state()
    now = time.time()
    if cached is None or (now - ts) > CONVERSATIONS_CACHE_TTL:
        from cortex_viz.infrastructure.scanner import discover_conversations

        cached = discover_conversations()
        set_cached_conversations_state(cached, now)
    return cached


def build_discussions_response(path: str) -> dict:
    """Paginated response for ``/api/discussions``."""
    from cortex_viz.core.graph_builder_discussions import build_discussion_nodes

    params = parse_discussion_params(path)
    conversations = _get_cached_conversations()
    if params["project"]:
        conversations = [
            c for c in conversations if c.get("project") == params["project"]
        ]
    conversations = sorted(
        conversations,
        key=lambda c: c.get("startedAt") or "",
        reverse=True,
    )
    total = len(conversations)
    batch_size = max(1, params["batch_size"])
    batch = params["batch"]
    total_batches = max(1, (total + batch_size - 1) // batch_size)
    start = batch * batch_size
    end = start + batch_size
    page = conversations[start:end]
    nodes, edges = build_discussion_nodes(page, _cached_domain_hub_ids)
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "total": total,
            "batch": batch,
            "batch_size": batch_size,
            "total_batches": total_batches,
        },
    }


def _find_session_file(session_id: str):
    """Whitelist scan of every project dir for ``<session_id>.jsonl``."""
    from cortex_viz.infrastructure.config import CLAUDE_DIR

    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.is_dir():
        return None
    target = session_id + ".jsonl"
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / target
        if candidate.is_file():
            return candidate
    return None


def build_discussion_detail(session_id: str) -> dict:
    """Detail response for ``/api/discussion/<session_id>``."""
    from cortex_viz.infrastructure.conversation_reader import (
        format_conversation_messages,
        read_full_conversation,
    )

    conversations = _get_cached_conversations()
    conv = next(
        (c for c in conversations if c.get("sessionId") == session_id),
        None,
    )
    if conv is None:
        return {"error": "Discussion not found", "sessionId": session_id}

    found_path = _find_session_file(session_id)
    if found_path is None:
        return {"error": "Session file not found", "sessionId": session_id}

    raw = read_full_conversation(str(found_path))
    messages = format_conversation_messages(raw)
    return {
        "sessionId": session_id,
        "project": conv.get("project"),
        "messages": messages,
        "startedAt": conv.get("startedAt"),
        "endedAt": conv.get("endedAt"),
        "duration": conv.get("duration"),
        "turnCount": conv.get("turnCount"),
    }
