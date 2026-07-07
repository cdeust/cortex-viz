"""Read-only memory<->memory association loaders (the "brain
associations" substrate).

Three independent association channels, each computed in a single
read-only SQL statement executed via ``MemoryReader.query(...,
batch=True)``, plus a pure combiner that merges them into ONE unified
``associates_with`` substrate (same downstream layout + community
detection, no per-channel special-casing):

  * v1 co-entity (``load_co_entity_associations``) — for every pair of
    memories that share at least one non-stop-word entity, a
    TF-IDF-style co-occurrence weight, sparsified to a top-k kNN graph
    per memory. All pair fan-out stays inside PostgreSQL where it can
    use indexes and hash aggregation instead of materializing every
    pair in the Python heap.
  * v2 semantic (``load_semantic_associations``) — per-memory top-k
    nearest neighbours in embedding space via the HNSW index
    (``idx_memories_embedding``, ``vector_cosine_ops``), weighted by
    cosine similarity and floored at a measured similarity threshold.
  * v3 temporal (``load_temporal_associations``) — pairs of memories
    accessed within the same time window, weighted by access
    proximity. Delegates the pair generation to Cortex's existing
    ``get_temporal_co_access()`` PG function (single source of truth
    for the proximity model — never re-derived here), then applies the
    SAME top-k kNN sparsification as the other channels: the raw
    output is burst cliques (every pair inside one working session),
    measured 138 051 pairs for only 1 000 seed memories on the live
    corpus (2026-07-07) — unusable as a substrate without
    sparsification.
  * ``load_memory_associations`` — runs all three and merges via
    ``combine_associations`` (union of the kNN graphs, per-pair
    max of max-normalized channel weights).

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
  * Semantic weight(a, b) = 1 - cosine_distance(emb_a, emb_b), i.e.
    cosine similarity, floored at ``min_sim`` — pairs below the floor
    carry no discriminating signal and are dropped before
    symmetrization (same kNN-graph construction as above, von Luxburg
    2007 sec. 2).
  * Temporal weight(a, b) = proximity as computed by
    ``get_temporal_co_access()`` (1 - dt/window, already in [0, 1]) —
    the PG function owns the model; this module only sparsifies its
    output to a top-k kNN graph and symmetrizes (von Luxburg 2007
    sec. 2, identical construction to the other channels).
  * Channel combination: union of the kNN graphs; each channel's
    weights are first normalized to [0, 1] by that channel's own max
    (they live on incommensurable scales — unbounded IDF sums vs.
    bounded cosine similarity vs. bounded access proximity), then a
    pair's combined weight is the MAX over channels. Union-of-graphs
    is the standard multi-view kNN-graph construction (von Luxburg
    2007 sec. 2); max-combination keeps a pair as strong as its
    strongest evidence channel rather than diluting a strong semantic
    link that happens to share no entity.

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

# source: measured on 2026-07-07 against the live cortex corpus —
# distribution of top-8 kNN cosine similarities: p25=0.628,
# median=0.679, p75=0.744. 0.6 ~= p25 keeps the strong-link mass and
# drops the weak tail. Overridable via CORTEX_VIZ_ASSOC_MIN_SIM for
# retuning on a different corpus without a code change.
DEFAULT_SEMANTIC_MIN_SIM = 0.6

# source: defaults of Cortex's get_temporal_co_access(p_window_hours
# real DEFAULT 2.0, p_min_access integer DEFAULT 1, ...) PG function —
# the function owns the temporal co-access model; we inherit its own
# documented defaults rather than inventing new ones. Overridable via
# CORTEX_VIZ_ASSOC_TEMPORAL_WINDOW_H / _TEMPORAL_MIN_ACCESS.
DEFAULT_TEMPORAL_WINDOW_HOURS = 2.0
DEFAULT_TEMPORAL_MIN_ACCESS = 1

# source: measured on 2026-07-07 against the live cortex corpus —
# 9 715 non-stale memories, all with last_accessed; a seed limit of
# 10 000 covers the whole corpus and the sparsified query (top-k=8 +
# symmetrization) returns 24 399 pairs in 1.5 s. The PG function's own
# default (100) is sized for interactive recall, not for building a
# full substrate. Overridable via CORTEX_VIZ_ASSOC_TEMPORAL_SEED_LIMIT.
DEFAULT_TEMPORAL_SEED_LIMIT = 10000

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


# Per-seed top-k semantic kNN, symmetrized to one row per undirected
# pair. The LATERAL subquery targets the base ``memories`` table
# directly (NOT a CTE): PostgreSQL can only drive the
# ``ORDER BY embedding <=> ... LIMIT k`` through the HNSW index
# (idx_memories_embedding, vector_cosine_ops) when scanning the
# indexed relation itself — wrapping it in a CTE loses the index and
# degrades to a sequential scan per seed.
_SEMANTIC_ASSOCIATION_SQL = """
SELECT
    LEAST(directed.seed_id, directed.neighbor_id) AS source_memory_id,
    GREATEST(directed.seed_id, directed.neighbor_id) AS target_memory_id,
    MAX(directed.similarity) AS weight
