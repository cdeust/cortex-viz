# Changelog

All notable changes to cortex-viz are documented in this file.
Releases before 2.7.0 were recorded as `chore(release)` / `release:` commits in git history.

## [2.7.1] - 2026-07-25

### Fixed
- Redaction pass on the two user-visible panel stage hints that carried em dashes (house rule: zero in published copy) (#31). Surface audit recorded in #31: cortex-viz generates no LLM prose, so no runtime redaction machinery applies here; README/docs copy sweep tracked in #32.

## [2.7.0] - 2026-07-22

### Added
- `--no-db` Trace-only mode with auto-fallback: the standalone HTTP server now runs without a Cortex/PostgreSQL store, serving the Trace view over Claude Code session JSONLs, and falls back to it automatically when the database is unreachable (#27).
- LICENSE (MIT) and CI workflow (#26).
- `glama.json` maintainer claim for the Glama MCP directory (#28).
- Privacy policy (`PRIVACY.md`), required by the plugin Directory Policy (#29).
- README note on installing under other MCP hosts (Gemini CLI, Codex, Cursor, Windsurf, VS Code).

### Changed
- Legacy-name cleanup: install hints in `cortex_viz/core/tile_renderer.py` and `cortex_viz/core/layout_engine.py` now point to `pip install cortex-viz[viz-tile]` instead of the legacy `neuro-cortex-memory` package name; the `viz-tile` provenance comment in `pyproject.toml` now names the Cortex memory engine (hypermnesia-mcp).
