"""PostgreSQL persistence for the complete workflow-graph SNAPSHOT.

A durable, build-independent copy of the fully assembled graph — every node
AND edge (backbone, memories, AST symbols, entities). It exists so the
full-graph endpoints can be served instantly from PG without depending on
the volatile in-process build cache or re-running the multi-hour build: the
cache loops/empties between builds, but a persisted snapshot is stable.

Written once at build completion (where the in-process cache holds the
finished graph); read by ``serve_graph_full`` / ``serve_graph_full_stream``.

Scoping (D10, inc5.5) + table isolation (D11, inc5.5b): every viz server
instance, on every checkout, needs its own snapshot row so two instances
serving different checkouts of this repo never overwrite or read each
other's graph. inc5.5's ``scope`` column closed this for same-version
deployments but left two risks open when an OLD (pre-D10) binary runs
concurrently against the SAME shared table: (1) the old binary's unscoped
``DELETE FROM workflow_graph_snapshot`` (no WHERE) still wipes every
scope's row, not just its own; (2) the primary key was ``fingerprint``
alone, so a fingerprint collision between two scopes would fail the
``INSERT``. inc5.5b closes both BY CONSTRUCTION rather than by procedure:
current (D11+) code reads and writes a DEDICATED table,
``workflow_graph_snapshot_scoped``, whose name the old binary does not
know and therefore cannot reach — its unscoped ``DELETE``/``INSERT``/
``SELECT`` still run, but only against the legacy ``workflow_graph_snapshot``
table, which current code no longer writes to. The new table's primary key
is the composite ``(scope, fingerprint)``, so a fingerprint collision
across two scopes is not a key collision — both rows coexist. ``scope`` is
resolved by ``cortex_viz.shared.instance_scope.resolve_instance_scope``;
both ``write_snapshot`` and ``read_latest_snapshot`` take it as a
caller-supplied argument (composition-root wiring, not resolved inside
this infra module).

Legacy-table migration (one-time, idempotent, additive): ``_ensure_table``
still issues ``CREATE TABLE IF NOT EXISTS`` for the legacy
``workflow_graph_snapshot`` table (so an old binary that later starts up
still has a table to write into — unaffected by this change) and, only
when the new table is still empty, copies the legacy table's single most
recent row into ``workflow_graph_snapshot_scoped`` under
``scope='default'`` — the historical "last write wins" snapshot becomes
that checkout's starting point instead of forcing every existing
deployment to pay for a from-scratch rebuild (multi-hour: see the module
docstring's ``ndjson.v1`` note). The legacy table itself is never written
to by current code again and is not dropped in this increment; its
decommission is a future increment once no pre-D11 binary is expected to
run against this database anymore.

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

# Legacy table (pre-D11). Current code never writes to it again — it is
# created here ONLY so an old (pre-D11) binary that starts up later still
# has a table, and so the one-time migration SELECT below always has a
# valid (possibly empty) source. Kept verbatim (same DDL as inc5.5) for an
# old binary's continued operation; not touched by write_snapshot/
# read_latest_snapshot below. Decommission is a future increment.
_LEGACY_TABLE = "workflow_graph_snapshot"

_DDL_LEGACY = """
CREATE TABLE IF NOT EXISTS workflow_graph_snapshot (
    fingerprint TEXT PRIMARY KEY,
    payload BYTEA NOT NULL,
    node_count INT NOT NULL,
    edge_count INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_DDL_LEGACY_FORMAT = """
ALTER TABLE workflow_graph_snapshot
    ADD COLUMN IF NOT EXISTS format TEXT NOT NULL DEFAULT 'json.v1';
"""

_DDL_LEGACY_SCOPE = """
ALTER TABLE workflow_graph_snapshot
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'default';
"""

# D11 (inc5.5b): the dedicated, scope-isolated table. An old binary does not
# know this name and therefore cannot reach it — this is what closes both
# residual risks BY CONSTRUCTION (see module docstring). Primary key is the
# composite (scope, fingerprint): a fingerprint collision between two scopes
# is not a key collision, both rows coexist.
_TABLE = "workflow_graph_snapshot_scoped"

_DDL_SCOPED = """
CREATE TABLE IF NOT EXISTS workflow_graph_snapshot_scoped (
    scope TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload BYTEA NOT NULL,
    node_count INT NOT NULL,
    edge_count INT NOT NULL,
    format TEXT NOT NULL DEFAULT 'json.v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, fingerprint)
);
"""

# One-time, idempotent backfill: only fires while the new table is still
# empty (``WHERE NOT EXISTS (SELECT 1 FROM workflow_graph_snapshot_scoped)``)
# — once any scope has written a row, this INSERT selects nothing on every
# subsequent call, so running it on every _ensure_table is cheap and safe.
# Picks the legacy table's single most recent row (its "last write wins"
# model kept at most one) and labels it scope='default', so an existing
# deployment's cache survives the table split instead of an expensive
# rebuild (see module docstring).
_DDL_MIGRATE_LEGACY = """
INSERT INTO workflow_graph_snapshot_scoped
    (scope, fingerprint, payload, node_count, edge_count, format, created_at)
SELECT 'default', fingerprint, payload, node_count, edge_count, format, created_at
FROM workflow_graph_snapshot
WHERE NOT EXISTS (SELECT 1 FROM workflow_graph_snapshot_scoped)
ORDER BY created_at DESC
LIMIT 1;
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
    """Idempotently create the scoped table (+ the legacy table, for an old
    binary's continued operation) and run the one-time legacy backfill."""
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(_DDL_LEGACY)
        cur.execute(_DDL_LEGACY_FORMAT)
        cur.execute(_DDL_LEGACY_SCOPE)
        cur.execute(_DDL_SCOPED)
        cur.execute(_DDL_MIGRATE_LEGACY)
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
    resolve its own scope). Post: the dedicated table
    (``workflow_graph_snapshot_scoped``) holds exactly one row for ``scope``
    (this snapshot, format ``ndjson.v1``); any PRIOR row for the SAME
    ``scope`` is removed. Rows belonging to other scopes are untouched —
    both because the ``DELETE`` is scope-filtered (D10) and because this
    table's primary key is the composite ``(scope, fingerprint)`` (D11), so
    a fingerprint collision with another scope's row cannot block the
    ``INSERT``. An old (pre-D11) binary cannot reach this table at all — it
    only knows the legacy table's name (D11 — see module docstring).
    Returns ``{"node_count", "edge_count", "bytes"}``.
    """
    _ensure_table(store)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or graph.get("links") or []
    payload = _encode_ndjson_gzip(nodes, edges, graph.get("meta") or {})
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM workflow_graph_snapshot_scoped WHERE scope = %s", (scope,)
        )
        cur.execute(
            "INSERT INTO workflow_graph_snapshot_scoped "
            "(scope, fingerprint, payload, node_count, edge_count, format) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (scope, fingerprint, payload, len(nodes), len(edges), FORMAT_NDJSON_V1),
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
    as written; interpret them per ``format`` (see module docstring). Reads
    the dedicated table (``workflow_graph_snapshot_scoped``) — an old
    (pre-D11) reader cannot reach it (D11, see module docstring).
    """
    _ensure_table(store)
    sql = (
        "SELECT fingerprint, payload, node_count, edge_count, format "
        "FROM workflow_graph_snapshot_scoped WHERE scope = %s "
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
