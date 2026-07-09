"""Unit tests for D10's ``workflow_graph_snapshot`` scoping.

No live PG: a fake batch pool/connection/cursor records every SQL statement
and its params, and serves canned SELECT rows, so these tests exercise the
exact scoped SQL ``write_snapshot`` / ``read_latest_snapshot`` issue without
a database. Live cross-scope coexistence is proven separately against the
dev DB (see the increment's acceptance-criterion run).
"""

from __future__ import annotations

from cortex_viz.infrastructure import snapshot_pg_store


class _FakeCursor:
    def __init__(self, calls: list, row: dict | None):
        self._calls = calls
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._calls.append((sql, params))

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, calls: list, row: dict | None):
        self._calls = calls
        self._row = row
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._calls, self._row)

    def commit(self):
        self.committed = True


class _FakePool:
    def __init__(self, row: dict | None = None):
        self.calls: list[tuple[str, tuple | None]] = []
        self._row = row

    def connection(self):
        return _FakeConn(self.calls, self._row)


class _FakeStore:
    def __init__(self, row: dict | None = None):
        self.batch_pool = _FakePool(row)


def _graph() -> dict:
    return {
        "nodes": [{"id": "n1", "kind": "memory"}],
        "edges": [{"source": "n1", "target": "n1", "kind": "k"}],
        "meta": {"schema": "workflow_graph.v1"},
    }


def test_write_snapshot_deletes_and_inserts_scoped():
    store = _FakeStore()
    snapshot_pg_store.write_snapshot(
        store, fingerprint="fp-a", graph=_graph(), scope="scope-a"
    )
    calls = store.batch_pool.calls
    # DDL (3 statements) + DELETE + INSERT = 5 execute calls.
    delete_call = next(c for c in calls if c[0].strip().startswith("DELETE"))
    insert_call = next(c for c in calls if c[0].strip().startswith("INSERT"))
    assert "WHERE scope = %s" in delete_call[0]
    assert delete_call[1] == ("scope-a",)
    assert "scope" in insert_call[0]
    assert insert_call[1][-1] == "scope-a"


def test_read_latest_snapshot_filters_by_scope():
    row = {
        "fingerprint": "fp-b",
        "payload": b"gzip-bytes",
        "node_count": 1,
        "edge_count": 1,
        "format": "ndjson.v1",
    }
    store = _FakeStore(row=row)
    result = snapshot_pg_store.read_latest_snapshot(store, scope="scope-b")
    calls = store.batch_pool.calls
    select_call = next(c for c in calls if c[0].strip().startswith("SELECT"))
    assert "WHERE scope = %s" in select_call[0]
    assert select_call[1] == ("scope-b",)
    assert result["fingerprint"] == "fp-b"


def test_read_latest_snapshot_none_when_scope_absent():
    store = _FakeStore(row=None)
    assert snapshot_pg_store.read_latest_snapshot(store, scope="unknown") is None


def test_ddl_adds_scope_column_additively():
    """The self-ensure DDL is idempotent additive ALTERs — no DROP, no
    schema-breaking statement, matching the ``format`` column precedent."""
    store = _FakeStore()
    snapshot_pg_store._ensure_table(store)
    ddl_calls = [c[0] for c in store.batch_pool.calls]
    scope_ddl = next(c for c in ddl_calls if "scope" in c)
    assert "ADD COLUMN IF NOT EXISTS scope" in scope_ddl
    assert "DEFAULT 'default'" in scope_ddl
    assert "DROP" not in scope_ddl.upper()
