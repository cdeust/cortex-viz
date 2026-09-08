# cortex-viz

Standalone read-only visualization MCP for Cortex (published as hypermnesia-mcp-viz).
Python + JavaScript.

This file is deliberately short: the host harness loads what it needs on demand.
Everything that used to be here is in `docs/agent-guidance.md` (repo-specific
constraints, etiquette). Read it before any non-trivial change.

## Commands

```bash
uv sync --frozen --extra dev   # bare --frozen silently skips pytest/mutmut; see agent-guidance.md
uv run pytest                  # Python suite
uv run ruff check . && uv run ruff format --check .
```

## Non-negotiables

- Read-only over Cortex's store: this server never writes memories.
- Layers: core / server / infrastructure / handlers / hooks / shared / errors.
- The plugin install is live-mounted onto this clone; respawn the standalone HTTP server
  after editing Python or the running process keeps the old code.
