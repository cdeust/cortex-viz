"""Unit tests for D10/D11's ``workflow_graph_snapshot_scoped`` scoping +
table isolation.

No live PG: a fake batch pool/connection/cursor records every SQL statement
and its params, and serves canned SELECT rows, so these tests exercise the
exact scoped SQL ``write_snapshot`` / ``read_latest_snapshot`` issue against
the DEDICATED table without a database. Live cross-scope coexistence and
legacy-writer immunity are proven separately against the dev DB (see the
increment's acceptance-criterion run)."""

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


def _scoped_insert_calls(calls):
    """INSERTs targeting the dedicated table with bound params — excludes the
    parameterless legacy-backfill ``INSERT ... SELECT`` (see
    ``_DDL_MIGRATE_LEGACY``)."""
    return [
        c
        for c in calls
        if c[0].strip().startswith("INSERT INTO workflow_graph_snapshot_scoped")
        and c[1] is not None
    ]


def test_write_snapshot_deletes_and_inserts_scoped():
    store = _FakeStore()
    snapshot_pg_store.write_snapshot(
        store, fingerprint="fp-a", graph=_graph(), scope="scope-a"
    )
    calls = store.batch_pool.calls
    delete_call = next(
        c
        for c in calls
        if c[0].strip().startswith("DELETE FROM workflow_graph_snapshot_scoped")
    )
    insert_call = _scoped_insert_calls(calls)[0]
    assert "WHERE scope = %s" in delete_call[0]
    assert delete_call[1] == ("scope-a",)
    assert "scope" in insert_call[0]
    assert insert_call[1][0] == "scope-a"


def test_write_snapshot_never_touches_legacy_table():
    """The scoped writer must not DELETE/INSERT against the legacy table —
    only the dedicated table (D11 table-isolation fix)."""
    store = _FakeStore()
    snapshot_pg_store.write_snapshot(
        store, fingerprint="fp-a", graph=_graph(), scope="scope-a"
    )
    calls = store.batch_pool.calls
    mutating = [
        c
        for c in calls
        if c[0].strip().startswith(("DELETE", "INSERT")) and c[1] is not None
    ]
    for sql, _params in mutating:
        assert "workflow_graph_snapshot_scoped" in sql
        assert "workflow_graph_snapshot " not in sql + " "  # no bare legacy table name


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
    assert "FROM workflow_graph_snapshot_scoped" in select_call[0]
    assert "WHERE scope = %s" in select_call[0]
    assert select_call[1] == ("scope-b",)
    assert result["fingerprint"] == "fp-b"


def test_read_latest_snapshot_none_when_scope_absent():
    store = _FakeStore(row=None)
    assert snapshot_pg_store.read_latest_snapshot(store, scope="unknown") is None


def test_ddl_creates_scoped_table_with_composite_pk():
    """The dedicated table's DDL declares the composite (scope, fingerprint)
    primary key — a fingerprint collision across scopes is not a key
    collision (D11 fix for the fingerprint-only PK risk)."""
    store = _FakeStore()
    snapshot_pg_store._ensure_table(store)
    ddl_calls = [c[0] for c in store.batch_pool.calls]
    scoped_ddl = next(c for c in ddl_calls if "workflow_graph_snapshot_scoped" in c and "CREATE TABLE" in c)
    assert "PRIMARY KEY (scope, fingerprint)" in scoped_ddl
    assert "DROP" not in scoped_ddl.upper()


def test_ddl_legacy_table_left_additive_no_drop():
    """The legacy table is still created (for an old binary's continued
    operation) but never dropped — additive migration only."""
    store = _FakeStore()
    snapshot_pg_store._ensure_table(store)
    ddl_calls = [c[0] for c in store.batch_pool.calls]
    assert any("CREATE TABLE IF NOT EXISTS workflow_graph_snapshot " in c for c in ddl_calls)
    assert all("DROP" not in c.upper() for c in ddl_calls)


def test_ensure_table_runs_idempotent_legacy_migration_insert():
    """The one-time backfill is expressed as a guarded INSERT ... SELECT ...
    WHERE NOT EXISTS — safe to run on every _ensure_table call."""
    store = _FakeStore()
    snapshot_pg_store._ensure_table(store)
    calls = store.batch_pool.calls
    migrate_call = next(
        c
        for c in calls
        if c[0].strip().startswith("INSERT INTO workflow_graph_snapshot_scoped")
        and c[1] is None
    )
    assert "WHERE NOT EXISTS" in migrate_call[0]
    assert "'default'" in migrate_call[0]
