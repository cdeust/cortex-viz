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

## Run

```bash
pip install -e ".[data,viz-tile]"   # data = PG read path; viz-tile = igraph/datashader tiles
cortex-viz                          # or: python -m cortex_viz   (stdio MCP transport)
```

Set `DATABASE_URL` to the shared Cortex database (defaults to
`postgresql://127.0.0.1:5432/cortex`). `open_visualization` launches the
bundled galaxy UI in the browser; it reads the store **read-only** via
`MemoryReader` and never writes memories.

## MCP tools

Registered now: `open_visualization`, `get_methodology_graph`.

The galaxy/workflow graph is served over HTTP by the server
`open_visualization` launches. Graph-data MCP tools that still depend on
Cortex's storage layer (`query_workflow_graph`, `graph_inspect`,
`ingest_codebase_graph`) are not yet registered here — they will be wired once
rebased onto `MemoryReader` / the `automatised-pipeline` MCP.

## Status

Alpha — extraction in progress. Standalone server boots on `MemoryReader` and
the galaxy graph builds end-to-end (153k+ nodes). See the parent repo's
`tasks/viz-mcp-extraction-plan.md` for the migration sequence.
