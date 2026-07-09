"""PostgreSQL persistence for the complete workflow-graph SNAPSHOT.

A durable, build-independent copy of the fully assembled graph — every node
AND edge (backbone, memories, AST symbols, entities). It exists so the
full-graph endpoints can be served instantly from PG without depending on
the volatile in-process build cache or re-running the multi-hour build: the
cache loops/empties between builds, but a persisted snapshot is stable.

Written once at build completion (where the in-process cache holds the
finished graph); read by ``serve_graph_full`` / ``serve_graph_full_stream``.

Scoping (D10, inc5.5): the table is shared PostgreSQL — every viz server
instance, on every checkout, writes into the SAME table. Prior to D10 this
was "last write wins" (``DELETE`` all + single-row ``INSERT``, no
discriminant): two instances serving different checkouts of this repo could
overwrite each other's snapshot, so one instance could end up serving the
graph of an unrelated checkout. ``scope`` is that discriminant — see
``cortex_viz.shared.instance_scope.resolve_instance_scope``. Both
``write_snapshot`` and ``read_latest_snapshot`` take a caller-supplied
``scope`` (composition-root wiring, not resolved inside this infra module).

Two-way version compat (additive migration — ``ADD COLUMN IF NOT EXISTS``,
no data loss, no rewritten rows):

* OLD code (pre-D10) running against a NEW (post-D10) table: its unscoped
  ``INSERT`` names explicit columns, not ``scope``, so PostgreSQL fills
  ``scope`` from the column DEFAULT (``'default'``) — the insert still
  succeeds. Its unscoped ``DELETE FROM workflow_graph_snapshot`` (no WHERE)
  still runs, though, and removes EVERY scope's row, not just its own — an
  old writer coexisting with new (scoped) writers can still wipe another
  instance's snapshot. This is an accepted residual risk of running mixed
  code versions against one shared table simultaneously; it does not affect
  same-version deployments (this increment's acceptance criterion), and the
  old code is out of scope for this change. Its unscoped ``SELECT ... ORDER
  BY created_at DESC LIMIT 1`` (no WHERE) also stays global — an old reader
  picks whichever scope was written most recently by ANYONE, i.e. keeps the
  pre-D10 "last write wins" read behaviour for itself even after the schema
  migrates.
* NEW code running against an OLD (pre-migration) table: ``_ensure_table``
  runs the additive ``ALTER TABLE ADD COLUMN IF NOT EXISTS`` on every call
  (idempotent, same pattern as the pre-existing ``format`` column), so the
  column always exists before the scoped ``DELETE``/``INSERT``/``SELECT``
  execute. No manual migration step required.

Storage formats (the ``format`` column):

* ``ndjson.v1`` (current writer) — gzip of newline-delimited JSON frames::

      {"node_total":N,"edge_total":E,"meta":{...}}
      {"nodes":[...batch...]}     (repeated)
      {"edges":[...batch...]}     (repeated)

  Framed at write time so the stream endpoint is a pure
  decompress-and-forward — no server-side parsing on ANY read. Chosen when
  the single-document form crossed ~1.17 GB decompressed (measured
  2026-07-02, 278,557 nodes / 5,526,064 edges): one JSON document of that
  size can neither be ``response.json()``-ed by a browser nor re-parsed
  per request server-side. The writer also streams the gzip incrementally,
  so persisting no longer materialises the whole serialised document.

* ``json.v1`` (legacy rows) — gzip of one compact JSON document
  ``{"nodes":[...],"edges":[...],"meta":{...}}``. Read-compat only; split
  on the fly by ``cortex_viz.shared.json_stream_split``.

One row per scope: within a scope, the newest snapshot full-replaces the
prior (same global-snapshot invalidation model as ``workflow_graph_layout``,
now applied per scope instead of per table). Pure infrastructure —
batch-pool I/O only. Mirrors ``layout_pg_store`` / ``lod_pg_store``'s
``_conn(store)`` idiom.
"""

from __future__ import annotations

import gzip
import io
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

# Additive migration for pre-format rows; existing rows are single-document
# gzip(JSON), which is exactly what the default declares.
_DDL_FORMAT = """
ALTER TABLE workflow_graph_snapshot
    ADD COLUMN IF NOT EXISTS format TEXT NOT NULL DEFAULT 'json.v1';
"""

# D10 (inc5.5): additive scope column. Backfill value is the literal string
# 'default' — pre-D10 rows were written by the old unscoped writer (global
# DELETE + single-row INSERT), so they represent the one-and-only snapshot
# that existed under the old "last write wins" model. 'default' is also the
# column's own DEFAULT, so pre-D10 AND pre-migration writers (see module
# docstring's compat note) land in the same bucket rather than a distinct
# unreachable one.
_DDL_SCOPE = """
ALTER TABLE workflow_graph_snapshot
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'default';
"""

