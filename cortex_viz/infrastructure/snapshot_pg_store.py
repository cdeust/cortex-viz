"""PostgreSQL persistence for the complete workflow-graph SNAPSHOT.

A durable, build-independent copy of the fully assembled graph — every node
AND edge (backbone, memories, AST symbols, entities). It exists so the
full-graph endpoint can be served instantly from PG without depending on the
volatile in-process build cache or re-running the multi-minute build: the
cache loops/empties between builds, but a persisted snapshot is stable.

Written once at build completion (where the in-process cache holds the
finished graph); read by ``serve_graph_full``. The payload is gzip(JSON)
stored as BYTEA — the full graph is tens of MB as JSON, a few MB gzipped —
and the handler streams those exact bytes with ``Content-Encoding: gzip``,
so there is no server-side re-encode on read.

One row only: the newest snapshot full-replaces the prior (same global-
snapshot invalidation model as ``workflow_graph_layout``). Pure
infrastructure — batch-pool I/O only. Mirrors ``layout_pg_store`` /
``lod_pg_store``'s ``_conn(store)`` idiom.
"""

from __future__ import annotations

import gzip
import json

# DDL — self-ensured on first use (CREATE TABLE IF NOT EXISTS), same pattern
# as lod_pg_store. ``created_at`` orders "latest" when (defensively) more than
# one row is ever present; the writer keeps exactly one.
_DDL = """
CREATE TABLE IF NOT EXISTS workflow_graph_snapshot (
    fingerprint TEXT PRIMARY KEY,
    payload BYTEA NOT NULL,
    node_count INT NOT NULL,
    edge_count INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _conn(store):
    """Context-manager accessor on the batch pool. See ``layout_pg_store._conn``.

    Raises ``AttributeError`` when ``store`` has no ``batch_pool`` (snapshot
    persistence is PG-only, same as the layout it mirrors).
    """
    pool = getattr(store, "batch_pool", None)
    if pool is None:
        raise AttributeError(
            "snapshot_pg_store requires a store exposing .batch_pool (PgMemoryStore)"
        )
    return pool.connection()


def _ensure_table(store) -> None:
    """Idempotently create the snapshot table (first-use self-ensure)."""
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


def write_snapshot(store, *, fingerprint: str, graph: dict) -> dict:
    """Persist ``graph`` as the latest full-graph snapshot. Full-replace.

    Pre: ``graph`` is ``{"nodes": [...], "edges"|"links": [...], "meta": {...}}``
    — the finished build cache. Post: the table holds exactly one row (this
    snapshot); any prior row is removed. Returns
    ``{"node_count", "edge_count", "bytes"}``.
    """
    _ensure_table(store)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or graph.get("links") or []
    # ``default=str`` so any non-JSON-native value carried on a node/edge/meta
    # (datetime ``computed_at`` / ``created_at`` timestamps were the first
    # offender — they crashed the whole snapshot write) degrades to its string
    # form rather than aborting the persist. The viz client never parses these
    # back to typed values; it reads id/kind/x/y/color/label.
    payload = gzip.compress(
        json.dumps(
            {"nodes": nodes, "edges": edges, "meta": graph.get("meta") or {}},
            separators=(",", ":"),
            default=str,
        ).encode("utf-8"),
        compresslevel=6,
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM workflow_graph_snapshot")
        cur.execute(
            "INSERT INTO workflow_graph_snapshot "
            "(fingerprint, payload, node_count, edge_count) "
            "VALUES (%s, %s, %s, %s)",
            (fingerprint, payload, len(nodes), len(edges)),
        )
        conn.commit()
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "bytes": len(payload),
    }


def read_latest_snapshot(store) -> dict | None:
    """Return the current snapshot, or None if none has been persisted yet.

    Result: ``{"fingerprint", "payload_gzip": bytes, "node_count",
    "edge_count"}``. ``payload_gzip`` is the gzipped JSON exactly as stored —
    the handler streams it straight to the client with ``Content-Encoding:
    gzip``, no re-encode.
    """
    _ensure_table(store)
    sql = (
        "SELECT fingerprint, payload, node_count, edge_count "
        "FROM workflow_graph_snapshot ORDER BY created_at DESC LIMIT 1"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    # The batch pool is configured with dict_row (see pg_store.py).
    return {
        "fingerprint": row["fingerprint"],
        "payload_gzip": bytes(row["payload"]),
        "node_count": int(row["node_count"]),
        "edge_count": int(row["edge_count"]),
    }
