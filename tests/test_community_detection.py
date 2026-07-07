"""Tests for server-side associative community detection.

Covers the pure Leiden+CPM detector (``core.community_detection``) and
the graph-glue that stamps ``community_id`` onto memory node dicts
(``server.graph_communities``).
"""

from __future__ import annotations

import pytest

from cortex_viz.core.community_detection import (
    DEFAULT_RESOLUTION,
    SEED,
    detect_communities,
)
from cortex_viz.server.graph_communities import attach_communities

leidenalg = pytest.importorskip("leidenalg")

# Two dense triangles joined by a single weak bridge, plus an isolated
# vertex. CPM at a resolution above the bridge weight must keep the two
# triangles separate and leave the isolated vertex a singleton.
_TWO_TRIANGLES = [
    ("A", "B", 1.0),
    ("B", "C", 1.0),
    ("A", "C", 1.0),
    ("D", "E", 1.0),
    ("E", "F", 1.0),
    ("D", "F", 1.0),
    ("C", "D", 0.05),  # weak inter-cluster bridge
]
_ALL_NODES = ["A", "B", "C", "D", "E", "F", "Z"]  # Z is isolated


def test_detects_two_communities_and_isolated_singleton():
    mapping = detect_communities(_TWO_TRIANGLES, _ALL_NODES, resolution=0.1)
    # Two triangles → two distinct communities.
    assert mapping["A"] == mapping["B"] == mapping["C"]
    assert mapping["D"] == mapping["E"] == mapping["F"]
    assert mapping["A"] != mapping["D"]
    # Isolated node gets its own singleton, distinct from both triangles.
    assert mapping["Z"] not in {mapping["A"], mapping["D"]}


def test_community_ids_are_contiguous_from_zero():
    mapping = detect_communities(_TWO_TRIANGLES, _ALL_NODES, resolution=0.1)
    ids = sorted(set(mapping.values()))
    assert ids == list(range(len(ids)))


def test_deterministic_across_runs():
    a = detect_communities(_TWO_TRIANGLES, _ALL_NODES, resolution=0.1, seed=SEED)
    b = detect_communities(_TWO_TRIANGLES, _ALL_NODES, resolution=0.1, seed=SEED)
    assert a == b


def test_all_isolated_when_no_edges():
    mapping = detect_communities([], ["X", "Y", "Z"])
    # No edges → each node its own community.
    assert len(set(mapping.values())) == 3


def test_self_loops_and_missing_endpoints_dropped():
    edges = [("A", "A", 1.0), (None, "B", 1.0), ("A", "B", 1.0)]
    mapping = detect_communities(edges, ["A", "B"], resolution=0.1)
    # A and B still get communities; the self-loop / None edge don't crash.
    assert set(mapping) == {"A", "B"}


def test_only_edge_vertices_returned_when_node_ids_none():
    mapping = detect_communities(_TWO_TRIANGLES, None, resolution=0.1)
    assert set(mapping) == {"A", "B", "C", "D", "E", "F"}
    assert "Z" not in mapping


def test_default_resolution_is_a_float():
    assert isinstance(DEFAULT_RESOLUTION, float)


# ── graph glue: attach_communities ──────────────────────────────────


def _graph_with(nodes, edges):
    return {"nodes": nodes, "edges": edges, "meta": {}}


def test_attach_stamps_community_id_on_memory_nodes():
    nodes = [
        {"id": "memory:1", "kind": "memory"},
        {"id": "memory:2", "kind": "memory"},
        {"id": "memory:3", "kind": "memory"},
        {"id": "domain:cortex", "kind": "domain"},
    ]
    edges = [
        {
            "source": "memory:1",
            "target": "memory:2",
            "kind": "associates_with",
            "reason": "co-entity",
            "weight": 1.0,
        },
    ]
    stats = attach_communities(_graph_with(nodes, edges), resolution=0.1)
    assert stats["status"] == "ok"
    # memory:1 and memory:2 share an edge → same community.
    assert nodes[0]["community_id"] == nodes[1]["community_id"]
    # memory:3 is isolated → its own community.
    assert nodes[2]["community_id"] not in (nodes[0]["community_id"],)
    # domain node never gets a community_id.
    assert "community_id" not in nodes[3]
    assert stats["attached"] == 3


def test_attach_excludes_temporal_only_edges_from_detection():
    """A pair evidenced ONLY by the temporal channel must not couple the
    two memories in the detection graph (that is the whole collapse-fix
    invariant)."""
    nodes = [
        {"id": "memory:1", "kind": "memory"},
        {"id": "memory:2", "kind": "memory"},
    ]
    edges = [
        {
            "source": "memory:1",
            "target": "memory:2",
            "kind": "associates_with",
            "reason": "temporal",  # temporal-only → excluded from detection
            "weight": 1.0,
        },
    ]
    attach_communities(_graph_with(nodes, edges), resolution=0.1)
    # No co-entity evidence → the two are NOT joined → distinct singletons.
    assert nodes[0]["community_id"] != nodes[1]["community_id"]


def test_attach_includes_combined_reason_with_co_entity():
    nodes = [
        {"id": "memory:1", "kind": "memory"},
        {"id": "memory:2", "kind": "memory"},
    ]
    edges = [
        {
            "source": "memory:1",
            "target": "memory:2",
            "kind": "associates_with",
            "reason": "co-entity+temporal",  # co-entity present → included
            "weight": 1.0,
        },
    ]
    attach_communities(_graph_with(nodes, edges), resolution=0.1)
    assert nodes[0]["community_id"] == nodes[1]["community_id"]


def test_attach_no_memory_nodes_is_noop():
    nodes = [{"id": "domain:cortex", "kind": "domain"}]
    stats = attach_communities(_graph_with(nodes, []))
    assert stats["status"] == "no-memory"
    assert stats["attached"] == 0
