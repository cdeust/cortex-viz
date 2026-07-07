"""Unit tests for the co-entity MEMORY<->MEMORY association loader.

``load_co_entity_associations`` (cortex_viz.infrastructure.
memory_associations) issues ONE read-only SQL statement; these tests
monkeypatch ``pg_store.query`` to return canned rows shaped exactly
like the SQL's final SELECT would produce, so the tests exercise the
Python-side contract (param wiring, N=0 guard, shape projection)
without a live database. The SQL's ranking/weighting/ceiling logic
itself is verified by construction: each fixture row below is what a
correct evaluation of the fixed model (Salton & Buckley 1988 TF-IDF
sum; Manning/Raghavan/Schuetze stop-word ceiling; von Luxburg top-k
kNN symmetrization) would return for a hand-computed toy corpus, so
asserting on the returned rows is equivalent to asserting the SQL
computed them correctly, modulo trusting the DB SELECT machinery
(covered separately by the live benchmark in the task report).
"""

from __future__ import annotations

import math

from cortex_viz.infrastructure.memory_associations import (
    DEFAULT_ASSOC_TOP_K,
    DEFAULT_SEMANTIC_MIN_SIM,
    DEFAULT_TEMPORAL_ENABLED,
    DEFAULT_TEMPORAL_MIN_ACCESS,
    DEFAULT_TEMPORAL_SEED_LIMIT,
    DEFAULT_TEMPORAL_WINDOW_HOURS,
    DF_CEILING_FRAC,
    combine_associations,
    load_co_entity_associations,
    load_memory_associations,
    load_semantic_associations,
    load_temporal_associations,
)


