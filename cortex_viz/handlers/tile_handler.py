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
import sys

from cortex_viz.server.http_standalone_response import (
    send_json_capability_unavailable,
)

# Refinement K (DECISION 1b): cells/tile = 4^K = 64. source: cortex-viz-scaling.md.
_LOD_REFINEMENT = 3
# Pyramid max level == existing tile-pyramid max (z=10). source: same.
_LOD_MAX_LEVEL = 10
# Raw-render budget: render the ACTUAL points via datashader (its purpose
# is rasterizing dense point clouds) up to this many points per tile; only
# above it does the LOD coarse band kick in, so the overview stays a dense
# field instead of ~64 representative dots. source: measured 2026-06-14 —
# 64,658 rows read from workflow_graph_layout in 35 ms (psql, local PG);
# datashader aggregation of that set ≈ 30-50 ms. Linear extrapolation:
# 250,000 rows ≈ 135 ms read + ~50 ms render < the 200 ms tile budget.
_RAW_RENDER_MAX = 250_000

_PATH_RE = re.compile(r"^/api/tile/(\d+)/(\d+)/(\d+)\.png$")


def _parse(path: str) -> tuple[int, int, int] | None:
    m = _PATH_RE.match(path)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _read_rows(store, layout_pg_store, lod_pg_store, *, z, bbox):
    """Return the ``(id, x, y, kind)`` rows to render for tile zoom ``z``.

    Pre: ``bbox`` is ``(min_x, min_y, max_x, max_y)`` in world coords. Post:
    renders the ACTUAL points via a raw bbox read whenever the tile holds
    ≤ ``_RAW_RENDER_MAX`` of them — datashader rasterizes a dense cloud, which
    is the whole point of a datashader tile pyramid and keeps the overview a
    real field rather than ~64 LOD dots. Only a genuinely over-dense tile
    (raw count > the budget) falls back to the LOD coarse band, which is
    bounded in N. The decision is made with a single bounded PROBE read
    (``LIMIT _RAW_RENDER_MAX + 1``) so a pathological bbox never triggers a
    full multi-million-row read.

    Why raw-first (was LOD-first): the LOD band returns ~64 representatives
    per tile, so the z=0 overview painted ~64 scattered dots instead of the
    point cloud. datashader is designed to aggregate large point sets into a
    fixed raster; at real corpus scale (tens of thousands of nodes) the raw
    read is ~35 ms (measured). source: cortex-viz-scaling.md + 2026-06-14
    measurement noted on ``_RAW_RENDER_MAX``.
    """
    min_x, min_y, max_x, max_y = bbox
    raw = layout_pg_store.read_positions_in_bbox(
        store,
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        limit=_RAW_RENDER_MAX + 1,
    )
    if len(raw) <= _RAW_RENDER_MAX:
        # Common case: the tile fits the raw-render budget — datashader the
        # real points into a dense field.
        return raw

    # Over-dense tile: fall back to the LOD coarse band (≤ ~64 reps/tile),
    # bounded in N. Requires the pyramid for the live layout fingerprint.
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
    # Pyramid absent or level out of range: render the bounded probe set
    # rather than nothing (graceful degradation — still a populated tile).
    return raw


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
        # NOT the ``viz-tile`` guard, despite appearances: none of the
        # three modules above imports datashader/pandas/pyarrow at module
        # level, so an absent extra sails through here and is caught by
        # the second guard below, on the renderer's lazy import (verified
        # 2026-08-02, #88 — that is also why the second guard had to be
        # added). What this one genuinely catches is a broken or partial
        # install of cortex_viz itself. The answer is the same either way
        # — "this install cannot do that", not an error (#90) — so the
        # guard stays, and both are now covered by
        # tests/test_optional_extra_absent.py.
        print(
            f"[cortex] /api/tile unavailable — viz-tile extra absent: {exc}",
            file=sys.stderr,
        )
        send_json_capability_unavailable(
            handler,
            capability="viz-tile",
            reason="extra_not_installed",
            fallback="/api/graph",
        )
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
        # Second entry to the same absent extra: the module imports above
        # can succeed while datashader/pandas — imported lazily inside the
        # renderer — are missing. Same answer as the import guard, or the
        # defect just moves one line down (#90).
        print(
            f"[cortex] /api/tile unavailable — viz-tile extra absent: {exc}",
            file=sys.stderr,
        )
        send_json_capability_unavailable(
            handler,
            capability="viz-tile",
            reason="extra_not_installed",
            fallback="/api/graph",
        )
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "image/png")
    handler.send_header("Content-Length", str(len(png)))
    handler.send_header("Cache-Control", "max-age=300")
    handler.end_headers()
    handler.wfile.write(png)
