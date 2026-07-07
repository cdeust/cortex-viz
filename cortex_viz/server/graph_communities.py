"""Attach associative community ids to a built graph (composition root).

Glue between the finished graph dict (nodes + edges, produced by
``build_workflow_graph``) and the pure Leiden+CPM detector in
``core.community_detection``. Runs once per build, server-side, so the
browser no longer computes communities at all — it just reads the
``community_id`` field this module writes onto each memory node dict.

The detection substrate is the SPARSE co-entity channel only: edges
whose evidence ``reason`` includes ``"co-entity"``. The dense temporal
and semantic channels are excluded from DETECTION (they smear the
partition into one blob — the collapse this fix addresses) but stay in
the graph so the brain view still RENDERS all three additive channels.
That detection/rendering split is the whole point.

Mutates the passed-in node dicts in place (adds ``community_id``); the
mutated dicts flow through the cumulative cache and the durable snapshot
to ``/api/graph/full``. No I/O of its own.
"""

from __future__ import annotations

from typing import Any

from cortex_viz.core.community_detection import detect_communities

_MEMORY_KIND = "memory"
_ASSOC_KIND = "associates_with"
_CO_ENTITY = "co-entity"


def _node_kind(node: dict) -> str:
    return node.get("kind") or node.get("type") or ""


def _endpoint_id(value: Any) -> Any:
    """An edge endpoint is normally a node-id string at build time, but
    tolerate the ``{"id": ...}`` object form some client-facing paths
    use."""
    if isinstance(value, dict):
        return value.get("id")
    return value


def attach_communities(graph: dict, *, resolution: float | None = None) -> dict:
    """Detect associative communities and stamp ``community_id`` on each
    memory node dict of ``graph`` in place.

    Args:
        graph: ``{"nodes": [...], "edges"|"links": [...], ...}`` — the
            finished build dict. Node/edge dicts are the
            ``_node_to_dict``/``_edge_to_dict`` projections.
        resolution: CPM resolution gamma forwarded to the detector
            (``None`` → env/default, see ``core.community_detection``).

    Returns:
        A small stats dict ``{"attached", "communities", "largest",
        "status"}`` for the build log — ``status`` is ``"degraded"``
        when the detector returned nothing (igraph/leidenalg absent),
        in which case no ``community_id`` is written and the client
        falls back to per-kind colouring.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or graph.get("links") or []

    memory_ids = [
        n["id"] for n in nodes if _node_kind(n) == _MEMORY_KIND and n.get("id")
    ]
    if not memory_ids:
        return {"attached": 0, "communities": 0, "largest": 0, "status": "no-memory"}
    memory_id_set = set(memory_ids)

    detection_edges: list[tuple[str, str, float]] = []
    for e in edges:
        if (e.get("kind") or e.get("type")) != _ASSOC_KIND:
            continue
        # Bare v1 rows default to co-entity; combined rows carry an
        # explicit reason ("co-entity", "semantic", "temporal", or a
        # "+"-join). Detect only where co-entity evidence is present.
        if _CO_ENTITY not in (e.get("reason") or _CO_ENTITY):
            continue
        src = _endpoint_id(e.get("source"))
        tgt = _endpoint_id(e.get("target"))
        if src in memory_id_set and tgt in memory_id_set:
            detection_edges.append((src, tgt, float(e.get("weight") or 0.0)))

    mapping = detect_communities(detection_edges, memory_ids, resolution=resolution)
    if not mapping:
        return {
            "attached": 0,
            "communities": 0,
            "largest": 0,
            "status": "degraded",
        }

    sizes: dict[int, int] = {}
    attached = 0
    for n in nodes:
        if _node_kind(n) != _MEMORY_KIND:
            continue
        cid = mapping.get(n.get("id"))
        if cid is None:
            continue
        n["community_id"] = cid
        sizes[cid] = sizes.get(cid, 0) + 1
        attached += 1

    return {
        "attached": attached,
        "communities": len(sizes),
        "largest": max(sizes.values(), default=0),
        "status": "ok",
    }


__all__ = ["attach_communities"]
