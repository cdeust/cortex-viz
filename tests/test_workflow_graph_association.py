"""Unit tests for ``core.workflow_graph_association.ingest_association``.

Mirrors ``test_workflow_graph_entity_wiring.py``'s style for
``ingest_about_entity``: a memory<->memory ``associates_with`` edge is
created only when both endpoints already exist in the target's
``_nodes``; orphan/self-referential/malformed rows are silently
skipped.
"""

from __future__ import annotations

from cortex_viz.core.workflow_graph_association import ingest_association
from cortex_viz.core.workflow_graph_schema import EdgeKind, NodeIdFactory


class _FakeTarget:
    """Minimal duck-typed stand-in for a builder — exactly the surface
    ``ingest_association`` needs (``_nodes`` membership + ``_edges``
    append), matching the adapter the streaming handler uses for the
    post-purge retention path."""

    def __init__(self, node_ids):
        self._nodes = set(node_ids)
        self._edges: list = []


def test_creates_one_associates_with_edge_when_both_endpoints_present():
    target = _FakeTarget({NodeIdFactory.memory_id(1), NodeIdFactory.memory_id(2)})
    ingest_association(
        target,
        {
            "source_memory_id": 1,
            "target_memory_id": 2,
            "weight": 3.25,
            "shared_count": 2,
        },
    )
    assert len(target._edges) == 1
    edge = target._edges[0]
    assert edge.source == NodeIdFactory.memory_id(1)
    assert edge.target == NodeIdFactory.memory_id(2)
    assert edge.kind == EdgeKind.ASSOCIATES_WITH.value
    assert edge.weight == 3.25
    assert edge.label == "2 shared"
    assert edge.reason == "co-entity"


def test_missing_source_endpoint_is_dropped_silently():
    target = _FakeTarget({NodeIdFactory.memory_id(2)})
    ingest_association(
        target,
        {"source_memory_id": 1, "target_memory_id": 2, "weight": 1.0,
         "shared_count": 1},
    )
    assert target._edges == []


def test_missing_target_endpoint_is_dropped_silently():
    target = _FakeTarget({NodeIdFactory.memory_id(1)})
    ingest_association(
        target,
        {"source_memory_id": 1, "target_memory_id": 2, "weight": 1.0,
         "shared_count": 1},
    )
    assert target._edges == []


def test_none_ids_are_skipped():
    target = _FakeTarget({NodeIdFactory.memory_id(1), NodeIdFactory.memory_id(2)})
    ingest_association(
        target, {"source_memory_id": None, "target_memory_id": 2,
                  "weight": 1.0, "shared_count": 1}
    )
    ingest_association(
        target, {"source_memory_id": 1, "target_memory_id": None,
                  "weight": 1.0, "shared_count": 1}
    )
    assert target._edges == []


def test_missing_weight_and_shared_count_default_to_zero():
    target = _FakeTarget({NodeIdFactory.memory_id(1), NodeIdFactory.memory_id(2)})
    ingest_association(target, {"source_memory_id": 1, "target_memory_id": 2})
    assert len(target._edges) == 1
    assert target._edges[0].weight == 0.0
    assert target._edges[0].label == "0 shared"
