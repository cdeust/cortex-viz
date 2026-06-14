"""Server-process appliers + read accessors over the shared graph cache.

Extracted verbatim from ``http_standalone_graph.py``. The drain thread
(``build_process._drain``) replays forwarded child messages through
``begin_epoch`` / ``apply_*``; the HTTP endpoints read through
``get_node_record`` / ``get_node_neighbors`` / ``get_build_progress`` /
``get_phase_payload`` / ``get_graph_slice``.

All shared mutable state lives in ``graph_cache_state`` (the single owner).
The prior ``global X`` declarations are replaced by ``state.X`` reads and
direct attribute assignment ``state.X = ...`` — behaviour-identical to the
in-module pattern (build_process already writes ``g._SINK_Q = q``).
"""

from __future__ import annotations

import time

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_wire import _slim_node


def begin_epoch(epoch: int) -> None:
    """SERVER-side single reset point for a new build epoch.

    Pre: called by build_process.start_build BEFORE the child is spawned /
    the drain thread starts, so the empty state is published before any
    delta of the new epoch can arrive.
    Post: _SERVER_EPOCH == epoch; the graph cache is empty; all dedup /
    index / adjacency / phase state is cleared; the FIXED phases are
    pending; dynamic L6:* phases are dropped; _build_progress is reset.

    Atomic publish: the empty cache is swapped under _apply_lock so a
    concurrent /api/graph read never observes a half-cleared cache.
    """
    with state._apply_lock:
        state._SERVER_EPOCH = int(epoch)
        state._graph_cache = {
            "data": {"nodes": [], "edges": [], "links": [], "meta": {}},
            "domain_filter": None,
        }
        state._graph_cache_ts = time.monotonic()
        state._applied_node_ids.clear()
        state._node_index.clear()
        state._adjacency.clear()
        # Drop dynamic L6 phases from the prior build, reset fixed-phase
        # buffers, and flip every fixed phase pending.
        for _k in list(state.PHASES):
            if _k.startswith("L6:") or _k == "L6_CROSS":
                state.PHASES.pop(_k, None)
                state._phase_payloads.pop(_k, None)
            else:
                state.PHASES[_k]["ready"] = False
        for _k in list(state._phase_payloads):
            state._phase_payloads[_k] = {"nodes": [], "edges": []}
    with state._build_progress_lock:
        state._build_progress.update(
            {
                "phase": "starting",
                "phase_seq": 0,
                "pct": 0.0,
                "message": "",
                "baseline_ready": False,
                "full_ready": False,
                "node_count": 0,
                "edge_count": 0,
                "started_at": time.monotonic(),
                "elapsed": 0.0,
                "phases": {k: False for k in state.PHASES},
            }
        )


def apply_progress(epoch: int, snap: dict) -> None:
    if epoch != state._SERVER_EPOCH:
        return  # stale build — drop
    with state._build_progress_lock:
        # started_at is SERVER-owned (set by begin_epoch on the server's
        # monotonic clock). The child runs on a DIFFERENT monotonic origin,
        # so a forwarded started_at would make get_build_progress compute a
        # garbage elapsed. Drop it.
        snap = {k: v for k, v in snap.items() if k != "started_at"}
        # phase_seq must be monotone: an out-of-order drain (or a stale
        # forwarded snapshot) must never roll the client's seq backwards,
        # or it would re-fetch an older graph. Take the max of current and
        # incoming.
        incoming_seq = snap.get("phase_seq")
        state._build_progress.update(snap)
        if incoming_seq is not None:
            state._build_progress["phase_seq"] = max(
                state._build_progress.get("phase_seq", 0), incoming_seq
            )


