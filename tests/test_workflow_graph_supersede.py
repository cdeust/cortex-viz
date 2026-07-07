"""Unit tests for ``core.workflow_graph_supersede.ingest_supersede``.

Mirrors ``test_workflow_graph_association.py``'s style for
``ingest_association``: a directed memory→memory ``supersedes`` edge is
created only when both endpoints already exist in the target's
``_nodes``; orphan/malformed rows are silently skipped.
"""

from __future__ import annotations

from cortex_viz.core.workflow_graph_schema import EdgeKind, NodeIdFactory
from cortex_viz.core.workflow_graph_supersede import ingest_supersede


class _FakeTarget:
    """Minimal duck-typed stand-in for a builder — exactly the surface
    ``ingest_supersede`` needs (``_nodes`` membership + ``_edges``
    append), matching the adapter the streaming handler uses for the
    post-purge retention path."""

    def __init__(self, node_ids):
        self._nodes = set(node_ids)
        self._edges: list = []


def test_creates_one_directed_supersedes_edge_when_both_endpoints_present():
    target = _FakeTarget({NodeIdFactory.memory_id(42), NodeIdFactory.memory_id(7)})
    ingest_supersede(target, {"source_memory_id": 42, "target_memory_id": 7})
    assert len(target._edges) == 1
    edge = target._edges[0]
    # Directional: source = the newer memory, target = the superseded
    # fact — the direction IS the semantics, unlike associates_with.
    assert edge.source == NodeIdFactory.memory_id(42)
    assert edge.target == NodeIdFactory.memory_id(7)
    assert edge.kind == EdgeKind.SUPERSEDES.value
    assert edge.weight == 1.0
    assert edge.label == "supersedes"
    assert edge.reason == "supersede"


def test_missing_source_endpoint_is_dropped_silently():
    target = _FakeTarget({NodeIdFactory.memory_id(7)})
    ingest_supersede(target, {"source_memory_id": 42, "target_memory_id": 7})
    assert target._edges == []


def test_missing_target_endpoint_is_dropped_silently():
    """A stale superseded memory never enters the graph — its lineage
    edge is skipped, not errored."""
    target = _FakeTarget({NodeIdFactory.memory_id(42)})
    ingest_supersede(target, {"source_memory_id": 42, "target_memory_id": 7})
    assert target._edges == []


def test_none_ids_are_skipped():
    target = _FakeTarget({NodeIdFactory.memory_id(42), NodeIdFactory.memory_id(7)})
    ingest_supersede(target, {"source_memory_id": None, "target_memory_id": 7})
    ingest_supersede(target, {"source_memory_id": 42, "target_memory_id": None})
    assert target._edges == []


def test_chain_yields_one_edge_per_link():
    """A supersession chain (43 supersedes 42 supersedes 7) projects to
    one directed edge per recorded link."""
    target = _FakeTarget(
        {
            NodeIdFactory.memory_id(7),
            NodeIdFactory.memory_id(42),
            NodeIdFactory.memory_id(43),
        }
    )
    ingest_supersede(target, {"source_memory_id": 42, "target_memory_id": 7})
    ingest_supersede(target, {"source_memory_id": 43, "target_memory_id": 42})
    assert len(target._edges) == 2
    assert [(e.source, e.target) for e in target._edges] == [
        (NodeIdFactory.memory_id(42), NodeIdFactory.memory_id(7)),
        (NodeIdFactory.memory_id(43), NodeIdFactory.memory_id(42)),
    ]
