"""Unit tests for the MEMORY→MEMORY supersession-edge loader.

``load_supersede_edges`` (cortex_viz.infrastructure.memory_supersede)
issues ONE read-only SELECT over the recorded ``memories.supersedes_id``
column; these tests monkeypatch ``pg_store.query`` to return canned
rows shaped like the SQL's SELECT would produce, exercising the
Python-side contract (batch wiring, shape projection) without a live
database. The lineage itself is written by Cortex's supersede write
path and never re-derived here.
"""

from __future__ import annotations

from cortex_viz.infrastructure.memory_supersede import load_supersede_edges


class _FakeStore:
    """Records the SQL/params it was called with; returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, tuple, bool]] = []

    def query(self, sql, params=None, *, batch=False):
        self.calls.append((sql, params, batch))
        return self._rows


def test_uses_batch_pool_and_reads_recorded_column():
    """Contract: one read-only SELECT through the batch pool, over the
    recorded supersedes_id column (never re-derived), live newer side
    only (NOT is_stale)."""
    store = _FakeStore(rows=[])
    load_supersede_edges(store)
    assert len(store.calls) == 1
    sql, params, batch = store.calls[0]
    assert batch is True
    assert params == ()
    assert "supersedes_id" in sql
    assert "is_stale" in sql
    assert "INSERT" not in sql.upper().replace("IS NOT NULL", "")
    assert "UPDATE" not in sql.upper()


def test_row_shape_and_types():
    store = _FakeStore(
        rows=[
            {"source_memory_id": 42, "target_memory_id": 7},
            {"source_memory_id": 43, "target_memory_id": 42},
        ]
    )
    out = load_supersede_edges(store)
    assert out == [
        {"source_memory_id": 42, "target_memory_id": 7},
        {"source_memory_id": 43, "target_memory_id": 42},
    ]


def test_empty_corpus_returns_empty_list():
    store = _FakeStore(rows=[])
    assert load_supersede_edges(store) == []
