# Changelog

All notable changes to cortex-viz are documented in this file.
Releases before 2.7.0 were recorded as `chore(release)` / `release:` commits in git history.

## [Unreleased]

### Added
- Coverage-honesty indicator (#36): a non-modal per-view surface that answers "what is missing from what I am looking at?" — CBM's MissedCallout in this app's HUD idiom. On the graph (galaxy) and trace (workflow) views it declares completeness explicitly: nodes/edges rendered vs the store total, files indexed vs present with a drill-down of extraction failures, the edge count the LOD aggregator collapsed (a named degraded mode, never inferred from a thinner picture), snapshot staleness (age + store/snapshot revision), and stream truncation. A fully covered view shows a quiet "Complete" affordance, not a warning. Engine parse-coverage is read from `GET /api/graph/coverage` against the automatised-pipeline#57 shape; when the engine has not reported it the endpoint returns an explicit `available:false` degraded mode rather than a fabricated figure. Verdict logic is a pure, mutation-gated seam (`ui/unified/js/coverage_model.js`); every emission — including the quiet complete state — is asserted by tests.
- JavaScript test harness for the browser UI (`ui/`, ~25.5k lines), wired into CI as a required job alongside pytest — a failing JS test now fails CI (#35). Vitest + jsdom, no bundler. Initial suites cover the highest-silent-risk surfaces: force-layout neighbour-set + edge-tier styling, workflow-graph filter predicates (state→visible-set), LOD aggregation thresholds, palette resolution + `cortex:surface-change` refresh, and SVG-vs-canvas renderer agreement on one model. Test strength is gated by mutation (Stryker), not line coverage; survivors triaged in `tests/js/MUTATION_NOTES.md`.

### Changed
- The pg_trgm conformance + scale benchmark for `ui/brain/js/trigram.js` is now a first-class vitest suite (`tests/js/trigram.test.mjs`), absorbing the former side-channel harness `tests/js/run_trigram_conformance.mjs` and its pytest wrapper `tests/test_trigram_conformance.py` (both removed) (#35).

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
