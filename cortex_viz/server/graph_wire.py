"""Pure slim-wire projection helpers for the graph stream.

Extracted verbatim from ``http_standalone_graph.py``. No state, no I/O —
pure functions over node dicts. Depends on nothing in the server package.

# ── Slim wire projection (graphify-informed, 2026-06-12) ──────────────
#
# The SSE stream used to ship every node's FULL record — measured
# 259 bytes/item, 107 MB for the complete galaxy replay (414k items).
# The renderer only consumes: id, kind, domain_id, x, y, label, color,
# heat, extra_domain_ids (verified consumer audit: workflow_graph.js
# nodeColor/labelOf fall back to palette/id; filters read domain_id +
# extra_domain_ids; memory/entity weighting reads heat). Everything
# else — path, symbol_type, memory metadata, edge confidence/reason —
# is detail-panel data served on demand by /api/graph/node.
#
# Plain JSON positional arrays, deliberately NO enum tables and NO
# index↔id mapping layer (user direction: light JSON without a mapper
# — the codec class of solution is what kept breaking). Fixed layout:
#   node: [id, kind, domain_id, x, y, label, color, heat, extra_ids]
#   edge: [source, target, kind, weight]
# Absent values are null; the client decoder skips nulls so the
# renderer's existing fallbacks engage.
"""

from __future__ import annotations

import math

from cortex_viz.shared.hash import simple_hash


def _round4(v):
    """Coordinates ride the wire at 4 decimals. The DrL layout emits
    unit-scale doubles (observed 0.6026883210462267 — 18 chars); 1e-4
    resolution is sub-pixel even on a 4k-wide render of the unit
    square, and the rounding alone removes ~2 MB from the full replay
    (measured 2026-06-12: 45,871 baked coordinate pairs)."""
    return round(v, 4) if isinstance(v, float) else v


def _slim_node(n: dict) -> list:
    """THE wire record: id, kind, x, y — nothing else. No labels, no
    colors, no domain ids, no metadata, and the stream carries NO
    edges at all (user direction 2026-06-12: the planetarium renders
    every dot from id+position alone; every other byte — neighbors,
    labels, details — is a query through the on-demand endpoints and
    MCP tools, fetched only when asked)."""
    return [
        n.get("id"),
        n.get("kind") or n.get("type"),
        _round4(n.get("x")),
        _round4(n.get("y")),
    ]


def _place_around(anchor_x: float, anchor_y: float, key: str) -> tuple[float, float]:
    """Deterministic position near an anchor, in the layout engine's
    [-1, 1] world coordinates.

    Gives L6 symbols (and AP-only files) server-side coordinates so the
    wire carries a position for EVERY node and the client never has to
    force-simulate them — the plan is "server positions, client draws".
    The DrL bake covers only the baseline; symbols are placed on a
    deterministic ray around their parent file's baked coordinate.

    Distance derivation (no invented constants): the client renderer
    seeded symbols 30–150 px past their file on a ~1200 px viewport
    (workflow_graph.js symbol seeding), i.e. 2.5–12.5 % of the view.
    The world span is 2.0 ([-1,1]), so the same visual ratio is
    0.05–0.25 world units. Angle and distance both derive from the
    DJB2 hash of the node id — same input, same position, every build.
    """
    h = int(simple_hash(key), 16)
    angle = (h % 3600) / 3600.0 * 2.0 * math.pi
    dist = 0.05 + ((h >> 12) % 1000) / 1000.0 * 0.20
    return (
        round(anchor_x + math.cos(angle) * dist, 4),
        round(anchor_y + math.sin(angle) * dist, 4),
    )