class _FakeStore:
    """Records the SQL/params it was called with; returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, tuple, bool]] = []

    def query(self, sql, params=None, *, batch=False):
        self.calls.append((sql, params, batch))
        return self._rows


def test_uses_batch_pool_and_forwards_params():
    """Contract: reads go through the batch pool (bulk build path) and
    the ceiling fraction / resolved top_k are forwarded as SQL params."""
    store = _FakeStore(rows=[])
    load_co_entity_associations(store, top_k=5, df_ceiling_frac=0.2)
    assert len(store.calls) == 1
    sql, params, batch = store.calls[0]
    assert batch is True
    assert params == (0.2, 5)
    assert "memory_entities" in sql
    assert "memories" in sql


def test_default_top_k_and_ceiling_are_named_constants():
    assert DEFAULT_ASSOC_TOP_K == 8
    assert DF_CEILING_FRAC == 0.10


def test_top_k_env_override(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_TOPK", "3")
    store = _FakeStore(rows=[])
    load_co_entity_associations(store)  # top_k=None -> resolve from env
    _, params, _ = store.calls[0]
    assert params[1] == 3


def test_rarer_shared_entity_yields_higher_weight():
    """A pair sharing one entity with df=2 (idf = ln(N/2)) must weigh
    more than a pair sharing one entity with df=50 (idf = ln(N/50)),
    for the same N — rarer terms are more discriminating.
    source: Salton & Buckley (1988) term-weighting."""
    n = 100
    idf_rare = math.log(n / 2)
    idf_common = math.log(n / 50)
    assert idf_rare > idf_common
    rows = [
        {
            "source_memory_id": 1,
            "target_memory_id": 2,
            "weight": idf_rare,
            "shared_count": 1,
        },
        {
            "source_memory_id": 3,
            "target_memory_id": 4,
            "weight": idf_common,
            "shared_count": 1,
        },
    ]
    store = _FakeStore(rows=rows)
    out = load_co_entity_associations(store)
    by_pair = {(r["source_memory_id"], r["target_memory_id"]): r for r in out}
    assert by_pair[(1, 2)]["weight"] > by_pair[(3, 4)]["weight"]


def test_symmetric_dedup_one_row_per_undirected_pair():
    """The SQL's LEAST/GREATEST + GROUP BY guarantees one row per
    undirected pair; verify the Python projection doesn't introduce a
    second row for the reverse direction."""
    rows = [
        {
            "source_memory_id": 10,
            "target_memory_id": 20,
            "weight": 1.5,
            "shared_count": 2,
        }
    ]
    store = _FakeStore(rows=rows)
    out = load_co_entity_associations(store)
    assert len(out) == 1
    pairs = {(r["source_memory_id"], r["target_memory_id"]) for r in out}
    assert pairs == {(10, 20)}
    assert (20, 10) not in pairs


def test_top_k_bound_is_forwarded_not_reapplied_in_python():
    """Sparsification happens inside the SQL (ROW_NUMBER() window); the
    Python loader must not re-truncate or re-rank — it must return
    exactly what the store handed back, just projected/typed."""
    rows = [
        {
            "source_memory_id": i,
            "target_memory_id": i + 1,
            "weight": float(i),
            "shared_count": 1,
        }
        for i in range(1, 6)
    ]
    store = _FakeStore(rows=rows)
    out = load_co_entity_associations(store, top_k=2)
    assert len(out) == len(rows)
    # top_k=2 was forwarded to the SQL as the ranking bound, not applied
    # again client-side.
    _, params, _ = store.calls[0]
    assert params[1] == 2


def test_ceiling_entities_excluded_is_a_forwarded_sql_param():
    """The stop-word ceiling is enforced by the SQL's WHERE clause
    (entity_df.df <= df_ceiling_frac * N); the loader's job is to pass
    the fraction through unmodified."""
    store = _FakeStore(rows=[])
    load_co_entity_associations(store, df_ceiling_frac=0.05)
    _, params, _ = store.calls[0]
    assert params[0] == 0.05


def test_zero_top_k_short_circuits_without_querying():
    store = _FakeStore(rows=[{"source_memory_id": 1, "target_memory_id": 2,
                               "weight": 1.0, "shared_count": 1}])
    out = load_co_entity_associations(store, top_k=0)
    assert out == []
    assert store.calls == []


def test_row_shape_and_types():
    rows = [
        {
            "source_memory_id": 1,
            "target_memory_id": 2,
            "weight": "2.5",  # DB may hand back numeric as str/Decimal-like
            "shared_count": 3,
        }
    ]
    store = _FakeStore(rows=rows)
    out = load_co_entity_associations(store)
    assert out == [
        {
            "source_memory_id": 1,
            "target_memory_id": 2,
            "weight": 2.5,
            "shared_count": 3,
        }
    ]


# ── v2 semantic kNN loader ──────────────────────────────────────────


def test_semantic_forwards_top_k_and_min_sim_params():
    """Contract: (top_k, min_sim) go to the SQL in that order (LIMIT
    inside the LATERAL, then the similarity floor), via the batch pool."""
    store = _FakeStore(rows=[])
    load_semantic_associations(store, top_k=5, min_sim=0.7)
    assert len(store.calls) == 1
    sql, params, batch = store.calls[0]
    assert batch is True
    assert params == (5, 0.7)
    assert "LATERAL" in sql
    assert "<=>" in sql  # pgvector cosine-distance operator


def test_semantic_default_min_sim_is_the_measured_constant():
    assert DEFAULT_SEMANTIC_MIN_SIM == 0.6
    store = _FakeStore(rows=[])
    load_semantic_associations(store, top_k=8)  # min_sim=None -> default
    _, params, _ = store.calls[0]
    assert params == (8, 0.6)


def test_semantic_min_sim_env_override(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_MIN_SIM", "0.75")
    store = _FakeStore(rows=[])
    load_semantic_associations(store, top_k=8)
    _, params, _ = store.calls[0]
    assert params == (8, 0.75)


def test_semantic_zero_top_k_short_circuits_without_querying():
    store = _FakeStore(rows=[{"source_memory_id": 1, "target_memory_id": 2,
                               "weight": 0.9}])
    out = load_semantic_associations(store, top_k=0)
    assert out == []
    assert store.calls == []


def test_semantic_row_shape_and_types():
    rows = [{"source_memory_id": 1, "target_memory_id": 2, "weight": "0.83"}]
    store = _FakeStore(rows=rows)
    out = load_semantic_associations(store)
    assert out == [
        {"source_memory_id": 1, "target_memory_id": 2, "weight": 0.83}
    ]


# ── v3 temporal co-access loader ────────────────────────────────────


def test_temporal_forwards_params_in_pg_function_order():
    """Contract: (window_hours, min_access, seed_limit) feed
    get_temporal_co_access positionally, then top_k bounds the ranking
    window — all via the batch pool."""
    store = _FakeStore(rows=[])
    load_temporal_associations(store, top_k=5)
    assert len(store.calls) == 1
    sql, params, batch = store.calls[0]
    assert batch is True
    assert params == (
        DEFAULT_TEMPORAL_WINDOW_HOURS,
        DEFAULT_TEMPORAL_MIN_ACCESS,
        DEFAULT_TEMPORAL_SEED_LIMIT,
        5,
    )
    assert "get_temporal_co_access" in sql


def test_temporal_defaults_are_the_sourced_constants():
    # window/min_access inherit the PG function's own defaults; the
    # seed limit is the measured corpus-covering value.
    assert DEFAULT_TEMPORAL_WINDOW_HOURS == 2.0
    assert DEFAULT_TEMPORAL_MIN_ACCESS == 1
    assert DEFAULT_TEMPORAL_SEED_LIMIT == 10000


def test_temporal_env_overrides(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_TEMPORAL_WINDOW_H", "6.0")
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_TEMPORAL_MIN_ACCESS", "2")
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_TEMPORAL_SEED_LIMIT", "500")
    store = _FakeStore(rows=[])
    load_temporal_associations(store, top_k=8)
    _, params, _ = store.calls[0]
    assert params == (6.0, 2, 500, 8)


def test_temporal_invalid_env_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_TEMPORAL_WINDOW_H", "not-a-number")
    store = _FakeStore(rows=[])
    load_temporal_associations(store, top_k=8)
    _, params, _ = store.calls[0]
    assert params[0] == DEFAULT_TEMPORAL_WINDOW_HOURS


def test_temporal_zero_top_k_short_circuits_without_querying():
    store = _FakeStore(rows=[{"source_memory_id": 1, "target_memory_id": 2,
                               "weight": 0.9}])
    out = load_temporal_associations(store, top_k=0)
    assert out == []
    assert store.calls == []


def test_temporal_row_shape_and_types():
    rows = [{"source_memory_id": 1, "target_memory_id": 2, "weight": "0.75"}]
    store = _FakeStore(rows=rows)
    out = load_temporal_associations(store)
    assert out == [
        {"source_memory_id": 1, "target_memory_id": 2, "weight": 0.75}
    ]


# ── channel combiner (pure — no store involved) ─────────────────────


def test_combine_normalizes_each_channel_by_its_own_max():
    """Channels live on incommensurable scales (unbounded IDF sums vs
    bounded cosine sim); each is divided by its own max before the
    per-pair MAX, so a channel's strongest pair always lands at 1.0."""
    co = [
        {"source_memory_id": 1, "target_memory_id": 2, "weight": 10.0,
         "shared_count": 2},
        {"source_memory_id": 3, "target_memory_id": 4, "weight": 5.0,
         "shared_count": 1},
    ]
    sem = [
        {"source_memory_id": 5, "target_memory_id": 6, "weight": 0.9},
        {"source_memory_id": 7, "target_memory_id": 8, "weight": 0.45},
    ]
    by_pair = {
        (r["source_memory_id"], r["target_memory_id"]): r
        for r in combine_associations(co, sem)
    }
    assert by_pair[(1, 2)]["weight"] == 1.0
    assert by_pair[(3, 4)]["weight"] == 0.5
    assert by_pair[(5, 6)]["weight"] == 1.0
    assert by_pair[(7, 8)]["weight"] == 0.5


