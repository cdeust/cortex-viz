"""Replay-window semantics of ``activity_store.read_recent``.

The fix this pins is behavioural, not textual: the former
``ORDER BY id ASC LIMIT`` returned the OLDEST rows in the table on a fresh
``since=0`` connect, so once ``session_activity`` grew past the replay limit a
new page painted ancient history and never showed today's work. The sibling
assertions in ``test_graph_persistence_coverage_contracts`` match the SQL
*string*; a rewrite that keeps those words while inverting the window would
pass them and reintroduce the bug. So this drives the real ``read_recent``
against a live SQL engine and asserts which rows come back, in which order.

SQLite stands in for PostgreSQL deliberately — the DDL is PostgreSQL-only
(``BIGSERIAL``/``JSONB``/``TIMESTAMPTZ``) and CI has no database, but the
query under test is portable, and the property at stake (newest bounded
window, restored to chronological order) is backend-independent.
"""

from __future__ import annotations

import sqlite3

import pytest

from cortex_viz.infrastructure import activity_store

_COLUMNS = (
    "id INTEGER PRIMARY KEY, session_id TEXT, ts REAL, event_type TEXT, "
    "tool TEXT, action TEXT, target_id TEXT, target_kind TEXT, "
    "target_label TEXT, edge_kind TEXT, cwd TEXT, detail TEXT"
)


class _Cursor:
    """Translates the module's PostgreSQL paramstyle onto sqlite3."""

    def __init__(self, conn):
        self._cur = conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._cur.close()
        return False

    def execute(self, sql, params=None):
        self._cur.execute(sql.replace("%s", "?"), params or ())

    def fetchall(self):
        return [dict(row) for row in self._cur.fetchall()]


class _Connection:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return _Cursor(self._conn)


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return _Connection(self._conn)


class _Store:
    def __init__(self, conn):
        self.batch_pool = _Pool(conn)


@pytest.fixture
def store(monkeypatch):
    """A real SQL engine holding 10 rows, ids 1..10."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE session_activity ({_COLUMNS})")
    conn.executemany(
        "INSERT INTO session_activity (id, session_id, action) VALUES (?, ?, ?)",
        [(i, "s1", "read") for i in range(1, 11)],
    )
    conn.commit()
    # The DDL is PostgreSQL-specific; the table above is its SQLite equivalent.
    monkeypatch.setattr(activity_store, "_ensure_table", lambda _store: None)
    return _Store(conn)


def test_a_bounded_replay_returns_the_newest_rows_not_the_oldest(store):
    """The regression itself: with 10 rows and room for 3, a fresh connect must
    replay 8, 9, 10 — the pre-fix query replayed 1, 2, 3 forever."""
    rows = activity_store.read_recent(store, limit=3, since_id=0)

    assert [row["id"] for row in rows] == [8, 9, 10]


def test_the_bounded_window_is_still_delivered_oldest_first(store):
    """Spine order: the client appends deltas in arrival order, so a
    newest-first replay would draw the session's causal chain backwards."""
    rows = activity_store.read_recent(store, limit=4, since_id=0)

    assert [row["id"] for row in rows] == [7, 8, 9, 10]


def test_a_resume_reads_only_past_the_clients_cursor(store):
    rows = activity_store.read_recent(store, limit=100, since_id=7)

    assert [row["id"] for row in rows] == [8, 9, 10]


def test_a_cursor_at_the_head_replays_nothing(store):
    assert activity_store.read_recent(store, limit=100, since_id=10) == []


def test_seq_mirrors_the_durable_id_for_the_client(store):
    """``seq`` is what the client dedups on; it must equal the durable id, not
    a positional index within the replayed window."""
    rows = activity_store.read_recent(store, limit=2, since_id=0)

    assert [(row["id"], row["seq"]) for row in rows] == [(9, 9), (10, 10)]
