"""GET /api/tile/{z}/{x}/{y}.png — composition root.

Pulls positions from PG and renders them LIVE via the Datashader-backed tile
renderer. NO tile-output cache: the DATA is multi-resolution (the LOD pyramid),
so every zoom reads ~constant points and rendering stays cheap at any N
(Mandelbrot, 2026-06-14, ``/memories/genius/mandelbrot/cortex-viz-scaling.md``).

Read-path (DECISION 3):
    L = z + 3 (refinement K=3 ⇒ 4^3 = 64 representatives/tile, constant in N).
    if L <= 10 and a layout fingerprint exists: read the COARSE band — the LOD
        pyramid's level-L representatives in the tile bbox (≤64 rows).
    else (z >= 8): read RAW positions in the tile bbox — the tile span is
        ≤ 2/256, so points-per-tile is bounded by local density, not N.
The renderer is UNCHANGED — it takes ``(id, x, y, kind)`` either way.
"""

from __future__ import annotations

import re

# Refinement K (DECISION 1b): cells/tile = 4^K = 64. source: cortex-viz-scaling.md.
_LOD_REFINEMENT = 3
# Pyramid max level == existing tile-pyramid max (z=10). source: same.
_LOD_MAX_LEVEL = 10

_PATH_RE = re.compile(r"^/api/tile/(\d+)/(\d+)/(\d+)\.png$")


def _parse(path: str) -> tuple[int, int, int] | None:
    m = _PATH_RE.match(path)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _read_rows(store, layout_pg_store, lod_pg_store, *, z, bbox):
    """Return the ``(id, x, y, kind)`` rows to render for tile zoom ``z``.

    Pre: ``bbox`` is ``(min_x, min_y, max_x, max_y)`` in world coords. Post:
    when the coarse band applies (``L = z + 3 <= 10`` and a layout fingerprint
    exists) returns ≤ ~64 LOD representatives in the bbox; otherwise returns the
    RAW positions in the bbox (the z>=8 path, viewport-bounded by definition).
    Falls back to RAW if the LOD read yields nothing (pyramid not yet built) so
    a missing pyramid degrades gracefully instead of rendering an empty tile.
    """
    min_x, min_y, max_x, max_y = bbox
    level = z + _LOD_REFINEMENT
    if level <= _LOD_MAX_LEVEL:
        version = layout_pg_store.read_layout_version(store)
        fingerprint = version.get("fingerprint") if version else None
        if fingerprint:
            rows = lod_pg_store.read_lod_in_bbox(
                store,
                fingerprint=fingerprint,
                level=level,
                min_x=min_x,
                min_y=min_y,
                max_x=max_x,
                max_y=max_y,
            )
            if rows:
                return rows
            # Empty LOD read: pyramid absent for this fingerprint. Fall through
            # to RAW so the tile still renders (graceful degradation).
    return layout_pg_store.read_positions_in_bbox(
        store, min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y
    )


def serve(handler, store) -> None:
    parsed = _parse(handler.path.split("?")[0])
    if not parsed:
        handler.send_response(404)
        handler.end_headers()
        return
    z, x, y = parsed

    try:
        from cortex_viz.core import tile_renderer
        from cortex_viz.infrastructure import layout_pg_store, lod_pg_store
    except ImportError as exc:
        # ``viz-tile`` extra not installed.
        body = (
            f'{{"status":"error","reason":"viz_tile_extra_missing","detail":"{exc}"}}'
        ).encode("utf-8")
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return

    min_x, min_y, max_x, max_y = tile_renderer.tile_world_bbox(z, x, y)
    rows = _read_rows(
        store,
        layout_pg_store,
        lod_pg_store,
        z=z,
        bbox=(min_x, min_y, max_x, max_y),
    )
    try:
        png = tile_renderer.render_tile_png(rows, z=z, x=x, y=y)
    except ImportError as exc:
        body = (
            f'{{"status":"error","reason":"datashader_missing","detail":"{exc}"}}'
        ).encode("utf-8")
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Content-Length", str(len(png)))
    handler.send_header("Cache-Control", "max-age=300")
    handler.end_headers()
    handler.wfile.write(png)