def apply_delta(
    epoch: int, phase_key: str | None, stage: str, nodes: list, edges: list
) -> None:
    """Append a forwarded build delta into the server-process cache AND
    re-emit it to the live /api/graph/events subscribers. O(batch).

    INV-NODE (Liskov node-shape contract): the cache always holds FULL
    DICTS. The cross-process delta carries full dicts (``nodes``); the slim
    ``[id,kind,x,y]`` projection is ONLY the SSE wire format, produced here
    on emit. So apply_delta:
      * stores full dicts in _graph_cache["data"]["nodes"];
      * maintains _node_index[nid]=node and _adjacency for each edge, so
        /api/graph/node and /api/graph/node neighbors resolve mid-build;
      * reconstructs the per-phase buffer from the delta stream by appending
        fresh nodes/edges into _phase_payloads[phase_key] (the slim wire
        drops phase membership; the real phase_key rides as a field);
      * emits SLIM nodes to the SSE stream only.

    Atomic publish: build the new node/edge lists privately, then swap the
    _graph_cache reference under _apply_lock. /api/graph readers see either
    the pre-delta dict or the post-delta dict, never a torn intermediate.
    """
    if epoch != state._SERVER_EPOCH:
        return  # stale build — drop
    with state._apply_lock:
        old = state._graph_cache["data"] if state._graph_cache else None
        new_nodes = list(old["nodes"]) if old else []
        new_edges = list(old["edges"]) if old else []
        new_meta = dict(old["meta"]) if old and old.get("meta") else {}
        fresh: list[dict] = []
        for n in nodes:
            nid = n.get("id")
            if nid and nid not in state._applied_node_ids:
                new_nodes.append(n)
                state._applied_node_ids.add(nid)
                state._node_index[nid] = n
                fresh.append(n)
        for e in edges:
            new_edges.append(e)
            s, t, ek = e.get("source"), e.get("target"), e.get("kind")
            if s and t:
                state._adjacency.setdefault(s, []).append((t, ek, "out"))
                state._adjacency.setdefault(t, []).append((s, ek, "in"))
        new_meta["node_count"] = len(new_nodes)
        new_meta["edge_count"] = len(new_edges)
        cur = {
            "nodes": new_nodes,
            "edges": new_edges,
            "links": new_edges,
            "meta": new_meta,
        }
        state._graph_cache = {"data": cur, "domain_filter": None}
        state._graph_cache_ts = time.monotonic()
        # Rebuild the server-side per-phase buffer from the delta stream.
        # _phase_payloads is authoritative for /api/graph/phase on the
        # server; the slim wire dropped phase membership so the real
        # phase_key is forwarded as a field and reassembled here.
        if phase_key:
            buf = state._phase_payloads.setdefault(
                phase_key, {"nodes": [], "edges": []}
            )
            buf["nodes"].extend(fresh)
            buf["edges"].extend(edges)
    try:
        from cortex_viz.server import graph_event_stream as _ev

        if fresh:
            _ev.emit(stage, [_slim_node(n) for n in fresh], [], chunk=1000)
    except Exception:  # pragma: no cover - defensive
        pass


def apply_phase_ready(epoch: int, phase_key: str, phase_seq: int) -> None:
    """SERVER-side: flip a phase ready in response to a forwarded
    ``phase_ready`` message. phase_seq is taken as max so an out-of-order
    drain never rolls the client's snapshot pointer backwards."""
    if epoch != state._SERVER_EPOCH:
        return  # stale build — drop
    # Dynamic L6:* / L6_CROSS phases are registered in the CHILD only; the
    # child's PHASES dict does not cross the process boundary. Create the
    # server-side entry on first phase_ready so /api/graph/phase reports the
    # authoritative ready flag (not the node_total>0 fallback). deps are not
    # needed server-side — the server publishes whatever the child marked
    # ready; the dependency gate is enforced in the child build loop.
    if phase_key not in state.PHASES:
        state.PHASES[phase_key] = {"deps": [], "ready": True, "label": phase_key}
    else:
        state.PHASES[phase_key]["ready"] = True
    with state._build_progress_lock:
        state._build_progress["phase_seq"] = max(
            state._build_progress.get("phase_seq", 0), int(phase_seq)
        )
        state._build_progress["phases"] = {
            k: v["ready"] for k, v in state.PHASES.items()
        }


def apply_done(epoch: int, status: str) -> None:
    """SERVER-side terminal progress for a build epoch.

    status ∈ {"ok","error","killed"}. ``ok`` flips full_ready so the client
    stops polling; ``error``/``killed`` set the phase so the UI shows the
    build ended rather than spinning forever. Dropped if epoch is stale."""
    if epoch != state._SERVER_EPOCH:
        return  # stale build — drop
    with state._build_progress_lock:
        if status == "ok":
            state._build_progress.update(
                {"phase": "full_ready", "pct": 1.0, "full_ready": True}
            )
        else:
            state._build_progress.update(
                {"phase": status, "message": f"build {status}", "full_ready": False}
            )


def apply_graph_replace(epoch: int, data: dict) -> None:
    """Install the authoritative full-record graph from the finished child.

    INV-NODE: the cache is always full dicts, so node ids read with
    ``n.get("id")`` unconditionally (no slim-list branch).

    Atomic publish: swap the _graph_cache reference under _apply_lock so a
    concurrent /api/graph read never observes the cache mid-replacement.
    """
    if epoch != state._SERVER_EPOCH:
        return  # stale build — drop
    with state._apply_lock:
        state._graph_cache = {"data": data, "domain_filter": None}
        state._graph_cache_ts = time.monotonic()
        state._applied_node_ids.clear()
        state._node_index.clear()
        state._adjacency.clear()
        for n in data.get("nodes", []):
            nid = n.get("id")
            if nid:
                state._applied_node_ids.add(nid)
                state._node_index[nid] = n
        for e in data.get("edges", []):
            s, t, ek = e.get("source"), e.get("target"), e.get("kind")
            if s and t:
                state._adjacency.setdefault(s, []).append((t, ek, "out"))
                state._adjacency.setdefault(t, []).append((s, ek, "in"))


