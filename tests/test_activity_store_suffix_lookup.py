"""Unit test for ``activity_store.find_abs_path_by_suffix``'s LIKE-wildcard
escaping (contract A.3 hardening).

No live PG: a fake batch pool/connection/cursor records the SQL + bound
params, mirroring the fake-pool pattern in ``test_snapshot_pg_store_scope``,
so the ``%``/``_`` escaping is exercised without a database.
"""

from __future__ import annotations

from cortex_viz.infrastructure import activity_store


class _FakeCursor:
    def __init__(self, calls: list):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._calls.append((sql, params))

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self, calls: list):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._calls)

    def commit(self):
        pass


class _FakePool:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []

    def connection(self):
        return _FakeConn(self.calls)


class _FakeStore:
    def __init__(self):
        self.batch_pool = _FakePool()


def test_find_abs_path_by_suffix_escapes_like_wildcards():
    store = _FakeStore()
    # A suffix containing raw '%' and '_' must not widen the SQL LIKE match —
    # both must be escaped in the bound pattern, and the query must declare
    # ESCAPE '\' so Postgres honors the escaping.
    activity_store.find_abs_path_by_suffix(store, "foo%bar_baz")

    query_calls = [c for c in store.batch_pool.calls if c[1] is not None]
    assert query_calls, "expected the SELECT to be issued with bound params"
    sql, params = query_calls[-1]
    assert "ESCAPE '\\'" in sql
    pattern, file_pattern = params
    assert pattern == "%/foo\\%bar\\_baz"
    assert file_pattern == "file:%/foo\\%bar\\_baz"
