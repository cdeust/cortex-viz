"""Color palette for the workflow graph — tuned for maximum kind separation.

Re-inked to the AI Architect design-system contract (see
``ui/shared/README.md`` § "Re-inking cortex-specific data families" and its
canonical oklch stage table). This module bakes colours server-side into the
API payload (``node.color``); the canvas/SVG renderers cannot read CSS custom
properties, so these hex values are the *paper*-surface encoding — deep ink
on cream (L46-55%, C0.11-0.16), same hue family as the previous neon-on-black
tuning so the legend stays learnable. KNOWN LIMITATION: because these are
baked at graph-build time, they do not react to the client-side paper/ink
toggle (``CortexSurface``/``surface-toggle.js``) the way CSS-driven chrome
does — flipping to the ink surface will show these paper-deep values against
the dark canvas until this module gains a surface parameter. Tracked as
follow-up; paper is the shipped default so this is the correct baked choice
today.

Each semantic family sits in a distinct hue band so glance-to-legend
identifies which node to click:

* YELLOW/GOLD → Cortex identity + shell (domain, command)
* ORANGE     → user-invoked slash commands (skill)
* PINK/RED   → human-actor interactions (agent, task, discussion)
* PURPLE     → integrations (hook, MCP)
* GREEN      → authorship (edit/write + memory lifecycle)
* CYAN       → information intake (read)
* FUCHSIA    → search (grep/glob)

Pure data. Imports the enum types from ``workflow_graph_schema`` so the
palette can stay keyed by ``ToolKind`` / ``PrimaryToolCluster`` without
a circular dependency (this module is only imported by the schema and
builder — never the other way round).
"""

from __future__ import annotations

from cortex_viz.core.workflow_graph_schema_enums import (
    PrimaryToolCluster,
    ToolKind,
)

DOMAIN_COLOR = "#8D6D00"  # oklch(55% 0.13 92)  — gold hub, paper-deep
SKILL_COLOR = "#9C4800"  # oklch(50% 0.14 56)  — orange, slash commands/skills
COMMAND_COLOR = "#755600"  # oklch(47% 0.13 92) — distinct from Bash-tool orange by lightness
HOOK_COLOR = "#6C399F"  # oklch(46% 0.16 304) — purple, settings hooks
AGENT_COLOR = "#A33069"  # oklch(50% 0.16 354) — pink, subagents
DISCUSSION_COLOR = "#A5292B"  # oklch(48% 0.16 25) — red, session anchors
MCP_COLOR = "#474CA4"  # oklch(46% 0.14 277) — indigo, MCP servers (distinct from hook)
# NOTE: entity node colour is NOT baked here — it never was consumed by any
# builder (grep confirmed zero importers of the former ENTITY_COLOR, removed
# 2026-07-05 as dead code per coding-standards.md §9). Extracted-entity nodes
# are coloured by cortex_viz.core.graph_builder_nodes.ENTITY_COLORS (a
# separate per-entity-type dict, out of this module's scope) or, when the
# server bakes no colour, by the client's KIND_TOKEN.entity → --kind-entity
# (ui/unified/js/workflow_graph.js). That dict's hues were re-targeted to the
# same DEEP band (L50%) as this module's constants 2026-07-05 (G7 fix —
# previously L64-82%, ~1.1-2:1 on the ~79k entity nodes).

# Symbol kinds produced by the ``automatised-pipeline`` AST bridge
# (ADR-0046). Sit inside the Authorship / structural hue range so a
# symbol cluster reads as "code you wrote" rather than something new.
SYMBOL_COLORS: dict[str, str] = {
    "function": "#00738B",  # oklch(50% 0.12 212) — sky, verbs
    # ``method`` gets a lighter sky so method vs free-function is visible
    # at a glance, matching the legend's dedicated entry for methods.
    "method": "#0F7BA7",  # oklch(55% 0.11 233) — sky/blue, methods (receiver-bound functions)
    "class": "#5E41A2",  # oklch(46% 0.15 293) — violet, types (also Rust struct/enum/trait)
    "module": "#8C6000",  # oklch(52% 0.13 84) — amber, boxes/packages/namespaces
    # ``constant`` covers language-level consts, fields, typedefs, type
    # aliases — values rather than behaviour. Intentionally desaturated
    # (slate), per the design system's method note for structural values.
    "constant": "#596475",  # oklch(50% 0.03 257)
    "import": "#596475",  # oklch(50% 0.03 257) — slate, structural, de-emphasised
}
SYMBOL_COLOR_DEFAULT = "#62626C"  # oklch(50% 0.015 286) — zinc fallback, intentionally desaturated

