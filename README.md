<p align="center">
  <img src="docs/assets/cortex-workflow-graph.png" alt="cortex-viz galaxy — each project becomes a dense brain-region cloud whose shape IS its code: files, commands, agents, memories and AST symbols (functions, methods, classes, modules, constants across 10 languages) pulled into position by the real edges between them (defined_in, calls, imports, member_of, tool_used_file). Symbols touched by two projects sit in the inter-project space between their hubs; long threads mark shared files and MCPs." width="100%"/>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/mcp-claude--code-blue.svg" alt="MCP">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/version-2.0.0-brightgreen.svg" alt="Version 2.0.0">
</p>

# cortex-viz

**The visualization layer for [Cortex](https://github.com/cdeust/Cortex).** A standalone MCP that turns Cortex's memory store, your Claude Code session history, and your codebase graph into seven live reading angles over the same data — a galaxy of every project, that same graph rendered inside a 3D anatomical brain, a per-session execution trace, a consolidation kanban, a curated knowledge browser, and a wiki. It reads Cortex's shared PostgreSQL store **read-only** plus the `~/.claude` artifacts; it renders, it never remembers.

Launch with the `open_visualization` tool (or `/cortex-visualize`). One launcher opens seven reading angles; the default landing view is **Trace**.

---

## Getting Started

The plugin marketplace is the supported install path. cortex-viz ships in the same `cortex-plugins` marketplace as Cortex:

```bash
claude plugin marketplace add cdeust/Cortex
claude plugin install cortex-viz
```

> **cortex-viz is a read-only companion to [Cortex](https://github.com/cdeust/Cortex).** Install Cortex first (`claude plugin install cortex`) — cortex-viz reads its shared PostgreSQL store and never writes to it. Point both at the same database: the `database_url` plugin setting defaults to `postgresql://127.0.0.1:5432/cortex`; set it to the same value you gave Cortex.

Restart your Claude Code session, then launch the visualizer:

```
/cortex-visualize
```

One launcher opens all seven reading angles (Graph · Brain · Trace · Board · Knowledge · Wiki · Pipeline) in the browser, served live from the Cortex store, your session JSONL, the code graph, and git.

<details>
<summary><strong>More options</strong> (Clone, manual run)</summary>

**Clone + run from source:**
```bash
git clone https://github.com/cdeust/cortex-viz.git && cd cortex-viz
pip install -e .
DATABASE_URL=postgresql://127.0.0.1:5432/cortex python3 -m cortex_viz
```

</details>

---

## The views

### Graph — the Claude workflow map

Each project becomes a **cloud of nodes** around one gold domain hub. Inside every cloud, nodes sit in six concentric levels by the Claude surface (or the code itself) that produced them:

| Level | What's there | Click through to |
|---|---|---|
| **L1 · Setup** | Skills · Commands · Hooks · Agents · MCPs | File paths; which domains share an MCP (thin indigo bridges) |
| **L2 · Tools** | One hub per Claude tool per domain (Edit · Write · Read · Grep · Glob · Bash · Task) | Files touched + total uses |
| **L3 · Files** | Every file Claude opened, read, edited, searched, or referenced — colored by primary tool | `first_seen` / `last_accessed` / `last_modified` + **See diff against HEAD** |
| **L4 · Discussions** | One node per Claude Code session | `started_at`, duration, message count + **View full conversation** replay |
| **L5 · Memories** | Persistent memories, colored by consolidation stage | Full content, tags, every scientific measurement |
| **L6 · AST symbols** | The code itself — functions, methods, classes, modules, constants parsed from 10 languages (Rust, Python, TypeScript, Java, Kotlin, Swift, Objective-C, C, C++, Go) | Qualified name, symbol type, parent file, and named `defined_in` / `calls` / `imports` / `member_of` edges |

**Why L6 matters.** L5 and below tell you *what Claude did*; L6 tells you *what the code is*. Three things become visible for free: **shared code** (any symbol referenced by two projects drifts into the inter-project gap), **impact** (clicking a symbol surfaces every caller, importer, and member — "what breaks if I change this?" is a graph neighbourhood, not a grep), and **the shape of the codebase itself** (a dense petal around a file means a fat internal API; a thin one means a leaf module). A grouped filter (`L1–L6` / by kind / by AST edge kind / `Cross-domain`) isolates any slice.

### Brain — the galaxy inside a real cortex

The same graph, on a second surface: every node placed inside an anatomical **cortical mesh** by the neuroscience of memory systems rather than by force-direction. Episodic memories sit in the **medial temporal lobe** and migrate outward to neocortex along a hot→consolidated **depth gradient** (the complementary-learning-systems consolidation model); semantic entities in **temporal neocortex**; code symbols in association cortex; procedural skills in the **striatum and cerebellum**; domains at the connectome's **rich-club hubs**. Region centres are registered from real **MNI152 atlas** coordinates (affine fit, not vertex-exact — the mesh is a single unlabeled surface). Every synapse routes along a major **white-matter tract** (fornix, uncinate, SLF, corpus callosum). Node colour is the same semantic palette as the galaxy — memories by consolidation stage, entities/symbols by type — and a live **Memory science** panel mirrors the store's system vitals (consolidation pipeline, skills, source-monitoring, extinction, sleep phases, and every mechanism Cortex exposes).

Because the full graph (278k+ nodes, 5.5M edges) is far larger than a browser can take in one payload, the brain **streams** it in progressively through a bounded-queue, frame-budgeted NDJSON loader — the cloud fills in as you watch. Clicking any node opens the same rich detail card as the galaxy (content, tags, live heat, relations, git diff, impact). Open it from the **Brain** button in the view bar, directly at `/brain`, or programmatically via `open_visualization(view="brain")`.

<p align="center">
<img src="docs/assets/cortex-consolidation-board.png" width="100%" alt="Board view — five columns for labile, early LTP, late LTP, consolidated, and reconsolidating memories, each column header showing total count and per-bucket stage metrics (decay, vulnerability, plasticity, heat, importance, encoding, interference, hippo, replay) plus cards grouped by stage" />
</p>

### Board — consolidation as a kanban

Five columns by consolidation stage (`labile` · `early_ltp` · `late_ltp` · `consolidated` · `reconsolidating`). Each header reads live bucket metrics — decay rate, vulnerability, plasticity, heat / importance / encoding / interference medians, hippocampal dependency, replay count — with the advancement rule (`replay ≥ 3` — or `≥ 1` when `schema > 0.5`; `DA ≥ 1 or imp > 0.3`) printed under the bar. Cards carry heat, importance, surprise, valence, arousal, and the exact tool that created the memory.

<p align="center">
<img src="docs/assets/cortex-memory-detail.png" width="100%" alt="Memory detail modal — stage pill, tags, valence chip, full body, then a Scientific measurements grid with plain-language explanations of consolidation stage, activity (heat), baseline activity, importance, surprise, emotional tone, emotional intensity, confidence, plasticity, stability" />
</p>

**Detail panel — every measurement explained.** Clicking any node opens a panel with the raw value *and* a one-line plain-language explanation. Consolidation stage, activity (heat), importance, surprise, emotional tone and intensity, confidence, plasticity, stability — each a labeled bar with a sentence like *"How unexpected this memory was when it arrived. Surprises stick better than routine events."*

<p align="center">
<img src="docs/assets/cortex-trace.png" width="100%" alt="Trace view — each Claude Code session is a tight phyllotaxis disk of its own prompt → action → file → discussion → memory chain, gravity-packed around the domain hub so the sessions of a domain cluster together; clicking a session hub expands its chain into the session's conversation, files, AST symbols, impact and git history" />
</p>

### Trace · Knowledge · Wiki · Pipeline

- **Trace** *(default)* — the live execution-trace drill: collapsed domain hubs → sessions → the ordered prompt → action → file chain of what actually happened → a file's AST symbols, impact neighbourhood, and git history. Discussions and Cortex `remember`/`recall` ops are woven into the chain. Served live from session JSONL, the code graph, and git on every request — no snapshots, always current.
- **Knowledge** — curated memory cards with heat-based borders, emotion tags, and evidence file references; filter by domain or emotion, click any card for a full detail panel.
- **Wiki** — the per-project knowledge base as a browsable Project → Kind → Pages tree, with a coverage grid on the welcome screen, and a CodeMirror split-pane editor with live preview. (The wiki *content* is authored autonomously by [Cortex](https://github.com/cdeust/Cortex#the-autonomous-wiki); cortex-viz is its reading + editing surface.)
- **Pipeline** — a horizontal Sankey from domains through the write gate into consolidation stages; ribbon width = memory volume, so retention and drop-off are visible at a glance.

<p align="center">
<img src="docs/assets/wiki-project-tree.png" width="100%" alt="Wiki view — left panel organizes pages as Project → Kind → Pages (agentic-ai expanded showing Architecture Decisions, Explanation, Tutorial, Reference sub-trees), breadcrumb Wiki › agentic-ai › Architecture overview, page body opens with the auto-authored architecture explanation for the project" />
</p>

<p align="center">
<img src="docs/assets/wiki-edit-preview.png" width="100%" alt="Wiki editor — CodeMirror 6 source pane on the left showing YAML frontmatter (title, kind, domain, scope, status, authored_by, provenance, dates) and the body with wikilinks; live preview on the right rendering the same content with EB Garamond typography, hierarchical headings, and resolved cross-references" />
</p>

---

## Install

cortex-viz is a Claude Code plugin (and a plain MCP server). Point it at the **same database as your Cortex install** — it reads that store read-only.

**As a plugin** — ships the MCP server, the `/cortex-visualize` skill, and the live session-activity hooks. The bundled `scripts/launcher.py` bootstraps its own dependencies on first launch (no manual `pip` needed). Configure the DB via the plugin's `database_url` user-config (defaults to `postgresql://127.0.0.1:5432/cortex`).

**As a raw MCP / for development:**

```bash
pip install -e ".[data,viz-tile]"   # data = PG read path; viz-tile = igraph/datashader tiles (optional)
cortex-viz                          # or: python -m cortex_viz   (stdio MCP transport)
```

Set `DATABASE_URL` to the shared Cortex database. `open_visualization` launches the galaxy UI in the browser, bound to `127.0.0.1`.

## Boundary

cortex-viz consumes Cortex's **artifacts on disk + PostgreSQL**, never Cortex's live Python objects:

| Data | Source |
|---|---|
| Memories, entities, relationships (graph nodes) | Cortex PG store (shared `DATABASE_URL`), read-only via `MemoryReader` |
| Wiki pages + thermodynamic state | `~/.claude/methodology/wiki/` + the `wiki.*` PG schema |
| Sessions / execution traces | `~/.claude/projects/*.jsonl` |
| Cognitive profiles | `~/.claude/methodology/profiles.json` |
| Codebase graph (AST symbols, impact) | [`automatised-pipeline`](https://github.com/cdeust/ai-automatised-pipeline) MCP (stdio) |
| PRD document/section nodes | [`prd-spec-generator`](https://github.com/cdeust/ai-prd-generator) MCP + on-disk artifacts |

No `import mcp_server.*` is permitted anywhere in `cortex_viz/` — that invariant is the extraction's correctness check.

## MCP tools

`open_visualization` (launch the browser UI — pass `view="brain"` for the 3D anatomical brain, `view="galaxy"` or omit for the 2D graph) and `get_methodology_graph` (graph data). The seven views are served over HTTP by the server `open_visualization` launches; a live session-activity stream (every tool call, MCP call, file access, skill, and command) feeds the graph in real time via the activity-capture hooks.

## Status

The visualization stack was extracted from Cortex (which is now a focused memory engine) so the graphics ship and scale on their own. Standalone MCP boots over stdio; all seven views are bridged to live data; the galaxy builds end-to-end at 75k+ nodes; the 3D brain streams the full 278k-node graph into a cortical mesh; the suite passes.