def get_build_progress() -> dict:
    with state._build_progress_lock:
        snap = dict(state._build_progress)
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
    return state._node_index.get(node_id)


def get_node_neighbors(node_id: str, offset: int = 0, limit: int = 500) -> dict:
    """One node's neighborhood, served on demand.

    Returns ``{"neighbors": [[other_id, other_kind, other_label,
    edge_kind, direction], ...], "total", "offset", "next_offset"}``.
    Bounded per page, complete across continuation (next_offset is
    None once drained) — high-degree hubs (a domain node carries tens
    of thousands of in_domain edges) page instead of truncating.
    Default page mirrors the MCP tool's 500-row default.
    """
    rows = state._adjacency.get(node_id, [])
    total = len(rows)
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    page = rows[offset : offset + limit]
    out = []
    for other_id, ekind, direction in page:
        other = state._node_index.get(other_id) or {}
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


def get_graph_slice(offset: int = 0, limit: int = 20000) -> dict:
    """Paginated FULL-fidelity page of the cumulative graph cache.

    The complete-across-continuation contract (no silent truncation —
    user direction 2026-06-12): each page slices BOTH nodes and edges
    by ``[offset : offset+limit]`` and reports totals; ``done`` flips
    once the window covers ``max(node_total, edge_total)``. The union
    of all pages equals the full cache. ``phase_seq`` keys consumer-
    side memoisation (a changed seq means the build published more).
    """
    cache = state._graph_cache
    data = cache.get("data", {}) if cache else {}
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    node_total = len(nodes)
    edge_total = len(edges)
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    with state._build_progress_lock:
        phase_seq = state._build_progress.get("phase_seq", 0)
        full_ready = bool(state._build_progress.get("full_ready"))
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


# Kind-membership of each FIXED baseline phase. The baseline rides the bulk
# graph FILE out-of-band (phase_key=None, NOT forwarded over the queue), so the
# server's _phase_payloads[L0..L5] are never populated by apply_delta. So
# /api/graph/phase for L0-L5 DERIVES the phase node set from _graph_cache by
# kind. Safe because INV-NODE (Liskov node-shape contract) guarantees the cache
# is always full dicts — the slim-list AttributeError that motivated removing
# this fallback can no longer occur. L6:* phases are NOT here: they stream as
# deltas with a real phase_key and apply_delta rebuilds their buffer (the
# `if not nodes` guard below skips L6 once populated).
# source: phase taxonomy (PHASES) + Lamport INV-PHASE decision, 2026-06-14.
_PHASE_KINDS: dict[str, set[str]] = {
    "L0": {"domain"},
    # L1 = structural setup layer (~190 nodes: skills, hooks, agents, MCPs).
    # "command" is Bash-execution telemetry — NOT setup; it belongs in L2
    # alongside tool_hubs via command_in_hub edges.
    "L1": {"skill", "hook", "agent", "mcp"},
    "L2": {"tool_hub", "command"},
    "L3": {"file"},
    "L4": {"discussion"},
    "L5": {"memory"},
}


def get_phase_payload(key: str, offset: int = 0, limit: int | None = None) -> dict:
    spec = state.PHASES.get(key)
    pl = state._phase_payloads.get(key, {"nodes": [], "edges": []})
    nodes = pl.get("nodes", [])
    edges = pl.get("edges", [])

    # Baseline phases (L0-L5) ride the bulk graph FILE out-of-band: their delta
    # is NOT forwarded (phase_key=None, stage="baseline"), so _phase_payloads
    # [L0..L5] is never populated. DERIVE the phase node set from the cumulative
    # cache by kind. SAFE: INV-NODE guarantees _graph_cache nodes are always
    # full dicts. L6:* keep their streamed buffer (the `if not nodes` guard
    # skips them). EPOCH-SAFE: begin_epoch empties the cache under _apply_lock,
    # so a stale epoch's slice is computed over the empty new-epoch cache.
    if not nodes and key in _PHASE_KINDS:
        cache = state._graph_cache  # single ref read — swaps are atomic
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
                # Edge scoping is AND (both endpoints inside the phase), NOT OR.
                # OR re-includes every in_domain edge (each node points to its
                # hub) -> L0 returns 20 nodes + 484k edges. Lossless: under the
                # client append model a cross-phase parent edge is owned by the
                # CHILD phase and appended atop the already-loaded parent; the
                # graph.js dedup sets make repeats a no-op.
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
