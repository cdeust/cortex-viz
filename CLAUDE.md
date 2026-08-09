# cortex-viz

Standalone read-only visualization MCP for Cortex (published as hypermnesia-mcp-viz). Python + JavaScript.

Global rules are imported, not restated:

@~/.claude/rules/model-behavior.md
@~/.claude/rules/coding-standards.md

## Repo-specific constraints

- Read-only over Cortex's store: this server never writes memories.
- The plugin install is live-mounted onto this clone; respawn the standalone HTTP server after editing Python or the running process keeps the old code.
- Layers: core / server / infrastructure / handlers / hooks / shared / errors.

## Etiquette

Conventional commits, staged file-by-file. One PR per concern. Do not merge your own PR without the owner's go-ahead.
