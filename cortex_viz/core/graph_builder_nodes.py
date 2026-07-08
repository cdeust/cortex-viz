"""Node construction helpers for the unified graph builder.

Builds a 6-level hierarchy: root → category → project → agent → type-group → leaf.
Each function appends nodes (and direct parent-child edges) for one level.
Pure business logic -- no I/O.

Memory and entity node builders live in graph_builder_memory.py.
"""

from __future__ import annotations

from typing import Any

# ── Type aliases ─────────────────────────────────────────────────────

Node = dict[str, Any]
Edge = dict[str, Any]
IdAllocator = Any  # callable(str) -> str

# ── Colors ───────────────────────────────────────────────────────────

ROOT_COLOR = "#FFFFFF"
CATEGORY_COLOR = "#8B5CF6"
DOMAIN_COLOR = "#E8B840"
AGENT_COLOR = "#2DD4BF"
TYPE_GROUP_COLOR = "#64748B"
ENTRY_COLOR = "#60D8F0"
PATTERN_COLOR = "#70D880"
TOOL_COLOR = "#E0A840"
FEATURE_COLOR = "#B088E0"
MEMORY_COLORS = {"episodic": "#58D888", "semantic": "#C070D0"}
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

DISCUSSION_COLOR = "#F43F5E"
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

# ── Technology category classification ───────────────────────────────

_TECH_KEYWORDS: dict[str, set[str]] = {
    "Backend": {
        "api",
        "database",
        "server",
        "fastapi",
        "postgresql",
        "auth",
        "migration",
        "backend",
        "endpoint",
        "middleware",
        "sql",
        "redis",
        "graphql",
        "rest",
        "microservice",
        "celery",
        "django",
        "flask",
    },
    "Frontend": {
        "react",
        "typescript",
        "component",
        "ui",
        "android",
        "css",
        "rendering",
        "frontend",
        "html",
        "vue",
        "angular",
        "swift",
        "ios",
        "mobile",
        "widget",
        "layout",
        "animation",
        "navigation",
    },
    "AI/Research": {
        "prd",
        "metaprompting",
        "orchestration",
        "research",
        "prompting",
        "rag",
        "strategy",
        "llm",
        "model",
        "embedding",
        "benchmark",
        "evaluation",
        "thinking",
        "cognitive",
        "neural",
        "memory",
        "thermodynamic",
        "cortex",
        "methodology",
    },
    "DevOps": {
        "deploy",
        "docker",
        "ci",
        "pipeline",
        "git",
        "homebrew",
        "compiler",
        "build",
        "infrastructure",
        "terraform",
        "kubernetes",
        "monitoring",
        "logging",
        "container",
        "certificate",
    },
}


def classify_tech_category(dp: dict) -> str:
    """Classify a domain profile into a technology category.

    Uses topKeywords and tool names for signal. Returns the best-matching
    category or 'General' as fallback.
    """
    keywords = {k.lower() for k in (dp.get("topKeywords") or [])}
    # Add tool names as signal
    for tool_name in dp.get("toolPreferences") or {}:
        keywords.add(tool_name.lower())

    best_cat = "General"
    best_score = 0
    for cat, cat_keywords in _TECH_KEYWORDS.items():
        score = len(keywords & cat_keywords)
        if score > best_score:
            best_score = score
            best_cat = cat
    return best_cat


# ── Level 0: Root node ───────────────────────────────────────────────


def add_root_node(
    next_id: IdAllocator,
    nodes: list[Node],
) -> str:
    """Create the single root node. Returns its id."""
    nid = next_id("root")
    nodes.append(
        {
            "id": nid,
            "type": "root",
            "label": "Cortex",
            "domain": "",
            "color": ROOT_COLOR,
            "size": 30,
            "group": "_root",
            "content": "Cortex — cognitive profiling & persistent memory",
        }
    )
    return nid


# ── Level 1: Category nodes ─────────────────────────────────────────