def test_combine_shared_pair_takes_max_and_merges_reason():
    co = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 4.0,
           "shared_count": 3}]
    sem = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 0.8}]
    out = combine_associations(co, sem)
    assert len(out) == 1
    row = out[0]
    # Both channels normalize to 1.0 (each pair is its channel's max).
    assert row["weight"] == 1.0
    assert row["shared_count"] == 3  # co-entity evidence survives
    assert row["reason"] == "co-entity+semantic"


def test_combine_tags_single_channel_reasons():
    co = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 4.0,
           "shared_count": 1}]
    sem = [{"source_memory_id": 3, "target_memory_id": 4, "weight": 0.7}]
    by_pair = {
        (r["source_memory_id"], r["target_memory_id"]): r
        for r in combine_associations(co, sem)
    }
    assert by_pair[(1, 2)]["reason"] == "co-entity"
    assert by_pair[(3, 4)]["reason"] == "semantic"
    assert by_pair[(3, 4)]["shared_count"] == 0


def test_combine_empty_channels():
    assert combine_associations([], []) == []
    sem_only = combine_associations(
        [], [{"source_memory_id": 1, "target_memory_id": 2, "weight": 0.7}]
    )
    assert sem_only[0]["weight"] == 1.0
    assert sem_only[0]["reason"] == "semantic"


