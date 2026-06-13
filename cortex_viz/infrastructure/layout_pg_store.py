"""PostgreSQL persistence for precomputed graph-layout coordinates.

Reads / writes ``workflow_graph_layout`` (defined in pg_schema.py).
Pure infrastructure — no core imports. The handler layer composes this
with ``core.layout_engine`` to produce + persist coords.
"""

from __future__ import annotations

import time
from typing import Iterable


def _conn(store):
    """Context-manager accessor on ``PgMemoryStore``.

    The PG store exposes a ``batch_pool`` (psycopg_pool ConnectionPool)
    via the property declared in pg_store.py. We use the batch pool —
    layout reads/writes are bulk, not interactive — and isolate the
    pool name here so the rest of this module never touches psycopg
    directly.

    Raises:
        AttributeError: when called on a SQLite-backed store. Layout
            persistence is PG-only by design (the BIGINT column type,
            TIMESTAMPTZ default, and bulk executemany pattern all
            assume PG).
    """
    pool = getattr(store, "batch_pool", None)
    if pool is None:
        raise AttributeError(
            "layout_pg_store requires PgMemoryStore (no .batch_pool on this store)"
        )
    return pool.connection()


def write_layout(
    store,
    coords: Iterable[tuple[str, float, float]],
    kinds: dict[str, str],
    *,
    topology_fingerprint: str,
) -> int:
    """Persist ``(node_id, x, y, kind)`` rows. Returns layout_version.

    ``layout_version`` is monotonically increasing wall-clock-millis;
    we use it as the cache key the tile + quadtree endpoints invalidate
    on. Bulk-inserted via ``executemany`` for speed (well under 1s for
    1M rows on local PG).

    The write is fully replacing — every prior row is removed before
    the new set lands. This is correct because the layout is a global
    snapshot, not an incremental update.
    """
    layout_version = int(time.time() * 1000)
    rows = [
        (
            nid,
            float(x),
            float(y),
            kinds.get(nid, "unknown"),
            topology_fingerprint,
            layout_version,
        )
        for nid, x, y in coords
    ]
    if not rows:
        return layout_version
    sql_clear = "DELETE FROM workflow_graph_layout"
    sql_ins = (
        "INSERT INTO workflow_graph_layout "
        "(node_id, x, y, kind, topology_fingerprint, layout_version) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql_clear)
        cur.executemany(sql_ins, rows)
        conn.commit()
    return layout_version


def upsert_positions(
    store,
    rows: Iterable[tuple[str, float, float, str]],
    *,
    layout_version: int,
    topology_fingerprint: str = "stream",
) -> int:
    """Incrementally UPSERT ``(node_id, x, y, kind)`` positions — no DELETE.

    The progressive-warm-up counterpart to ``write_layout``: positions
    land per batch AS the build streams (the LayoutAuthority assigns each
    node a deterministic slot the moment it arrives), so the quadtree /
    tile endpoints render the graph filling in instead of waiting for the
    whole igraph layout to finish. ``node_id`` is the PK, so repeated
    nodes update in place. Callers pass a single stable ``layout_version``
    for the duration of one build so the client can poll for growth.

    Returns the number of rows written.
    """
    payload = [
        (nid, float(x), float(y), kind, topology_fingerprint, layout_version)
        for nid, x, y, kind in rows
    ]
    if not payload:
        return 0
    sql = (
        "INSERT INTO workflow_graph_layout "
        "(node_id, x, y, kind, topology_fingerprint, layout_version) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (node_id) DO UPDATE SET "
        "x = EXCLUDED.x, y = EXCLUDED.y, kind = EXCLUDED.kind, "
        "topology_fingerprint = EXCLUDED.topology_fingerprint, "
        "layout_version = EXCLUDED.layout_version, computed_at = NOW()"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.executemany(sql, payload)
        conn.commit()
    return len(payload)


def read_layout_version(store) -> dict | None:
    """Return ``{'version', 'fingerprint', 'count'}`` or None if empty."""
    sql = (
        "SELECT layout_version AS v, topology_fingerprint AS fp, "
        "COUNT(*) AS n "
        "FROM workflow_graph_layout "
        "GROUP BY layout_version, topology_fingerprint "
        "ORDER BY layout_version DESC LIMIT 1"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    # The pool is configured with ``dict_row`` (see pg_store.py), so
    # ``row`` is a dict keyed on the SELECT aliases. Tuple-indexing
    # would raise KeyError(0). The aliases above pin stable keys.
    return {"version": int(row["v"]), "fingerprint": row["fp"], "count": int(row["n"])}


def iter_positions_chunked(store, chunk_size: int = 50_000):
    """Stream ``(node_id, x, y, kind)`` rows in keyset-paged chunks.

    The quadtree endpoint ships every node's picking coords. Materializing
    them as four Python lists at 1M+ nodes spikes RAM on the high-cardinality
    ``node_id`` strings; streaming keeps peak at one chunk while the endpoint
    writes Arrow record-batches incrementally. Keyset paging on the ``node_id``
    PK (``WHERE node_id > last``) avoids a full sort and is drift-safe.

    Yields ``list[tuple[str, float, float, str]]`` per page.
    """
    sql = (
        "SELECT node_id, x, y, kind FROM workflow_graph_layout "
        "WHERE node_id > %s ORDER BY node_id LIMIT %s"
    )
    last = ""
    page = int(chunk_size)
    while True:
        with _conn(store) as conn, conn.cursor() as cur:
            cur.execute(sql, (last, page))
            rows = cur.fetchall()
        if not rows:
            return
        yield [(r["node_id"], float(r["x"]), float(r["y"]), r["kind"]) for r in rows]
        last = rows[-1]["node_id"]
        if len(rows) < page:
            return


def read_positions_in_bbox(
    store,
    *,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> list[tuple[str, float, float, str]]:
    """Return positions intersecting the world-space bbox.

    Used by the tile renderer: each tile request asks PG for only the
    nodes whose coordinates fall inside the tile's world-space cell.
    The B-tree on (x, y) (see ``INDEXES_DDL`` in pg_schema.py) keeps
    this query under 5 ms even for 10M-row tables.
    """
    sql = (
        "SELECT node_id, x, y, kind FROM workflow_graph_layout "
        "WHERE x BETWEEN %s AND %s AND y BETWEEN %s AND %s"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (min_x, max_x, min_y, max_y))
        return [
            (r["node_id"], float(r["x"]), float(r["y"]), r["kind"])
            for r in cur.fetchall()
        ]
