"""Memory -> memory supersession-edge ingestion.

Holds the single helper that projects a recorded supersession row into
one ``SUPERSEDES`` workflow-graph edge:

  * ``ingest_supersede(b, row)`` — creates one MEMORY -> MEMORY
    ``SUPERSEDES`` edge per row of
    ``infrastructure.memory_supersede.load_supersede_edges``. Silently
    skips rows whose endpoints are not in the graph (memories below
    ``min_heat``, stale superseded targets, etc.) — matches the
    "skip-missing-endpoint" contract of ``ingest_association``.

Deliberately its own module rather than folded into
``workflow_graph_association``: an association is an undirected
co-evidence edge with a channel-normalized weight; a supersession is a
DIRECTED versioning pointer with constant provenance weight. Different
edge shape, different semantics, different producer — folding them
together would violate SRP (two independently-changing reasons to edit
one file). Kept under CLAUDE.md's 300-line ceiling trivially.

Pure core logic — no I/O. ``ingest_supersede`` only reads ``b._nodes``
(membership check) and appends to ``b._edges``; any object exposing
those two attributes satisfies the contract, including the streaming
handler's post-purge retained-node adapter (see
``handlers.workflow_graph_streaming``).
"""

from __future__ import annotations

from cortex_viz.core.workflow_graph_schema import (
    EdgeKind,
    NodeIdFactory,
    WorkflowEdge,
)

# Constant provenance weight, same convention as ABOUT_ENTITY edges:
# a supersession either exists or it does not — there is no evidence
# strength to grade, unlike association channels.
_SUPERSEDE_WEIGHT = 1.0


def ingest_supersede(b, row: dict) -> None:
    """Create one MEMORY -> MEMORY ``SUPERSEDES`` edge (directional).

    Args:
        b: ``WorkflowGraphBuilder`` instance, or any duck-typed adapter
           exposing ``_nodes`` (membership-checkable) and ``_edges``
           (a list to append to).
        row: ``{"source_memory_id": int, "target_memory_id": int}`` —
           one row from ``infrastructure.memory_supersede.
           load_supersede_edges``; source = the newer memory, target =
           the older fact it replaces.

    Precondition: ``row["source_memory_id"] != row["target_memory_id"]``
    (Cortex's supersede write path validates the target before posting
    the edge, so self-loops cannot occur; not re-validated here).
    Postcondition: exactly one ``WorkflowEdge`` is appended to
    ``b._edges`` iff both endpoints are present in ``b._nodes``;
    otherwise ``b`` is left unmodified.
    """
    src_pg = row.get("source_memory_id")
    tgt_pg = row.get("target_memory_id")
    if src_pg is None or tgt_pg is None:
        return
    src_id = NodeIdFactory.memory_id(src_pg)
    tgt_id = NodeIdFactory.memory_id(tgt_pg)
    if src_id not in b._nodes or tgt_id not in b._nodes:
        return
    b._edges.append(
        WorkflowEdge(
            source=src_id,
            target=tgt_id,
            kind=EdgeKind.SUPERSEDES,
            weight=_SUPERSEDE_WEIGHT,
            label="supersedes",
            reason="supersede",
        )
    )


__all__ = ["ingest_supersede"]