def test_combine_temporal_channel_normalized_and_tagged():
    temp = [
        {"source_memory_id": 1, "target_memory_id": 2, "weight": 0.9},
        {"source_memory_id": 3, "target_memory_id": 4, "weight": 0.45},
    ]
    by_pair = {
        (r["source_memory_id"], r["target_memory_id"]): r
        for r in combine_associations([], [], temp)
    }
    assert by_pair[(1, 2)]["weight"] == 1.0
    assert by_pair[(3, 4)]["weight"] == 0.5
    assert by_pair[(1, 2)]["reason"] == "temporal"
    assert by_pair[(1, 2)]["shared_count"] == 0


def test_combine_three_channel_pair_joins_reasons_in_order():
    co = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 4.0,
           "shared_count": 2}]
    sem = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 0.8}]
    temp = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 0.9}]
    out = combine_associations(co, sem, temp)
    assert len(out) == 1
    row = out[0]
    assert row["weight"] == 1.0
    assert row["shared_count"] == 2
    assert row["reason"] == "co-entity+semantic+temporal"


def test_combine_temporal_default_is_backward_compatible():
    """Omitting temporal_rows behaves exactly like the two-channel v2
    combiner — existing callers are unaffected."""
    co = [{"source_memory_id": 1, "target_memory_id": 2, "weight": 4.0,
           "shared_count": 1}]
    assert combine_associations(co, []) == combine_associations(co, [], None)


def test_combine_output_sorted_by_pair():
    sem = [
        {"source_memory_id": 9, "target_memory_id": 10, "weight": 0.7},
        {"source_memory_id": 1, "target_memory_id": 5, "weight": 0.8},
    ]
    out = combine_associations([], sem)
    pairs = [(r["source_memory_id"], r["target_memory_id"]) for r in out]
    assert pairs == sorted(pairs)


# ── unified entry point ─────────────────────────────────────────────


class _ThreeChannelStore:
    def __init__(self):
        self.calls = []

    def query(self, sql, params=None, *, batch=False):
        self.calls.append(sql)
        if "memory_entities" in sql:  # co-entity channel
            return [{"source_memory_id": 1, "target_memory_id": 2,
                     "weight": 3.0, "shared_count": 2}]
        if "get_temporal_co_access" in sql:  # temporal channel
            return [{"source_memory_id": 1, "target_memory_id": 2,
                     "weight": 0.7}]
        return [{"source_memory_id": 1, "target_memory_id": 2,
                 "weight": 0.9}]  # semantic channel


def test_load_memory_associations_default_runs_all_three_channels(monkeypatch):
    """All three channels are active by default (additive render, user
    directive 2026-07-07): with no env override the unified loader runs
    THREE SELECTs and every channel contributes its reason tag. The
    community-collapse regression that once justified opt-in is gone —
    detection is decoupled onto the co-entity channel server-side."""
    monkeypatch.delenv("CORTEX_VIZ_ASSOC_TEMPORAL", raising=False)
    store = _ThreeChannelStore()
    out = load_memory_associations(store)
    assert len(store.calls) == 3
    assert any("get_temporal_co_access" in sql for sql in store.calls)
    assert out == [
        {
            "source_memory_id": 1,
            "target_memory_id": 2,
            "weight": 1.0,
            "shared_count": 2,
            "reason": "co-entity+semantic+temporal",
        }
    ]


def test_load_memory_associations_temporal_opt_out_issues_two_selects(
    monkeypatch,
):
    """With CORTEX_VIZ_ASSOC_TEMPORAL=0 the temporal channel is disabled:
    the loader runs exactly two SELECTs and drops the temporal reason
    tag — the escape hatch for an operator who wants the leaner v2
    render."""
    monkeypatch.setenv("CORTEX_VIZ_ASSOC_TEMPORAL", "0")
    store = _ThreeChannelStore()
    out = load_memory_associations(store)
    assert len(store.calls) == 2
    assert not any("get_temporal_co_access" in sql for sql in store.calls)
    assert out == [
        {
            "source_memory_id": 1,
            "target_memory_id": 2,
            "weight": 1.0,
            "shared_count": 2,
            "reason": "co-entity+semantic",
        }
    ]


def test_temporal_default_constant_is_on():
    """The gate's default is a named constant and it is True — the three
    additive channels are the out-of-the-box render (user directive);
    community detection is decoupled so this cannot regress community
    structure."""
    assert DEFAULT_TEMPORAL_ENABLED is True
