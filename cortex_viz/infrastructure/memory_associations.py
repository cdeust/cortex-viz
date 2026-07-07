"""Read-only co-entity memory<->memory association loader (v1 "brain
associations").

For every pair of memories that share at least one non-stop-word
entity, computes a TF-IDF-style co-occurrence weight and sparsifies to
a top-k kNN graph per memory. All work happens in a single read-only
SQL statement executed via ``MemoryReader.query(..., batch=True)`` —
no row-by-row Python aggregation, so the O(pairs) fan-out from a wide
entity's memory list stays inside PostgreSQL where it can use indexes
and hash aggregation instead of materializing every pair in the
Python heap.

Model (fixed — do not change without a new ADR):

  * weight(a, b) = SUM over shared entities e of idf(e), where
    idf(e) = ln(N / df(e)); N = total non-stale memories; df(e) =
    count of DISTINCT memory_id linked to entity e.
    source: Salton, G. & Buckley, C. (1988), "Term-weighting approaches
    in automatic text retrieval", Information Processing & Management
    24(5), 513-523.
  * Stop-word ceiling: entities with df(e) > _DF_CEILING_FRAC * N are
    excluded from the weight sum entirely (too common to discriminate
    between memories — the IR stop-word problem).
    source: Manning, C.D., Raghavan, P. & Schuetze, H. (2008),
    "Introduction to Information Retrieval", Cambridge University
    Press, section 2.2.2.
  * Sparsify: keep each memory's top-k highest-weight associations
    (a kNN graph), then symmetrize by taking the union of both
    directions so a pair survives if either endpoint ranks the other
    in its own top-k.
    source: von Luxburg, U. (2007), "A tutorial on spectral
    clustering", Statistics and Computing 17(4), 395-416, section 2.

No I/O beyond the single ``pg_store.query`` SELECT — this module never
INSERTs, UPDATEs, or DELETEs; cortex-viz is a read-only bridge over
Cortex's shared Postgres store.
"""

from __future__ import annotations

import os
from typing import Any

# source: von Luxburg (2007) sec. 2 kNN-graph construction — k is a
# tunable sparsification parameter, not a paper-derived constant;
# CORTEX_VIZ_ASSOC_TOPK lets an operator retune it without a code
# change. Default chosen as a starting point for the empirical
# degree-distribution benchmark (see task report) — 8 is not itself
# sourced, it is the value under test.
DEFAULT_ASSOC_TOP_K = 8

# source: Manning, Raghavan & Schuetze (2008) sec. 2.2.2 stop-word
# ceiling — entities present in more than this fraction of the corpus
# behave like IR stop words (they discriminate nothing) and are
# excluded from every pair's weight sum.
DF_CEILING_FRAC = 0.10