FORMAT_NDJSON_V1 = "ndjson.v1"
FORMAT_JSON_V1 = "json.v1"

# Items per stored NDJSON frame. Nodes carry full detail dicts (~0.5–4 KB
# each), edges are flat 4-field dicts — both land frames in the hundreds of
# KB: small enough for a browser to JSON.parse inside one frame budget,
# large enough that frame overhead is noise. Matches the wire batching the
# live SSE path already uses (graph_event_stream emit chunk=1000).
_NODES_PER_FRAME = 1000
_EDGES_PER_FRAME = 5000


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
    """Idempotently create the snapshot table + format column."""
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute(_DDL_FORMAT)
        cur.execute(_DDL_SCOPE)
        conn.commit()


def _dump_frame(obj: dict) -> bytes:
    """One NDJSON frame. ``default=str`` so non-JSON-native values carried
    on a node/edge/meta (datetime ``computed_at`` timestamps were the first
    offender) degrade to strings rather than aborting the persist."""
    return json.dumps(obj, separators=(",", ":"), default=str).encode() + b"\n"


def _encode_ndjson_gzip(nodes: list, edges: list, meta: dict) -> bytes:
    """Incrementally gzip the framed snapshot (never holds the whole JSON)."""
    sink = io.BytesIO()
    with gzip.GzipFile(fileobj=sink, mode="wb", compresslevel=6) as gz:
        gz.write(
            _dump_frame(
                {"node_total": len(nodes), "edge_total": len(edges), "meta": meta}
            )
        )
        for off in range(0, len(nodes), _NODES_PER_FRAME):
            gz.write(_dump_frame({"nodes": nodes[off : off + _NODES_PER_FRAME]}))
        for off in range(0, len(edges), _EDGES_PER_FRAME):
            gz.write(_dump_frame({"edges": edges[off : off + _EDGES_PER_FRAME]}))
    return sink.getvalue()


def write_snapshot(store, *, fingerprint: str, graph: dict, scope: str) -> dict:
    """Persist ``graph`` as the latest full-graph snapshot for ``scope``.

    Pre: ``graph`` is ``{"nodes": [...], "edges"|"links": [...], "meta": {...}}``
    — the finished build cache; ``scope`` identifies the writing instance
    (``cortex_viz.shared.instance_scope.resolve_instance_scope``, resolved by
    the caller — this module stays a pure persistence boundary and does not
    resolve its own scope). Post: the table holds exactly one row for
    ``scope`` (this snapshot, format ``ndjson.v1``); any PRIOR row for the
    SAME ``scope`` is removed. Rows belonging to other scopes are untouched
    (D10 — this is the fix for the pre-scope "last write wins" contamination
    between instances serving different checkouts). Returns
    ``{"node_count", "edge_count", "bytes"}``.
    """
    _ensure_table(store)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or graph.get("links") or []
    payload = _encode_ndjson_gzip(nodes, edges, graph.get("meta") or {})
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM workflow_graph_snapshot WHERE scope = %s", (scope,))
        cur.execute(
            "INSERT INTO workflow_graph_snapshot "
            "(fingerprint, payload, node_count, edge_count, format, scope) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (fingerprint, payload, len(nodes), len(edges), FORMAT_NDJSON_V1, scope),
        )
        conn.commit()
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "bytes": len(payload),
    }


def read_latest_snapshot(store, *, scope: str) -> dict | None:
    """Return the current snapshot for ``scope``, or None if none exists.

    Pre: ``scope`` identifies the reading instance (same resolution as
    ``write_snapshot``'s ``scope`` — the caller wires both from the same
    source). Result: ``{"fingerprint", "payload_gzip": bytes, "node_count",
    "edge_count", "format"}``. ``payload_gzip`` is the stored bytes exactly
    as written; interpret them per ``format`` (see module docstring).
    """
    _ensure_table(store)
    sql = (
        "SELECT fingerprint, payload, node_count, edge_count, format "
        "FROM workflow_graph_snapshot WHERE scope = %s "
        "ORDER BY created_at DESC LIMIT 1"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (scope,))
        row = cur.fetchone()
    if not row:
        return None
    # The batch pool is configured with dict_row (see pg_store.py).
    return {
        "fingerprint": row["fingerprint"],
        "payload_gzip": bytes(row["payload"]),
        "node_count": int(row["node_count"]),
        "edge_count": int(row["edge_count"]),
        "format": row["format"],
    }