FROM (
    SELECT seed.id AS seed_id, knn.neighbor_id, knn.similarity
    FROM memories seed
    CROSS JOIN LATERAL (
        SELECT m2.id AS neighbor_id,
               1 - (m2.embedding <=> seed.embedding) AS similarity
        FROM memories m2
        WHERE m2.id <> seed.id
          AND NOT m2.is_stale
          AND m2.embedding IS NOT NULL
        ORDER BY m2.embedding <=> seed.embedding
        LIMIT %s
    ) knn
    WHERE NOT seed.is_stale
      AND seed.embedding IS NOT NULL
      AND knn.similarity >= %s
) directed
GROUP BY LEAST(directed.seed_id, directed.neighbor_id),
         GREATEST(directed.seed_id, directed.neighbor_id)
ORDER BY source_memory_id, target_memory_id
"""


# Pair generation is delegated to get_temporal_co_access() (Cortex's
# PG function — proximity = 1 - dt/window over the p_limit most
# recently accessed memories). Its raw output is burst cliques, so the
# same top-k kNN sparsification + symmetrization as the other channels
# is applied on top (von Luxburg 2007 sec. 2). The function already
# emits one row per undirected pair (mem_a < mem_b); the mirror into
# both directions exists only so top-k can be ranked per memory.
_TEMPORAL_ASSOCIATION_SQL = """
WITH pairs AS (
    -- Explicit casts: psycopg binds Python float/int as double
    -- precision/smallint, and PG's function resolution will not
    -- implicitly narrow those to the function's (real, integer,
    -- integer) signature.
    SELECT mem_a, mem_b, proximity
    FROM get_temporal_co_access(%s::real, %s::integer, %s::integer)
),
directed AS (
    SELECT mem_a AS memory_id, mem_b AS neighbor_id, proximity FROM pairs
    UNION ALL
    SELECT mem_b AS memory_id, mem_a AS neighbor_id, proximity FROM pairs
),
ranked AS (
    SELECT
        memory_id, neighbor_id, proximity,
        ROW_NUMBER() OVER (
            PARTITION BY memory_id ORDER BY proximity DESC, neighbor_id
        ) AS rn
    FROM directed
)
SELECT
    LEAST(memory_id, neighbor_id) AS source_memory_id,
    GREATEST(memory_id, neighbor_id) AS target_memory_id,
    MAX(proximity) AS weight
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


def _resolve_min_sim(min_sim: float | None) -> float:
    if min_sim is not None:
        return float(min_sim)
    try:
        return float(
            os.environ.get("CORTEX_VIZ_ASSOC_MIN_SIM", DEFAULT_SEMANTIC_MIN_SIM)
        )
    except (TypeError, ValueError):
        return DEFAULT_SEMANTIC_MIN_SIM


