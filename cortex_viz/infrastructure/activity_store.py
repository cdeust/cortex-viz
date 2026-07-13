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
CREATE INDEX IF NOT EXISTS session_activity_target_idx
    ON session_activity (target_kind, target_id);
"""

# A post-P4 (path-unification) FILE target_id is ``file:<10-hex-hash>``
# (``core.activity_paths.is_canonical_file_target_id``'s Python-side
# definition, mirrored here as a Postgres regex so the legacy-scan query can
# push the "already canonical, skip it" filter down to the server instead of
# fetching every file-kind row and filtering in Python).
_LEGACY_FILE_ID_SQL = "target_kind = 'file' AND target_id !~ '^file:[0-9a-f]{10}$'"


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


def find_by_target_ids(
    store, target_ids: list[str], *, limit: int = 2000
) -> list[dict]:
    """Activity rows whose ``target_id`` is in ``target_ids`` — the fast
    path for ``core.wiki_page_actions`` (page -> source-file -> activity):
    an indexed equality lookup (``session_activity_target_idx``) against the
    canonical post-P4 ``file:<hash>`` ids ``core.wiki_page_actions`` already
    resolved. ``limit`` mirrors ``read_recent``'s replay bound (2000) so a
    hot file with a huge activity history can't stall the request.
    """
    if not target_ids:
        return []
    _ensure_table(store)
    sql = (
        "SELECT id, session_id, ts, event_type, tool, action, target_id, "
        "       target_kind, target_label, edge_kind, cwd, detail "
        "FROM session_activity WHERE target_kind = 'file' AND target_id = ANY(%s) "
        "ORDER BY id DESC LIMIT %s"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (list(target_ids), int(limit)))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def find_abs_path_by_label(store, label: str) -> str | None:
    """Most-recent absolute filesystem path for a bare basename ``label``.

    Backs the ``/api/file-diff`` basename-resolution rung (contract A.3):
    the client sometimes has only a file's display label (no path), so this
    resolves it against the activity spine's own record of where that file
    actually lives — ``detail->>'path'`` when present (canonical rows),
    falling back to the legacy ``file:<raw path>`` target_id's embedded
    path (``target_id[5:]``, i.e. after the ``file:`` prefix). Only rows
    that carry an absolute path are eligible, so a resolution never lands
    on a bare hash or a relative fragment. Query proven live against
    production data (9352/9352 candidate rows resolvable).
    """
    if not label:
        return None
    _ensure_table(store)
    sql = (
        "SELECT COALESCE(NULLIF(detail->>'path',''), substring(target_id from 6)) "
        "  AS abs_path "
        "FROM session_activity "
        "WHERE target_kind='file' AND target_label=%s "
        "  AND (detail->>'path' LIKE '/%%' OR target_id LIKE 'file:/%%') "
        "ORDER BY id DESC LIMIT 1"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (label,))
        row = cur.fetchone()
    return (row or {}).get("abs_path") or None


def find_abs_path_by_suffix(store, suffix: str) -> str | None:
    """Most-recent absolute path ending in ``/<suffix>`` (contract A.3).

    Backs the relative-name-with-slash resolution rung: when the registry
    of known repo roots doesn't have a match, fall back to a suffix search
    over the same activity-spine path data ``find_abs_path_by_label`` uses.
    """
    if not suffix:
        return None
    _ensure_table(store)
    pattern = f"%/{suffix}"
    sql = (
        "SELECT COALESCE(NULLIF(detail->>'path',''), substring(target_id from 6)) "
        "  AS abs_path "
        "FROM session_activity "
        "WHERE target_kind='file' "
        "  AND (detail->>'path' LIKE %s OR target_id LIKE %s) "
        "ORDER BY id DESC LIMIT 1"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (pattern, f"file:{pattern}"))
        row = cur.fetchone()
    return (row or {}).get("abs_path") or None


def scan_legacy_file_rows(store, *, limit: int = 2000) -> list[dict]:
    """Bounded, newest-first scan of pre-P4 FILE rows — ``target_id`` still
    embeds the raw literal path (``file:<raw path>``) instead of the
    canonical hash (see ``core.activity_paths``' module docstring for why
    these exist: rows written before the path-unification fix shipped).

    Callers (``core.wiki_page_actions.match_activity_rows``) recompute each
    row's canonical id from its embedded raw path + stored ``cwd`` and match
    against the same resolved target-id set the fast path
    (``find_by_target_ids``) uses — so old activity still joins to its wiki
    page without a DB migration. ``limit`` mirrors ``read_recent``'s replay
    bound (2000): a large legacy backlog degrades to "the N most recent
    legacy rows checked" rather than an unbounded scan.
    """
    _ensure_table(store)
    sql = (
        "SELECT id, session_id, ts, event_type, tool, action, target_id, "
        "       target_kind, target_label, edge_kind, cwd, detail "
        f"FROM session_activity WHERE {_LEGACY_FILE_ID_SQL} "
        "ORDER BY id DESC LIMIT %s"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (int(limit),))
        rows = cur.fetchall()
    return [dict(r) for r in rows]
