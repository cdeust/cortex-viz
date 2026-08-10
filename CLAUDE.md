# cortex-viz

Standalone read-only visualization MCP for Cortex (published as hypermnesia-mcp-viz). Python + JavaScript.

Global rules are imported, not restated:

@~/.claude/rules/model-behavior.md
@~/.claude/rules/coding-standards.md

## Repo-specific constraints

- Read-only over Cortex's store: this server never writes memories.
- The plugin install is live-mounted onto this clone; respawn the standalone HTTP server after editing Python or the running process keeps the old code.
- Layers: core / server / infrastructure / handlers / hooks / shared / errors.
- **`uv sync --frozen` alone does not install `pytest` or `mutmut`** — they live in the `dev` optional-dependency group (`pyproject.toml`'s `[project.optional-dependencies] dev`), and a bare `--frozen` sync skips every extra. Always run `uv sync --frozen --extra dev` in a fresh worktree/clone before testing.
  - **Symptom, not just the fix**: this fails *silently*, not loudly. `uv run pytest` still runs — it just falls through PATH to a system `pytest` (e.g. `/opt/homebrew/bin/pytest`, a different Python than `.venv`'s) instead of erroring "pytest not found". The tell is `uv run which pytest` resolving outside `.venv/bin/`, or DB-backed test modules (`test_memory_read.py`, `test_no_db_mode.py`, ...) failing collection with `ModuleNotFoundError: No module named 'psycopg'` even though `uv run python -c "import psycopg"` succeeds — the module-level import works fine standalone; only the wrong pytest interpreter can't see it. A scoped run against a couple of test files can look completely green while the full suite silently loses its collection of every DB-touching module — the whole point of `--frozen` (a reproducible, verifiable environment) is defeated by exactly the case it's meant to prevent.

## Etiquette

Conventional commits, staged file-by-file. One PR per concern. Do not merge your own PR without the owner's go-ahead.
