"""Read-only memory supersession-edge loader.

One SELECT over the recorded ``memories.supersedes_id`` column — the
lineage is WRITTEN by Cortex's supersede write path (``remember(
supersedes_id)`` / ``supersede_atomic``, merged on Cortex main, PR #82);
cortex-viz only reads what was recorded and never re-derives or
mutates it. Uses the partial index ``idx_memories_supersedes``
(``WHERE supersedes_id IS NOT NULL``) so the scan touches only the
chain rows.

Edges are DIRECTIONAL: ``source_memory_id`` is the newer memory,
``target_memory_id`` the older fact it replaces ("source supersedes
target"). This is deliberately NOT an association channel
(``memory_associations``): associations are undirected co-evidence
merged into one substrate that drives layout and community detection,
while a supersession is a directed replacement pointer — feeding it
into the association substrate would let versioning lineage warp the
semantic communities. It becomes its own ``EdgeKind.SUPERSEDES`` edge
instead (see ``core.workflow_graph_supersede``).

Only the NEWER side is filtered on ``is_stale``: a live memory's
lineage pointer is worth drawing even if the superseded fact has since
been marked stale — the ingestion's skip-missing-endpoint contract
already drops edges whose old endpoint never made it into the graph.

No I/O beyond the single ``pg_store.query`` SELECT — this module never
INSERTs, UPDATEs, or DELETEs; cortex-viz is a read-only bridge over
Cortex's shared Postgres store.
"""

from __future__ import annotations

from typing import Any

_SUPERSEDE_SQL = """
SELECT
    m.id AS source_memory_id,
    m.supersedes_id AS target_memory_id
FROM memories m
WHERE m.supersedes_id IS NOT NULL
  AND NOT m.is_stale
ORDER BY source_memory_id, target_memory_id
"""


def load_supersede_edges(pg_store) -> list[dict[str, Any]]:
    """Return every recorded supersession as one directed edge row.

    Args:
        pg_store: read-only store exposing ``.query(sql, params, *,
            batch=True)`` (``MemoryReader`` — see infrastructure.
            memory_read). Read-only: this function only SELECTs.

    Returns:
        ``[{"source_memory_id": int, "target_memory_id": int}, ...]``
        — one row per live memory that supersedes an older one,
        ``source_memory_id`` = the newer memory, ``target_memory_id``
        = the fact it replaces. Self-loops cannot occur (the write
        path validates the target exists and is a different row before
        posting the edge). Empty list when no supersession chain
        exists.
    """
    rows = pg_store.query(_SUPERSEDE_SQL, (), batch=True)
    return [
        {
            "source_memory_id": r["source_memory_id"],
            "target_memory_id": r["target_memory_id"],
        }
        for r in rows
    ]


__all__ = ["load_supersede_edges"]