def _resolve_temporal_params() -> tuple[float, int, int]:
    """Resolve (window_hours, min_access, seed_limit) from the
    environment, falling back to the sourced defaults above. Env-only
    knobs (no function parameters): the three values travel together as
    the ``get_temporal_co_access`` call signature and are operator
    tuning, not per-call variation."""
    try:
        window = float(
            os.environ.get(
                "CORTEX_VIZ_ASSOC_TEMPORAL_WINDOW_H", DEFAULT_TEMPORAL_WINDOW_HOURS
            )
        )
    except (TypeError, ValueError):
        window = DEFAULT_TEMPORAL_WINDOW_HOURS
    try:
        min_access = int(
            os.environ.get(
                "CORTEX_VIZ_ASSOC_TEMPORAL_MIN_ACCESS", DEFAULT_TEMPORAL_MIN_ACCESS
            )
        )
    except (TypeError, ValueError):
        min_access = DEFAULT_TEMPORAL_MIN_ACCESS
    try:
        seed_limit = int(
            os.environ.get(
                "CORTEX_VIZ_ASSOC_TEMPORAL_SEED_LIMIT", DEFAULT_TEMPORAL_SEED_LIMIT
            )
        )
    except (TypeError, ValueError):
        seed_limit = DEFAULT_TEMPORAL_SEED_LIMIT
    return window, min_access, seed_limit


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


def load_semantic_associations(
    pg_store,
    top_k: int | None = None,
    min_sim: float | None = None,
) -> list[dict[str, Any]]:
    """Return sparsified semantic-kNN memory<->memory associations.

    Args:
        pg_store: read-only store exposing ``.query(sql, params, *,
            batch=True)`` — this function only SELECTs.
        top_k: nearest neighbours fetched per seed memory before
            symmetrizing. ``None`` resolves from
            ``CORTEX_VIZ_ASSOC_TOPK`` (default ``DEFAULT_ASSOC_TOP_K``)
            — deliberately the SAME knob as the co-entity channel so
            both kNN graphs stay equally sparse.
        min_sim: cosine-similarity floor; neighbours below it are
            dropped before symmetrization. ``None`` resolves from
            ``CORTEX_VIZ_ASSOC_MIN_SIM`` (default
            ``DEFAULT_SEMANTIC_MIN_SIM`` — see its source comment).

    Returns:
        ``[{"source_memory_id": int, "target_memory_id": int,
        "weight": float}, ...]`` — one row per undirected pair,
        ``source_memory_id < target_memory_id``, weight = cosine
        similarity in ``[min_sim, 1]``. Memories without an embedding
        contribute no rows (NULL embeddings are excluded on both sides
        of the kNN join).
    """
    resolved_k = _resolve_top_k(top_k)
    if resolved_k <= 0:
        return []
    resolved_min_sim = _resolve_min_sim(min_sim)
    rows = pg_store.query(
        _SEMANTIC_ASSOCIATION_SQL,
        (resolved_k, resolved_min_sim),
        batch=True,
    )
    return [
        {
            "source_memory_id": r["source_memory_id"],
            "target_memory_id": r["target_memory_id"],
            "weight": float(r["weight"]),
        }
        for r in rows
    ]