def add_category_node(
    name: str,
    root_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> str:
    """Create a technology category node linked to root. Returns its id."""
    nid = next_id("cat")
    nodes.append(
        {
            "id": nid,
            "type": "category",
            "label": name,
            "domain": "",
            "color": CATEGORY_COLOR,
            "size": 12,
            "group": "_categories",
            "content": f"Technology category: {name}",
        }
    )
    edges.append(
        {
            "source": root_id,
            "target": nid,
            "type": "has-category",
            "weight": 0.8,
            "color": EDGE_COLORS["has-category"],
        }
    )
    return nid


# ── Level 2: Project (domain) nodes ─────────────────────────────────


def add_domain_hub(
    dp: dict,
    domain_key: str,
    category_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> str:
    """Create a project/domain hub node linked to its category. Returns its id."""
    hub_id = next_id("dom")
    session_count = dp.get("sessionCount") or 0
    nodes.append(
        {
            "id": hub_id,
            "type": "domain",
            "label": dp.get("label") or domain_key,
            "domain": domain_key,
            "color": DOMAIN_COLOR,
            "size": max(6, min(25, (session_count or 1) ** 0.5 * 2)),
            "group": domain_key,
            "sessionCount": session_count,
            "confidence": dp.get("confidence") or 0,
            "content": (
                f"{dp.get('label') or domain_key} — "
                f"{session_count} sessions, confidence {(dp.get('confidence') or 0):.0%}"
            ),
        }
    )
    edges.append(
        {
            "source": category_id,
            "target": hub_id,
            "type": "has-project",
            "weight": 0.7,
            "color": EDGE_COLORS["has-project"],
        }
    )
    return hub_id


# ── Level 3: Agent nodes ────────────────────────────────────────────


def add_agent_node(
    agent: dict,
    domain_key: str,
    hub_id: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
) -> str:
    """Create an agent node linked to its project. Returns its id."""
    nid = next_id("agent")
    nodes.append(
        {
            "id": nid,
            "type": "agent",
            "label": agent["name"],
            "domain": domain_key,
            "color": AGENT_COLOR,
            "size": 6,
            "group": domain_key,
            "content": agent.get("description", agent["name"]),
            "toolCount": len(agent.get("tools", [])),
        }
    )
    edges.append(
        {
            "source": hub_id,
            "target": nid,
            "type": "has-agent",
            "weight": 0.6,
            "color": EDGE_COLORS["has-agent"],
        }
    )
    return nid


# ── Level 4: Type-group nodes ───────────────────────────────────────

TYPE_GROUP_LABELS = [
    "Entry Points",
    "Patterns",
    "Tools",
    "Features",
    "Memories",
    "Discussions",
]


def add_type_group_nodes(
    parent_id: str,
    domain_key: str,
    next_id: IdAllocator,
    nodes: list[Node],
    edges: list[Edge],
    labels: list[str] | None = None,
) -> dict[str, str]:
    """Create type-group nodes under a parent (agent or hub). Returns {label: nid}."""
    labels = labels or TYPE_GROUP_LABELS
    result: dict[str, str] = {}
    for label in labels:
        nid = next_id("tg")
        nodes.append(
            {
                "id": nid,
                "type": "type-group",
                "label": label,
                "domain": domain_key,
                "color": TYPE_GROUP_COLOR,
                "size": 3,
                "group": domain_key,
                "content": f"{label} for {domain_key}",
            }
        )
        edges.append(
            {
                "source": parent_id,
                "target": nid,
                "type": "has-group",
                "weight": 0.5,
                "color": EDGE_COLORS["has-group"],
            }
        )
        result[label] = nid
    return result

# ── Level 5: Leaf node builders ──────────────────────────────────────
# Split into ``graph_builder_nodes_leaves`` (500-line limit). Re-exported
# here so historical ``from cortex_viz.core.graph_builder_nodes import
# add_entry_points`` (etc.) keeps resolving.
from cortex_viz.core.graph_builder_nodes_leaves import (  # noqa: E402, F401
    _is_readable_pattern,
    add_behavioral_features,
    add_entry_points,
    add_recurring_patterns,
    add_tool_preferences,
)
