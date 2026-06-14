"""Level-5 leaf node builders for the unified graph builder.

Split out of ``graph_builder_nodes.py`` (was 548 lines) to respect the
500-line file limit. Pure business logic — no I/O. Holds the readable-
pattern filter plus the entry-point / recurring-pattern / tool-preference
/ behavioral-feature leaf builders. The structural levels (root → category
→ domain → agent → type-group) and shared colors/types stay in
``graph_builder_nodes``, which re-exports these for back-compat.
"""

from __future__ import annotations

import re

from cortex_viz.core.graph_builder_nodes import (
    EDGE_COLORS,
    ENTRY_COLOR,
    FEATURE_COLOR,
    PATTERN_COLOR,
    TOOL_COLOR,
    Edge,
    IdAllocator,
    Node,
)


def _is_readable_pattern(pattern: str) -> bool:
    """Filter out nonsensical n-gram patterns (hashes, random word combos)."""
    if not pattern or len(pattern) < 3:
        return False
    parts = [p.strip() for p in pattern.replace("/", " ").split()]
    for p in parts:
        if len(p) > 8 and re.fullmatch(r"[0-9a-f]+", p):
            return False
    stopwords = {
        "json",
        "general",
        "against",
        "through",
        "already",
        "instead",
        "context",
        "updates",
        "meaning",
        "continue",
        "connect",
        "acceptable",
        "violating",
        "interactive",
        "verified",
        "updated",
        "internal",
        "background",
    }
    meaningful = [p for p in parts if len(p) > 2 and p.lower() not in stopwords]
    return len(meaningful) >= 1


def add_entry_points(
    dp: dict,
    domain_key: str,
    parent_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    """Add entry-point leaf nodes linked to a type-group parent."""
    for ep in dp.get("entryPoints") or []:
        pattern = ep.get("pattern", "")
        if not _is_readable_pattern(pattern):
            continue
        label = pattern.replace(" / ", ", ")
        nid = next_id("entry")
        freq = ep.get("frequency") or 0
        nodes.append(
            {
                "id": nid,
                "type": "entry-point",
                "label": label,
                "domain": domain_key,
                "color": ENTRY_COLOR,
                "size": max(3, min(12, (freq or 1) * 1.5)),
                "group": domain_key,
                "confidence": ep.get("confidence") or 0,
                "frequency": freq,
                "content": pattern,
            }
        )
        edges.append(
            {
                "source": parent_id,
                "target": nid,
                "type": "groups",
                "weight": ep.get("confidence") or 0.5,
                "color": EDGE_COLORS["groups"],
            }
        )


def add_recurring_patterns(
    dp: dict,
    domain_key: str,
    parent_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    """Add recurring-pattern leaf nodes linked to a type-group parent."""
    for rp in dp.get("recurringPatterns") or []:
        nid = next_id("pat")
        freq = rp.get("frequency") or 0
        nodes.append(
            {
                "id": nid,
                "type": "recurring-pattern",
                "label": rp.get("pattern", ""),
                "domain": domain_key,
                "color": PATTERN_COLOR,
                "size": max(3, min(12, (freq or 1) * 1.2)),
                "group": domain_key,
                "confidence": rp.get("confidence") or 0,
                "frequency": freq,
                "content": rp.get("pattern", ""),
            }
        )
        edges.append(
            {
                "source": parent_id,
                "target": nid,
                "type": "groups",
                "weight": rp.get("confidence") or 0.5,
                "color": EDGE_COLORS["groups"],
            }
        )


def add_tool_preferences(
    dp: dict,
    domain_key: str,
    parent_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    """Add top-5 tool-preference leaf nodes linked to a type-group parent."""
    tool_prefs = dp.get("toolPreferences") or {}
    top_tools = sorted(
        tool_prefs.items(), key=lambda x: x[1].get("ratio", 0), reverse=True
    )[:5]
    for tool_name, pref in top_tools:
        nid = next_id("tool")
        ratio = pref.get("ratio", 0)
        nodes.append(
            {
                "id": nid,
                "type": "tool-preference",
                "label": tool_name,
                "domain": domain_key,
                "color": TOOL_COLOR,
                "size": max(3, min(10, ratio * 10)),
                "group": domain_key,
                "ratio": ratio,
                "avgPerSession": pref.get("avgPerSession", 0),
                "content": (
                    f"{tool_name} (usage: {ratio:.0%}, "
                    f"avg/session: {pref.get('avgPerSession', 0)})"
                ),
            }
        )
        edges.append(
            {
                "source": parent_id,
                "target": nid,
                "type": "groups",
                "weight": ratio,
                "color": EDGE_COLORS["groups"],
            }
        )


def add_behavioral_features(
    dp: dict,
    domain_key: str,
    parent_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> None:
    """Add behavioral-feature leaf nodes linked to a type-group parent."""
    for feat_label, weight in (dp.get("featureActivations") or {}).items():
        if abs(weight) < 0.05:
            continue
        nid = next_id("feat")
        nodes.append(
            {
                "id": nid,
                "type": "behavioral-feature",
                "label": feat_label,
                "domain": domain_key,
                "color": FEATURE_COLOR,
                "size": max(2, min(8, abs(weight) * 10)),
                "group": domain_key,
                "activation": weight,
                "content": f"{feat_label} (activation: {weight:+.3f})",
            }
        )
        edges.append(
            {
                "source": parent_id,
                "target": nid,
                "type": "groups",
                "weight": abs(weight),
                "color": EDGE_COLORS["groups"],
            }
        )
