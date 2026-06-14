"""PostgreSQL persistence for the live session-activity spine.

Every captured Claude action (one normalized row from
``core.activity_graph.normalize_event``) is appended here, so the live graph
survives a page reload / server restart and a session can be replayed from its
first action. The SSE stream is the live channel; this table is the durable
log behind it.

Pure infrastructure — batch-pool I/O only, self-ensured DDL. Mirrors
``layout_pg_store`` / ``lod_pg_store``.
"""

from __future__ import annotations

import json

_DDL = """
CREATE TABLE IF NOT EXISTS session_activity (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts DOUBLE PRECISION,
    event_type TEXT,
    tool TEXT,
    action TEXT,
    target_id TEXT,
    target_kind TEXT,
    target_label TEXT,
    edge_kind TEXT,
    cwd TEXT,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS session_activity_recent_idx
    ON session_activity (id DESC);
CREATE INDEX IF NOT EXISTS session_activity_session_idx
    ON session_activity (session_id, id);
"""


def _conn(store):
    """Batch-pool accessor. See ``layout_pg_store._conn``."""
    pool = getattr(store, "batch_pool", None)
    if pool is None:
        raise AttributeError(
            "activity_store requires a store exposing .batch_pool (PgMemoryStore)"
        )
    return pool.connection()


def _ensure_table(store) -> None:
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


def record_activity(store, row: dict) -> int:
    """Append one normalized activity row. Returns its monotonic ``id``.

    The ``id`` doubles as the activity's sequence number (the spine's order)
    and the SSE replay cursor (``since_id``).
    """
    _ensure_table(store)
    sql = (
        "INSERT INTO session_activity "
        "(session_id, ts, event_type, tool, action, target_id, target_kind, "
        " target_label, edge_kind, cwd, detail) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            (
                row.get("session_id") or "live",
                row.get("ts"),
                row.get("event_type"),
                row.get("tool"),
                row.get("action"),
                row.get("target_id"),
                row.get("target_kind"),
                row.get("target_label"),
                row.get("edge_kind"),
                row.get("cwd"),
                json.dumps(row.get("detail") or {}),
            ),
        )
        new_id = int(cur.fetchone()["id"])
        conn.commit()
    return new_id


def read_recent(store, *, limit: int = 2000, since_id: int = 0) -> list[dict]:
    """Return activity rows with ``id > since_id``, oldest-first (spine order).

    Used on SSE connect to replay the session so a fresh page paints the
    actions that already happened, then streams new ones live. ``limit`` caps
    the replay so an enormous backlog can't stall first paint.
    """
    _ensure_table(store)
    sql = (
        "SELECT id, session_id, ts, event_type, tool, action, target_id, "
        "       target_kind, target_label, edge_kind, cwd, detail "
        "FROM session_activity WHERE id > %s ORDER BY id ASC LIMIT %s"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (int(since_id), int(limit)))
        rows = cur.fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["seq"] = d["id"]
        out.append(d)
    return out
