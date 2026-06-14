"""PostgreSQL persistence for the multi-resolution LOD pyramid.

Reads / writes ``workflow_graph_layout_lod`` — the coarse-band aggregation
that lets a low-zoom tile read ≤64 representatives instead of all N raw rows
(Mandelbrot, 2026-06-14, ``/memories/genius/mandelbrot/cortex-viz-scaling.md``).

Pure infrastructure — no core-state imports except ``core.lod_aggregator``
(the pure single-pass binning function), which this layer COMPOSES with the
raw-position reader. The Dependency Rule is preserved: core declares the
aggregation (no I/O); infrastructure performs the I/O and calls the pure core
function. Mirrors ``layout_pg_store``'s ``_conn(store)`` batch-pool idiom.
"""

from __future__ import annotations

# DDL — self-ensured on first use (CREATE TABLE IF NOT EXISTS). The table is
# fingerprint-keyed: one pyramid per persisted layout topology, full-replaced
# when the topology changes (same invalidation model as workflow_graph_layout).
_DDL = """
CREATE TABLE IF NOT EXISTS workflow_graph_layout_lod (
    topology_fingerprint TEXT NOT NULL,
    level INT NOT NULL,
    cx INT NOT NULL,
    cy INT NOT NULL,
    xbar REAL NOT NULL,
    ybar REAL NOT NULL,
    count INT NOT NULL,
    dom_kind TEXT NOT NULL,
    PRIMARY KEY (topology_fingerprint, level, cx, cy)
);
CREATE INDEX IF NOT EXISTS lod_bbox_idx
    ON workflow_graph_layout_lod (topology_fingerprint, level, xbar, ybar);
"""


def _conn(store):
    """Context-manager accessor on the batch pool. See ``layout_pg_store._conn``.

    Raises ``AttributeError`` when ``store`` has no ``batch_pool`` (LOD
    persistence is PG-only, same as the layout it derives from).
    """
    pool = getattr(store, "batch_pool", None)
    if pool is None:
        raise AttributeError(
            "lod_pg_store requires a store exposing .batch_pool (PgMemoryStore)"
        )
    return pool.connection()


def _ensure_table(store) -> None:
    """Idempotently create the LOD table + bbox index (first-use self-ensure)."""
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


def _has_rows_for(store, fingerprint: str) -> bool:
    """True if the pyramid for ``fingerprint`` is already materialised."""
    sql = (
        "SELECT 1 FROM workflow_graph_layout_lod "
        "WHERE topology_fingerprint = %s LIMIT 1"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (fingerprint,))
        return cur.fetchone() is not None


def build_lod(store, *, fingerprint: str, max_level: int = 7) -> dict:
    """Materialise the LOD pyramid for one layout topology. Idempotent.

    Pre-conditions:
        * ``workflow_graph_layout`` holds the raw ``(node_id, x, y, kind)`` rows
          for ``fingerprint`` (the just-persisted full layout).
        * ``store`` exposes ``batch_pool``.
    Post-conditions:
        * ``workflow_graph_layout_lod`` holds every OCCUPIED cell for levels
          ``0..max_level`` keyed by ``fingerprint``.
        * Stale pyramids (any other fingerprint) are removed — at most one
          fingerprint's pyramid is ever resident (full-replace invalidation,
          mirrors ``write_layout``).
        * Skip-if-fresh: if the pyramid for ``fingerprint`` already exists, this
          returns ``{"status": "ok", "skipped": True}`` without rebuilding.

    Returns a status dict (never the cells themselves).

    Cost: O(N · levels) integer-binning pass (the pure ``lod_aggregator``) +
    bulk insert of the occupied cells (≪ N at coarse levels). Runs in the build
    child after ``write_layout``, hidden behind the DrL pass.
    """
    from cortex_viz.core import lod_aggregator
    from cortex_viz.infrastructure import layout_pg_store

    _ensure_table(store)

    if _has_rows_for(store, fingerprint):
        # Already fresh for this topology — still prune any stale siblings, then
        # return without re-aggregating (idempotent re-entry).
        prune_other_fingerprints(store, keep_fingerprint=fingerprint)
        return {"status": "ok", "skipped": True, "fingerprint": fingerprint}

    # Stream the raw positions and aggregate in a single pass. ``aggregate``
    # consumes any iterable of rows; chaining the chunked generator keeps peak
    # memory at one chunk of raw rows + the (small) occupied-cell dict.
    def _all_rows():
        for chunk in layout_pg_store.iter_positions_chunked(store):
            yield from chunk

    cells = lod_aggregator.aggregate(_all_rows(), max_level=max_level)

    rows = [
        (fingerprint, level, cx, cy, float(xbar), float(ybar), int(count), dom_kind)
        for (level, cx, cy), (xbar, ybar, count, dom_kind) in cells.items()
    ]

    sql_clear = "DELETE FROM workflow_graph_layout_lod WHERE topology_fingerprint = %s"
    sql_ins = (
        "INSERT INTO workflow_graph_layout_lod "
        "(topology_fingerprint, level, cx, cy, xbar, ybar, count, dom_kind) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql_clear, (fingerprint,))
        if rows:
            cur.executemany(sql_ins, rows)
        conn.commit()

    prune_other_fingerprints(store, keep_fingerprint=fingerprint)
    return {
        "status": "ok",
        "skipped": False,
        "fingerprint": fingerprint,
        "cells": len(rows),
        "max_level": max_level,
    }


def read_lod_in_bbox(
    store,
    *,
    fingerprint: str,
    level: int,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
) -> list[tuple[str, float, float, str]]:
    """Return coarse representatives in the bbox at one pyramid ``level``.

    Pre: the pyramid for ``fingerprint`` is materialised. Post: a list of
    ``(node_id, x, y, kind)`` where ``node_id`` is the synthetic
    ``'lod:<level>:<cx>:<cy>'`` (decodable for drill-down), ``x``/``y`` are the
    cell centroid, ``kind`` is the modal kind. The ``lod_bbox_idx`` B-tree on
    ``(fingerprint, level, xbar, ybar)`` keeps this <5 ms — and at coarse levels
    the result is ≤ a constant cell budget regardless of N.
    """
    sql = (
        "SELECT ('lod:' || level || ':' || cx || ':' || cy) AS node_id, "
        "xbar AS x, ybar AS y, dom_kind AS kind "
        "FROM workflow_graph_layout_lod "
        "WHERE topology_fingerprint = %s AND level = %s "
        "AND xbar BETWEEN %s AND %s AND ybar BETWEEN %s AND %s"
    )
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (fingerprint, level, min_x, max_x, min_y, max_y))
        return [
            (r["node_id"], float(r["x"]), float(r["y"]), r["kind"])
            for r in cur.fetchall()
        ]


def prune_other_fingerprints(store, *, keep_fingerprint: str) -> int:
    """Delete every pyramid whose fingerprint != ``keep_fingerprint``.

    Returns the number of rows removed. Keeps the table at one resident
    topology (stale-pyramid invalidation).
    """
    sql = "DELETE FROM workflow_graph_layout_lod WHERE topology_fingerprint != %s"
    with _conn(store) as conn, conn.cursor() as cur:
        cur.execute(sql, (keep_fingerprint,))
        removed = cur.rowcount
        conn.commit()
    return int(removed or 0)
