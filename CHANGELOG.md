# Changelog

All notable changes to Hypermnesia MCP Viz are documented in this file.
Releases before 2.7.0 were recorded as `chore(release)` / `release:` commits in git history.

## [Unreleased]

### Added
- `MCPClient.aclose()`, an awaitable teardown that terminates the child and then
  waits for it to exit, escalating to `SIGKILL` after a 5 s grace period. The
  synchronous `close()` can only request the exit; it cannot reap. Every async
  caller (both bridges, the handshake-failure and idle-timeout paths, and the
  stdio handshake test) now awaits `aclose()`.

### Fixed
- A leaked asyncio subprocess transport (#113). `close()` left the child
  terminating and its transport alive, so a caller that closed its event loop
  immediately afterwards — which `asyncio.run` does on return — left the
  transport to be finalized against a dead loop. `__del__` then raised
  `RuntimeError: Event loop is closed`, which the interpreter swallows, so the
  whole suite reported it as a single warning attributed to an unrelated
  graph-build test. Awaiting the reap closes the transport while its loop is
  still alive, and leaves no zombie for a caller that opens many short-lived
  clients.
- The test suite now fails on `PytestUnraisableExceptionWarning` instead of
  printing it. An exception inside `__del__` cannot fail a test by itself, so
  this class of resource leak was structurally invisible.

### Security
- Update `cryptography` from 49.0.0 to 50.0.0, the first release patched for
  GHSA-g6cj-pr64-35w5 (high). Unlike the `brace-expansion` bump in 3.0.0 this
  one is a **runtime** dependency and does ship in the wheel: it arrives
  transitively through `authlib`, `joserfc`, `pyjwt` and `secretstorage`, none
  of which cap the major version (`joserfc` asks for `>=45.0.1`, the others are
  unpinned), so the bump needs no upstream release to land.
- Update the development-only `fast-uri` from 3.1.4 to 3.1.5, patched for
  GHSA-7p8r-x3mc-p8w7 (high, host confusion via a backslash authority
  introducer). Transitive under the JS test toolchain; it does not ship.
  `npm audit --package-lock-only` now reports zero vulnerabilities.

## [3.0.0] - 2026-08-04

### Security

- Update the transitive test dependency `brace-expansion` from 5.0.8 to 5.0.9,
  the first patched 5.x release for GHSA-rgw5-rvv9-x895 / CVE-2026-69152.
  The package remains development-only and does not ship in the wheel;
  `npm audit --package-lock-only` reports zero vulnerabilities.

### Changed

- **Breaking publication rename.** `hypermnesia-mcp-viz` is now the sole
  Claude Code plugin, MCP server, Python distribution, and console identity.
  Because `2.8.0` was already published, removing the old plugin and console
  identities ships as the SemVer-major `3.0.0` release rather than replacing
  an immutable artifact in place.
  Existing Claude installs must run
  `claude plugin uninstall cortex-viz@cortex-plugins`, refresh
  `cortex-plugins`, and run
  `claude plugin install hypermnesia-mcp-viz@cortex-plugins`. Permission and
  tool references must use Claude's composed names:
  `mcp__plugin_cortex-viz_cortex-viz__open_visualization` becomes <!-- mcp-prefix-allow-legacy -->
  `mcp__plugin_hypermnesia-mcp-viz_hypermnesia-mcp-viz__open_visualization`,
  and `mcp__plugin_cortex-viz_cortex-viz__get_methodology_graph` becomes <!-- mcp-prefix-allow-legacy -->
  `mcp__plugin_hypermnesia-mcp-viz_hypermnesia-mcp-viz__get_methodology_graph`.
  Direct-process hosts must replace the removed `cortex-viz` executable with
  `hypermnesia-mcp-viz`. This source change and the marketplace rename in
  `cdeust/Cortex#351` form one coordinated release and must not be published
  independently.
- The PRD bridge now discovers only the canonical `ai-architect-mcp-spec`
  Claude plugin and reports that publication identity in its API metadata and
  current documentation. A deprecated `prd-spec-generator` install is ignored.
- The artifact guard distinguishes GitHub tag runs from branch and pull-request
  refs before enforcing the immutable release version, so normal PR CI is not
  rejected for its `<number>/merge` ref name.
- Synchronize committed assurance evidence with OpenSSF Best Practices Silver,
  verified v2.8.0 Sigstore attestations, 81% Python statement coverage, zero
  open CodeQL alerts, and post-Silver OpenSSF Scorecard 7.4.

## [2.8.0] - 2026-08-03

### Added
- The canonical Python and MCP Registry distribution is now
  `hypermnesia-mcp-viz` at version 2.8.0. Releases publish the wheel and source
  archive to PyPI through Trusted Publishing, and `server.json` describes the
  matching stdio package for the official MCP Registry. The Python import
  package remains `cortex_viz`, but no `cortex-viz` publication or console
  alias is emitted.
- A versioned, host-neutral live activity contract
  (`docs/host-event-v1.schema.json`) for Codex, Gemini, and generic MCP-host
  adapters. `POST /api/activity` normalizes it into the existing activity
  graph while preserving the legacy Claude hook payload unchanged.
- Coverage-honesty indicator (#36): a non-modal per-view surface that answers "what is missing from what I am looking at?" — CBM's MissedCallout in this app's HUD idiom. On the graph (galaxy) and trace (workflow) views it declares completeness explicitly: nodes/edges rendered vs the store total, files indexed vs present with a drill-down of extraction failures, the edge count the LOD aggregator collapsed (a named degraded mode, never inferred from a thinner picture), snapshot staleness (age + store/snapshot revision), and stream truncation. A fully covered view shows a quiet "Complete" affordance, not a warning. Engine parse-coverage is read from `GET /api/graph/coverage` against the automatised-pipeline#57 shape; when the engine has not reported it the endpoint returns an explicit `available:false` degraded mode rather than a fabricated figure. Verdict logic is a pure, mutation-gated seam (`ui/unified/js/coverage_model.js`); every emission — including the quiet complete state — is asserted by tests.
- JavaScript test harness for the browser UI (`ui/`, ~25.5k lines), wired into CI as a required job alongside pytest — a failing JS test now fails CI (#35). Vitest + jsdom, no bundler. Initial suites cover the highest-silent-risk surfaces: force-layout neighbour-set + edge-tier styling, workflow-graph filter predicates (state→visible-set), LOD aggregation thresholds, palette resolution + `cortex:surface-change` refresh, and SVG-vs-canvas renderer agreement on one model. Test strength is gated by mutation (Stryker), not line coverage; survivors triaged in `tests/js/MUTATION_NOTES.md`.

- Python mutation testing (`scripts/mutation_check.sh`, mutmut 3.x), the counterpart to the existing Stryker gate for JS — rules/coding-standards.md §12. Scoped per change: it repoints `only_mutate` and the test selection at the touched files and restores `pyproject.toml` afterwards.

### Changed
- The streaming graph builder's association and supersede phases (`handlers/workflow_graph_streaming.py`) capture their delta baselines unconditionally instead of inside a second copy of the `_mem_cap > 0` guard that wraps the read (`py/uninitialized-local-variable`, 3 alerts). The guard sat on the far side of the ingest loop, so both a reader and an analyser had to prove `_mem_cap` was unchanged across the loop before the read was safe; the supersede phase's two arms also turned out to be the same expression written twice, since `_assoc_target is builder` whenever the cap is bounded. Behaviour-preserving. The re-scoped mutation run then showed both phases' progress counts and delta frames were unasserted — the tests drove them with empty association and supersede inputs — so they are now exercised with real rows under both cap modes and their emitted frames asserted.

- The pg_trgm conformance + scale benchmark for `ui/brain/js/trigram.js` is now a first-class vitest suite (`tests/js/trigram.test.mjs`), absorbing the former side-channel harness `tests/js/run_trigram_conformance.mjs` and its pytest wrapper `tests/test_trigram_conformance.py` (both removed) (#35).

### Fixed
- Rejected requests no longer hang the client (#66). `send_plain_error` wrote a status line and terminated the headers without `Content-Length`, and the standalone server runs at HTTP/1.1 where keep-alive is the default — so every 403/404 from the sandboxed static readers was an *unframed* response: the caller could not tell the empty body had ended and blocked until its own timeout (measured on a raw socket: 5.003 s to client timeout before, 0.001 s and framed after). Every rejection path in `serve_static` / `serve_shared_asset` went through that helper, so the failure was total on the refusal side while every accepted request looked healthy — which is why a green suite never saw it: the traversal tests asserted no body was *leaked*, never that the client could tell there was no body. The contract is now pinned for all three response helpers, including the accepted path, in `tests/test_http_response_framing.py` (24 tests; 21 fail against the pre-fix helper).

### Security
- `/api/file-diff` and `/api/trace/file` no longer read files outside the user's own trees (`py/path-injection`, 10 high, #46). The absolute-path branch of the name ladder handed the request's string straight to the diff engine, so any file inside any git repository anywhere on the machine came back in full as a `diff_type: "untracked"` patch — reproduced against a throwaway repo outside every configured root. Reads are now contained to the roots the graph's file nodes actually come from (the configured development roots, `~/.claude`, the temp roots agent scratchpads use); measured against the live activity spine, all 1069 graphed absolute paths fall inside them and none outside, so nothing reachable was given up. The remaining alerts were guards that were already correct but written in forms no analyser models (`os.path.commonpath` in the wiki reader, `base in target.parents` in the `/shared/` reader) — rewritten onto one containment primitive, `shared/path_containment.py`, which resolves symlinks before comparing and compares on a separator-terminated prefix, and which returns the proven path rather than a boolean so a caller cannot use a value the guard did not sanction. Verified by running the CodeQL query locally before and after (10 → 0) with a paired full-suite run confirming no other rule moved (147 → 137).
- Brain-view search index no longer deduplicates words through an object used as a map (`js/remote-property-injection`, 2 high, #153/#154). Triage first: the maps were `Object.create(null)`, so no prototype pollution was reachable and `Object.prototype` was provably untouched — the alerts were not exploitable as rated. The null prototype was load-bearing all the same, and the defect it was holding back is a search one: on a plain object, `seen['constructor']` is truthy before anything is written, so a node labelled `constructor` would be silently dropped from the index and become unfindable. Both maps are now `Set`s, which have no name space to collide with (and is what the CodeQL query itself recommends). `uniqueWords` moved from `search_worker.js` — untestable behind `importScripts` — into `trigram.js`, deleting the duplicate implementation and putting the one that remains under test. Pinned by 5 tests that fail against the object-backed version. Re-scoping the Stryker gate onto the changed lines then surfaced 6 unrelated gaps in the same tokenizer — either camelCase split loop could be deleted, the alnum-run class could be widened to make `/` and `_` word characters, and the no-alphanumeric-input path had no coverage at all — none of which any test could see; all six are now closed, taking the gate to 0 survivors (`tests/js/MUTATION_NOTES.md`).

- Brain-view legend and impact panel no longer build HTML attributes with a quote-incomplete escaper (`js/incomplete-html-attribute-sanitization`, 4 alerts, #148–#151). Both files' local `esc()` escaped only `&<>` while their output was interpolated into double-quoted attributes (`data-kind`, `data-color-cat`, `data-file`), so a value containing `"` closed the attribute and everything after it parsed as further attributes — `a" onmouseover="alert(1)` became a live handler, reproduced in jsdom before the fix. Both now escape the full set including `"` and `'`, matching what `ui/brain/js/search.js` and every `ui/unified` escaper already did; the two were the only quote-incomplete escapers in the tree that reach an attribute context. Pinned by parsed-DOM tests (`tests/js/brain_escape.test.mjs`) that assert the attribute round-trips and that no injected handler exists — 5 of the 8 fail against the pre-fix escapers — and by a re-scoped Stryker run at 0 survivors, which also closed an uncovered null-coercion arm in the impact panel.

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
