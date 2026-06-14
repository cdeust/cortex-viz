"""Multi-resolution spatial-binning aggregation (pure, no I/O).

ROOT-CAUSE fix for the flat-z=0-reads-N pathology (Mandelbrot, 2026-06-14,
``/memories/genius/mandelbrot/cortex-viz-scaling.md``): the VIEW is scale-free
but the DATA is not — a low-zoom tile re-aggregates ALL N persisted points.
This module makes the DATA multi-resolution so every zoom reads ~constant
points.

Structure (DECISION 1): a dyadic spatial-binning pyramid over the world
``[-1, 1] × [-1, 1]`` — the SAME decomposition the tile renderer already uses
(``tile_world_bbox`` span = ``2 / 2^z``). Level ``L`` is a grid of ``2^L × 2^L``
cells. One representative per OCCUPIED cell: the centroid (real coords, so the
dot lands where mass is, not at the cell center) plus the modal kind (drives the
datashader ``color_key``) and the count. Occupied-cells-only ⇒ coarse levels are
tiny (``L=3`` ⇒ ≤64 cells total).

Single O(N·levels) pass (DECISION 2): for each row, compute its integer cell
index at every level and accumulate ``sum_x, sum_y, count, Counter(kind)`` per
``(L, cx, cy)``.

No I/O here: the infrastructure layer (``lod_pg_store``) feeds rows in and
persists the emitted cells. Renderer-agnostic — datashader just sees points.
"""

from __future__ import annotations

from collections import Counter
from math import floor
from typing import Iterable

# World extent. Mirrors ``tile_renderer.WORLD_MIN / WORLD_MAX``; kept local so
# this pure module imports nothing from the rendering path.
# source: cortex_viz.core.tile_renderer (same [-1, 1] world convention).
_WORLD_MIN = -1.0
_WORLD_SPAN = 2.0  # WORLD_MAX - WORLD_MIN


def _cell_index(coord: float, level: int) -> int:
    """Integer bin of ``coord`` ∈ [-1, 1] at grid ``level`` (2^level cells).

    Pre: ``level >= 0``. Post: result ∈ ``[0, 2^level - 1]`` (clamped, so
    out-of-world coords from a degenerate layout never index out of range).
    Formula matches the dyadic tile decomposition: a point at world ``x``
    falls in tile ``floor((x + 1) / 2 * 2^level)`` at that level.
    """
    cells = 1 << level
    raw = floor((coord - _WORLD_MIN) / _WORLD_SPAN * cells)
    if raw < 0:
        return 0
    if raw >= cells:
        return cells - 1
    return raw


def aggregate(
    rows: Iterable[tuple[str, float, float, str]],
    max_level: int = 7,
) -> dict[tuple[int, int, int], tuple[float, float, int, str]]:
    """Bin ``(node_id, x, y, kind)`` rows into a multi-resolution pyramid.

    Pre-conditions:
        * ``rows`` yields ``(node_id, x, y, kind)``; ``x, y`` are world coords
          (nominally ∈ [-1, 1]; out-of-range values are clamped, not dropped).
        * ``max_level >= 0``.
    Post-conditions:
        * Returns a dict keyed ``(level, cx, cy)`` for every level
          ``L ∈ 0..max_level`` and every OCCUPIED cell at that level.
        * Each value is ``(xbar, ybar, count, dom_kind)`` where ``xbar``/``ybar``
          are the centroid (mean) of the rows in the cell, ``count`` is the
          number of rows, and ``dom_kind`` is the modal ``kind`` in the cell.
        * For every level ``L``, ``sum(count over cells at L) == len(rows)`` —
          binning partitions the rows, losing none (the conservation invariant).
    Complexity: single O(N · (max_level + 1)) pass over ``rows``; memory bounded
        by the number of occupied cells across levels (≪ N at coarse levels —
        the whole point).

    The ``node_id`` column is intentionally unused: a cell's representative gets
    a synthetic id (``lod:L:cx:cy``) assigned at read time, so we keep only the
    spatial + kind aggregates here.
    """
    if max_level < 0:
        raise ValueError(f"max_level must be >= 0, got {max_level}")

    # Accumulators keyed (level, cx, cy). Kept as parallel running sums plus a
    # per-cell kind Counter; finalised to centroids + modal kind after the pass.
    sum_x: dict[tuple[int, int, int], float] = {}
    sum_y: dict[tuple[int, int, int], float] = {}
    count: dict[tuple[int, int, int], int] = {}
    kinds: dict[tuple[int, int, int], Counter] = {}

    levels = range(max_level + 1)
    for _node_id, x, y, kind in rows:
        fx = float(x)
        fy = float(y)
        # invariant per iteration: this row is added to exactly one cell at each
        # level, so every level's total count grows by 1 (conservation).
        for level in levels:
            cx = _cell_index(fx, level)
            cy = _cell_index(fy, level)
            key = (level, cx, cy)
            if key in count:
                sum_x[key] += fx
                sum_y[key] += fy
                count[key] += 1
                kinds[key][kind] += 1
            else:
                sum_x[key] = fx
                sum_y[key] = fy
                count[key] = 1
                kinds[key] = Counter({kind: 1})

    out: dict[tuple[int, int, int], tuple[float, float, int, str]] = {}
    for key, n in count.items():
        xbar = sum_x[key] / n
        ybar = sum_y[key] / n
        # Modal kind. ``Counter.most_common(1)`` is deterministic for ties by
        # insertion order; acceptable — colour of a tied coarse dot is cosmetic.
        dom_kind = kinds[key].most_common(1)[0][0]
        out[key] = (xbar, ybar, n, dom_kind)
    return out
