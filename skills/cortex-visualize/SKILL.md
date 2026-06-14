---
name: cortex-visualize
description: "Launch the interactive Cortex neural-graph visualization (the cortex-viz MCP). Use when the user says 'show visualization', 'show me the graph', 'visualize memories', 'show memory map', 'open neural graph', 'cortex-visualize', or when a visual overview of the memory system, codebase graph, or session trace would help."
---

# Visualize — Interactive Neural Graph (cortex-viz)

## Keywords
visualize, graph, neural graph, memory map, show memories, visual overview, entity graph, methodology graph, codebase graph, session trace, show profile, galaxy

## Overview

Launch the interactive browser-based neural graph served by the **cortex-viz**
MCP. It reads Cortex's shared PostgreSQL store (read-only) plus your `~/.claude`
session history and wiki, and opens six reading angles over the same data:
**Trace** (default), **Graph** (the galaxy), **Knowledge**, **Wiki**, **Board**,
and **Pipeline**.

**Use this skill when:** the user wants a visual overview, is exploring the
knowledge/codebase graph, or needs to present or screenshot Cortex's state.

**Requires:** the `cortex-viz` MCP installed alongside Cortex. If its tools are
not available, tell the user to install cortex-viz
(https://github.com/cdeust/cortex-viz).

## Workflow

### Launch the neural graph

```
cortex-viz:open_visualization({})
```

Or filter to a specific domain:

```
cortex-viz:open_visualization({ "domain": "cortex" })
```

Opens in the browser on a local 127.0.0.1 port. Features:
- **Trace** — the live execution drill: domain → session → ordered
  prompt → action → file chain → a file's AST symbols, impact neighbourhood,
  and git history.
- **Graph** — the galaxy: every project as a node cloud (setup → tools →
  files → discussions → memories → AST symbols), with cross-project shared-code
  and impact edges.
- **Knowledge / Board / Wiki / Pipeline** — memory cards, the consolidation
  kanban, the browsable wiki, and the domain→stage Sankey.
- **Interactive** — click any node for a full detail panel, drag to explore,
  scroll to zoom; filter by kind / domain / AST edge.

### Get graph data (programmatic)

For custom visualization or analysis without opening the browser:

```
cortex-viz:get_methodology_graph({ "domain": "<optional filter>" })
```
