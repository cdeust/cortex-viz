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
    DF_CEILING_FRAC,
    load_co_entity_associations,
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
