"""Shared node/edge types and colour tokens for the graph builders.

Holds the ``Node``/``Edge`` aliases and the colour tokens that more than one
graph builder inks nodes with. Pure declarations -- no I/O, no behaviour.

Consumed by ``graph_builder_discussions`` (``Node``, ``Edge``,
``EDGE_COLORS``), ``workflow_graph_entity`` (``ENTITY_COLORS``) and
``workflow_graph_wiki`` (``WIKI_COLOR``). Tokens used by exactly one builder
live in that builder; the workflow-graph surface has its own palette in
``workflow_graph_palette.py``.
"""

from __future__ import annotations

from typing import Any

# ── Type aliases ─────────────────────────────────────────────────────

Node = dict[str, Any]
Edge = dict[str, Any]

# ── Colors ───────────────────────────────────────────────────────────

# G7 (design gate): interactive points need DEEP paper values (L<=52%,
# >=4.5:1) — the previous set was L64-82%, pale on cream (~1.1-2:1 on the
# ~79k entity nodes these colour, cortex-viz Graph/Trace views). Re-targeted
# to L50% at each entry's original hue (same H, C clamped to 0.10-0.155 —
# the DS-deep convention already used by every other constant in this
# module's sibling ``workflow_graph_palette.py``), computed via the OKLCH
# <-> sRGB round trip (Ottosson, 2020, "A perceptual color space for image
# processing", https://bottosson.github.io/posts/oklab/). Hues stay
# distinct per entity type — only lightness/chroma moved into the deep band.
ENTITY_COLORS = {
    "function": "#007389",  # oklch(50% 0.12 212), was #50D0E8 (L80%)
    "dependency": "#2566A2",  # oklch(50% 0.12 250), was #60A0E0 (L69%)
    "error": "#A43A3E",  # oklch(50% 0.14 21), was #E07070 (L68%)
    "decision": "#7E5F00",  # oklch(50% 0.13 93), was #E0C050 (L82%)
    "technology": "#6654A0",  # oklch(50% 0.12 292), was #9080D0 (L65%)
    "file": "#495FA3",  # oklch(50% 0.11 269), was #7088D0 (L64%)
    "variable": "#007187",  # oklch(50% 0.10 215), was #50B8D0 (L73%)
}

# WIKI_COLOR — wiki-page nodes (documentation surface). Deep indigo,
# distinct hue from every other constant in this module (nearest
# neighbour is ENTITY_COLORS["dependency"] at hue ~250 vs this hue
# ~275) so wiki nodes read as their own visual cluster.
WIKI_COLOR = "#4A3F8A"

EDGE_COLORS = {
    "has-category": "#B0B0B0",
    "has-project": "#8B5CF6",
    "has-agent": "#2DD4BF",
    "has-group": "#64748B",
    "groups": "#50C8E0",
    "bridge": "#FF00FF",
    "persistent-feature": "#ec4899",
    "memory-entity": "#40A0B8",
    "domain-entity": "#50B0C8",
    "has-discussion": "#F43F5E60",
    "domain-contains": "#06b6d4",
    "topic-member": "#06b6d480",
    "co-entity": "#a78bfa",
}
