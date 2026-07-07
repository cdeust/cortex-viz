"""Memory <-> memory association-edge ingestion (v1 "brain associations").

Holds the single helper that projects a co-entity association row into
one ``ASSOCIATES_WITH`` workflow-graph edge:

  * ``ingest_association(b, assoc)`` — creates one MEMORY -> MEMORY
    ``ASSOCIATES_WITH`` edge per row of
    ``infrastructure.memory_associations.load_co_entity_associations``.
    Silently skips associations whose endpoints are not in the graph
    (memories below ``min_heat``, filtered by domain, etc.) — matches
    the "skip-missing-endpoint" contract of ``ingest_about_entity``.

Deliberately its own module rather than folded into
``workflow_graph_entity`` (which owns MEMORY -> ENTITY edges): the two
are different concerns — different edge shape (memory<->memory vs
memory->entity), different weight semantics (TF-IDF sum vs a constant
1.0 provenance edge), and different producers. Folding them together
would violate SRP (two independently-changing reasons to edit one
file). Kept under CLAUDE.md's 300-line ceiling trivially.

Pure core logic — no I/O. ``ingest_association`` only reads ``b._nodes``
(membership check) and appends to ``b._edges``; it does not require a
full ``WorkflowGraphBuilder`` — any object exposing those two
attributes satisfies the contract. The streaming handler relies on
this looseness to run a final association pass over a lightweight
node-id-set adapter after memory nodes have already been purged from
the real builder (see ``handlers.workflow_graph_streaming``).
"""

from __future__ import annotations

from cortex_viz.core.workflow_graph_schema import (
    EdgeKind,
    NodeIdFactory,
    WorkflowEdge,
)


def ingest_association(b, assoc: dict) -> None:
    """Create one MEMORY -> MEMORY ``ASSOCIATES_WITH`` edge.

    Args:
        b: ``WorkflowGraphBuilder`` instance, or any duck-typed adapter
           exposing ``_nodes`` (membership-checkable) and ``_edges``
           (a list to append to).
        assoc: ``{"source_memory_id": int, "target_memory_id": int,
           "weight": float, "shared_count": int}`` — one row from
           ``infrastructure.memory_associations.load_co_entity_associations``.

    Precondition: ``assoc["source_memory_id"] != assoc["target_memory_id"]``
    (the source query only ever emits ``a.memory_id < b.memory_id``
    pairs, so self-loops cannot occur; not re-validated here).
    Postcondition: exactly one ``WorkflowEdge`` is appended to
    ``b._edges`` iff both endpoints are present in ``b._nodes``;
    otherwise ``b`` is left unmodified.
    """
    src_pg = assoc.get("source_memory_id")
    tgt_pg = assoc.get("target_memory_id")
    if src_pg is None or tgt_pg is None:
        return
    src_id = NodeIdFactory.memory_id(src_pg)
    tgt_id = NodeIdFactory.memory_id(tgt_pg)
    if src_id not in b._nodes or tgt_id not in b._nodes:
        return
    weight = float(assoc.get("weight") or 0.0)
    shared_count = int(assoc.get("shared_count") or 0)
    # The unified substrate (infrastructure.memory_associations.
    # load_memory_associations) tags each row with its evidence channel
    # ("co-entity", "semantic", "temporal", or a "+"-join of those);
    # rows from the bare v1 loader carry no tag and default to
    # "co-entity".
    reason = str(assoc.get("reason") or "co-entity")
    # "N shared" only means something on the co-entity channel; a pair
    # without co-entity evidence shares no entity, so its label is the
    # channel tag.
    label = f"{shared_count} shared" if shared_count > 0 else reason
    b._edges.append(
        WorkflowEdge(
            source=src_id,
            target=tgt_id,
            kind=EdgeKind.ASSOCIATES_WITH,
            weight=weight,
            label=label,
            reason=reason,
        )
    )


__all__ = ["ingest_association"]