TOOL_HUB_COLORS: dict[ToolKind, str] = {
    ToolKind.EDIT: "#00784F",  # oklch(50% 0.12 163) — emerald
    ToolKind.WRITE: "#00673D",  # oklch(44% 0.13 163) — dark emerald, paired with Edit
    ToolKind.READ: "#00728A",  # oklch(50% 0.11 215) — cyan, far from every green
    ToolKind.GREP: "#8B3C98",  # oklch(50% 0.16 322) — fuchsia
    ToolKind.GLOB: "#7A2984",  # oklch(44% 0.16 323) — deeper fuchsia, paired with Grep
    ToolKind.BASH: "#A04400",  # oklch(50% 0.14 48) — orange, shell band
    ToolKind.TASK: "#A33069",  # oklch(50% 0.16 354) — pink, paired with Agent (same hue by design)
}

PRIMARY_TOOL_COLORS: dict[PrimaryToolCluster, str] = {
    PrimaryToolCluster.EDIT_WRITE: "#00784F",  # oklch(50% 0.12 163) — emerald, files you author
    PrimaryToolCluster.READ: "#00728A",  # oklch(50% 0.11 215) — cyan, files you only read
    PrimaryToolCluster.GREP_GLOB: "#8B3C98",  # oklch(50% 0.16 322) — fuchsia, files you only searched
    PrimaryToolCluster.BASH: "#A04400",  # oklch(50% 0.14 48) — orange, files touched only by shell
}

# Consolidation-stage → node color, mirroring the design system's canonical
# oklch stage table (ui/shared/README.md § "Canonical data tokens", paper
# column) verbatim so the legend, the memory-science rows, and nodes agree
# hue-for-hue. The viz also mirrors this map in ui/brain/js/palette.js
# (STAGE_COLORS) — keep both in sync when the canonical table changes.
MEMORY_STAGE_COLORS: dict[str, str] = {
    "labile": "#006894",  # oklch(48% 0.12 230) — blue
    "early_ltp": "#006A66",  # oklch(46% 0.11 190) — teal
    "late_ltp": "#0A693C",  # oklch(46% 0.11 155) — green
    "consolidated": "#7D6700",  # oklch(52% 0.11 95) — olive/gold
    # reconsolidating: a consolidated memory recalled and reopened — briefly
    # plastic again. No dedicated canonical token exists yet; kept in the
    # cool band between late_ltp and semantic, distinct from both.
    # source: unify pass 2026-07-03; re-inked 2026-07-04.
    "reconsolidating": "#007760",  # oklch(50% 0.11 175)
    # episodic is the default PG stage before LTP promotion — paint it
    # with the same hue as early_ltp so the legend covers every
    # non-semantic memory with no "unexplained" colour.
    "episodic": "#006A66",  # oklch(46% 0.11 190)
    "semantic": "#753E81",  # oklch(46% 0.12 320) — magenta/purple
}


def primary_tool_color(cluster: PrimaryToolCluster) -> str:
    """Return the file color dictated by the primary-tool meta-rule."""
    return PRIMARY_TOOL_COLORS[cluster]


def classify_primary_tool(
    tool_counts: dict[ToolKind, int],
) -> PrimaryToolCluster:
    """Apply the primary-tool meta-rule: Edit/Write > Read > Grep/Glob > Bash."""
    if tool_counts.get(ToolKind.EDIT, 0) or tool_counts.get(ToolKind.WRITE, 0):
        return PrimaryToolCluster.EDIT_WRITE
    if tool_counts.get(ToolKind.READ, 0):
        return PrimaryToolCluster.READ
    if tool_counts.get(ToolKind.GREP, 0) or tool_counts.get(ToolKind.GLOB, 0):
        return PrimaryToolCluster.GREP_GLOB
    return PrimaryToolCluster.BASH
