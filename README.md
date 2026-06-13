# cortex-viz

Visualization and graph MCP server for [Cortex](https://github.com/cdeust/Cortex).

Extracted from the Cortex memory engine so that Cortex remains a pure
memory/profiling MCP. `cortex-viz` owns the neural-graph galaxy, the
methodology map, the workflow graph, the trace/diff UI, and the layout
authority — everything that renders, not remembers.

## Boundary

`cortex-viz` consumes Cortex's **artifacts on disk + PostgreSQL**, never
Cortex's live Python objects:

| Data | Source |
|---|---|
| Memory rows (graph nodes) | Cortex PG store (shared `DATABASE_URL`) |
| Cognitive profiles | `~/.claude/methodology/profiles.json` |
| Sessions / traces | `~/.claude/projects/` |
| Codebase graph | `automatised-pipeline` MCP (called directly) |

No `import mcp_server.*` is permitted anywhere in `cortex_viz/` — that
invariant is the extraction's correctness check.

## MCP tools

`open_visualization`, `get_methodology_graph`, `query_workflow_graph`,
`workflow_graph`, `graph_inspect`, `graph_stream`, `ingest_codebase_graph`.

## Status

Alpha — extraction in progress. See the parent repo's
`tasks/viz-mcp-extraction-plan.md` for the migration sequence.