def load_temporal_associations(
    pg_store,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Return sparsified temporal co-access memory<->memory associations.

    Args:
        pg_store: read-only store exposing ``.query(sql, params, *,
            batch=True)`` — this function only SELECTs (the PG function
            it calls is STABLE).
        top_k: associations retained per memory before symmetrizing.
            ``None`` resolves from ``CORTEX_VIZ_ASSOC_TOPK`` (default
            ``DEFAULT_ASSOC_TOP_K``) — deliberately the SAME knob as
            the other channels so all kNN graphs stay equally sparse.

    The ``get_temporal_co_access`` call parameters (window hours,
    min access count, seed limit) are env-only knobs —
    ``CORTEX_VIZ_ASSOC_TEMPORAL_WINDOW_H`` / ``_TEMPORAL_MIN_ACCESS`` /
    ``_TEMPORAL_SEED_LIMIT`` — see ``_resolve_temporal_params``.

    Returns:
        ``[{"source_memory_id": int, "target_memory_id": int,
        "weight": float}, ...]`` — one row per undirected pair,
        ``source_memory_id < target_memory_id``, weight = access
        proximity in ``(0, 1]`` (1 - dt/window as computed by the PG
        function). Memories never accessed contribute no rows.
    """
    resolved_k = _resolve_top_k(top_k)
    if resolved_k <= 0:
        return []
    window, min_access, seed_limit = _resolve_temporal_params()
    rows = pg_store.query(
        _TEMPORAL_ASSOCIATION_SQL,
        (window, min_access, seed_limit, resolved_k),
        batch=True,
    )
    return [
        {
            "source_memory_id": r["source_memory_id"],
            "target_memory_id": r["target_memory_id"],
            "weight": float(r["weight"]),
        }
        for r in rows
    ]


def combine_associations(
    co_entity_rows: list[dict[str, Any]],
    semantic_rows: list[dict[str, Any]],
    temporal_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge the channels into one unified association substrate.

    Pure function — no I/O. Union of the kNN graphs; per-pair
    combined weight = MAX over channels of the channel's max-normalized
    weight (see module docstring for the model and its sources).

    Returns one row per undirected pair, sorted by
    ``(source_memory_id, target_memory_id)``:
    ``{"source_memory_id", "target_memory_id", "weight" (in [0, 1]),
    "shared_count" (0 for pairs without co-entity evidence), "reason"
    ("co-entity", "semantic", "temporal", or a "+"-join of the
    channels that evidenced the pair, in that order)}``.
    """
    temporal_rows = temporal_rows or []
    co_max = max((r["weight"] for r in co_entity_rows), default=0.0)
    sem_max = max((r["weight"] for r in semantic_rows), default=0.0)
    temp_max = max((r["weight"] for r in temporal_rows), default=0.0)
    combined: dict[tuple[int, int], dict[str, Any]] = {}
    for rows, channel_max, reason in (
        (co_entity_rows, co_max, "co-entity"),
        (semantic_rows, sem_max, "semantic"),
        (temporal_rows, temp_max, "temporal"),
    ):
        for r in rows:
            pair = (r["source_memory_id"], r["target_memory_id"])
            norm_w = r["weight"] / channel_max if channel_max > 0 else 0.0
            existing = combined.get(pair)
            if existing is None:
                combined[pair] = {
                    "source_memory_id": pair[0],
                    "target_memory_id": pair[1],
                    "weight": norm_w,
                    "shared_count": int(r.get("shared_count", 0)),
                    "reason": reason,
                }
            else:
                existing["weight"] = max(existing["weight"], norm_w)
                existing["shared_count"] = max(
                    existing["shared_count"], int(r.get("shared_count", 0))
                )
                if reason not in existing["reason"]:
                    existing["reason"] += f"+{reason}"
    return [combined[pair] for pair in sorted(combined)]


def load_memory_associations(
    pg_store,
    top_k: int | None = None,
    df_ceiling_frac: float = DF_CEILING_FRAC,
    min_sim: float | None = None,
) -> list[dict[str, Any]]:
    """Load all three association channels and return the unified substrate.

    Three read-only SELECTs (one per channel) + a pure in-Python merge —
    see ``combine_associations`` for the combination model. This is the
    entry point the workflow-graph source delegates to; downstream
    (layout, community detection) consumes the unified substrate and
    never distinguishes channels.
    """
    co_rows = load_co_entity_associations(
        pg_store, top_k=top_k, df_ceiling_frac=df_ceiling_frac
    )
    semantic_rows = load_semantic_associations(pg_store, top_k=top_k, min_sim=min_sim)
    temporal_rows = load_temporal_associations(pg_store, top_k=top_k)
    return combine_associations(co_rows, semantic_rows, temporal_rows)


__all__ = [
    "load_co_entity_associations",
    "load_semantic_associations",
    "load_temporal_associations",
    "combine_associations",
    "load_memory_associations",
    "DEFAULT_ASSOC_TOP_K",
    "DF_CEILING_FRAC",
    "DEFAULT_SEMANTIC_MIN_SIM",
    "DEFAULT_TEMPORAL_WINDOW_HOURS",
    "DEFAULT_TEMPORAL_MIN_ACCESS",
    "DEFAULT_TEMPORAL_SEED_LIMIT",
]