_ASSOCIATION_SQL = """
WITH live_links AS (
    SELECT me.memory_id, me.entity_id
    FROM memory_entities me
    JOIN memories m ON m.id = me.memory_id
    WHERE NOT m.is_stale
),
corpus_n AS (
    SELECT COUNT(*)::float AS total FROM memories WHERE NOT is_stale
),
entity_df AS (
    SELECT entity_id, COUNT(DISTINCT memory_id) AS df
    FROM live_links
    GROUP BY entity_id
),
eligible_entities AS (
    -- Stop-word ceiling: df(e) > DF_CEILING_FRAC * N is excluded.
    SELECT entity_df.entity_id, ln(corpus_n.total / entity_df.df) AS idf
    FROM entity_df, corpus_n
    WHERE entity_df.df > 0
      AND entity_df.df <= %s * corpus_n.total
      AND corpus_n.total > 0
),
pairs AS (
    -- Undirected pairs (a.memory_id < b.memory_id): one row per pair,
    -- weight = SUM(idf) over shared eligible entities.
    SELECT
        a.memory_id AS m1,
        b.memory_id AS m2,
        SUM(eligible_entities.idf) AS weight,
        COUNT(*) AS shared_count
    FROM live_links a
    JOIN live_links b
        ON a.entity_id = b.entity_id AND a.memory_id < b.memory_id
    JOIN eligible_entities ON eligible_entities.entity_id = a.entity_id
    GROUP BY a.memory_id, b.memory_id
),
directed AS (
    -- Mirror each undirected pair into both directions so top-k can be
    -- ranked per memory regardless of which side of the pair it fell on.
    SELECT m1 AS memory_id, m2 AS neighbor_id, weight, shared_count FROM pairs
    UNION ALL
    SELECT m2 AS memory_id, m1 AS neighbor_id, weight, shared_count FROM pairs
),
ranked AS (
    SELECT
        memory_id, neighbor_id, weight, shared_count,
        ROW_NUMBER() OVER (
            PARTITION BY memory_id ORDER BY weight DESC, neighbor_id
        ) AS rn
    FROM directed
)
-- Symmetrize: a pair survives if it was in the top-k of EITHER
-- endpoint (rn <= top_k on either directed row), deduped back to one
-- undirected row per pair via LEAST/GREATEST + GROUP BY.
SELECT
    LEAST(memory_id, neighbor_id) AS source_memory_id,
    GREATEST(memory_id, neighbor_id) AS target_memory_id,
    MAX(weight) AS weight,
    MAX(shared_count)::int AS shared_count
FROM ranked
WHERE rn <= %s
GROUP BY LEAST(memory_id, neighbor_id), GREATEST(memory_id, neighbor_id)
ORDER BY source_memory_id, target_memory_id
"""


def _resolve_top_k(top_k: int | None) -> int:
    if top_k is not None:
        return int(top_k)
    try:
        return int(os.environ.get("CORTEX_VIZ_ASSOC_TOPK", DEFAULT_ASSOC_TOP_K))
    except (TypeError, ValueError):
        return DEFAULT_ASSOC_TOP_K


def load_co_entity_associations(
    pg_store,
    top_k: int | None = None,
    df_ceiling_frac: float = DF_CEILING_FRAC,
) -> list[dict[str, Any]]:
    """Return sparsified co-entity memory<->memory associations.

    Args:
        pg_store: read-only store exposing ``.query(sql, params, *,
            batch=True)`` (``MemoryReader`` — see infrastructure.
            memory_read). Read-only: this function only SELECTs.
        top_k: max associations retained per memory before
            symmetrizing (see module docstring). ``None`` resolves
            from ``CORTEX_VIZ_ASSOC_TOPK`` (default
            ``DEFAULT_ASSOC_TOP_K``).
        df_ceiling_frac: stop-word ceiling as a fraction of the total
            non-stale corpus size N (default ``DF_CEILING_FRAC``).

    Returns:
        ``[{"source_memory_id": int, "target_memory_id": int,
        "weight": float, "shared_count": int}, ...]`` — one row per
        undirected pair, ``source_memory_id < target_memory_id``.
        Empty list when the corpus has zero non-stale memories (N=0
        guard — the SQL's ``corpus_n.total > 0`` predicate already
        prevents a division-by-zero inside ``ln()``, this is belt and
        suspenders against ``pg_store`` implementations that might
        short-circuit before running the query).
    """
    resolved_k = _resolve_top_k(top_k)
    if resolved_k <= 0:
        return []
    rows = pg_store.query(
        _ASSOCIATION_SQL,
        (df_ceiling_frac, resolved_k),
        batch=True,
    )
    return [
        {
            "source_memory_id": r["source_memory_id"],
            "target_memory_id": r["target_memory_id"],
            "weight": float(r["weight"]),
            "shared_count": int(r["shared_count"]),
        }
        for r in rows
    ]


__all__ = ["load_co_entity_associations", "DEFAULT_ASSOC_TOP_K", "DF_CEILING_FRAC"]
