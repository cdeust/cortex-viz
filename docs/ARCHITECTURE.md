# Architecture

cortex-viz turns three data sources it does not own (Cortex's PostgreSQL store,
Claude Code's on-disk session artifacts, and your local git) into six browser
views. It **reads**, it never writes. This document is the high-level design:
what the layers are, which way dependencies point, and where the trust
boundaries sit.

The security argument built on this shape is in
[ASSURANCE_CASE.md](ASSURANCE_CASE.md).

## The two halves

cortex-viz is one repository containing two programs that talk over HTTP:

```
   MCP host (Claude Code, Cursor, ...)
            |  stdio
            v
   +---------------------------+          +-----------------------------+
   |  cortex_viz/ (Python)     |  HTTP    |  ui/ (browser JavaScript)   |
   |  MCP server + local       |<-------->|  98 files, ~26k lines,      |
   |  HTTP server, 127.0.0.1   |  JSON    |  vanilla ESM/IIFE, no       |
   |                           |  SSE     |  bundler, no framework      |
   +---------------------------+          +-----------------------------+
        |          |         |
        v          v         v
   Cortex PG   ~/.claude   local git
   (read-only)  artifacts   (read-only)
```

The browser half is the larger and the more exposed one: it is the code that
actually executes in a user's browser. That is why CodeQL analyses
`javascript-typescript` as well as `python`, and why the release workflow
fingerprints every byte under `ui/`.

## Python layers

Dependencies point **inward**. A module may import from the layers below it in
this table, never above.

| Layer | Directory | Files | Responsibility | May import |
|---|---|---|---|---|
| **Entry points** | `cortex_viz/__main__.py`, `handlers/` | 12 | MCP tool surface and composition roots. Wires concrete infrastructure into core. | everything below |
| **Server** | `cortex_viz/server/` | 53 | The standalone HTTP server: routing, static serving, SSE streams, security guards, layout authority. | core, infrastructure, shared |
| **Infrastructure** | `cortex_viz/infrastructure/` | 37 | All I/O: PostgreSQL reads (`MemoryReader`), session JSONL parsing, wiki file reads, git invocation. | core, shared |
| **Core** | `cortex_viz/core/` | 39 | Graph construction, layout, tiling, domain mapping. Pure transformation over data handed in. | shared |
| **Shared** | `cortex_viz/shared/` | 9 | Pure helpers with no project dependencies: hashing, JSON stream splitting, YAML parsing, identifier canonicalisation. | standard library |

One invariant is checked rather than assumed: **no `import mcp_server.*` is
permitted anywhere in `cortex_viz/`.** cortex-viz was extracted from the Cortex
memory engine, and that import ban is the extraction's correctness test. If it
ever passes, the two projects have re-fused and the read-only boundary is no
longer structural.

## The read contract

cortex-viz consumes Cortex's **artifacts on disk plus PostgreSQL**, never
Cortex's live Python objects.

| Data | Source | Access |
|---|---|---|
| Memories, entities, relationships | Cortex PG store (`DATABASE_URL`) | read-only, via `MemoryReader` |
| Wiki pages, thermodynamic state | `~/.claude/methodology/wiki/` plus the `wiki.*` PG schema | read-only |
| Archived sessions and execution traces | `~/.claude/projects/*.jsonl` | read-only |
| Live host activity | `POST /api/activity`, `docs/host-event-v1.schema.json` | append-only derived activity |
| Cognitive profiles | `~/.claude/methodology/profiles.json` | read-only |
| Codebase graph (AST symbols, impact) | [`ai-architect-mcp-codebase`](https://github.com/cdeust/ai-architect-mcp-codebase) MCP | read-only, stdio |
| PRD document nodes | [`ai-architect-mcp-spec`](https://github.com/cdeust/ai-architect-mcp-spec) MCP | read-only |
| File diffs and commit history | local `git` | read-only |

**Degraded mode is explicit, never silent.** With no database reachable, the
server logs one line and starts in no-DB mode: Trace is fully live from session
JSONLs and git, and the five DB-backed views render greyed out with an install
pointer instead of erroring or fabricating data.

## The browser half

| Directory | What it renders |
|---|---|
| `ui/unified/` | Graph (galaxy) and Trace: force layout, workflow graph, LOD aggregation, SVG and canvas renderers |
| `ui/brain/` | The 3D anatomical brain view, trigram search index, search worker |
| `ui/dashboard/` | Board (consolidation kanban) and Knowledge |
| `ui/methodology/` | The methodology map |
| `ui/shared/` | Design-system palette resolution shared by every view |

There is **no bundler and no framework**. Files are vanilla browser IIFEs that
publish seams onto a global event bus, which is why the vitest harness loads
them with `new Function(code)` into a jsdom global rather than importing them
as ES modules. That choice has a measurement consequence recorded in the
assurance case: v8 coverage cannot attribute the executed lines back to the
source file.

## Trust boundaries

Four boundaries, in order of exposure:

1. **Browser to local HTTP server.** The widest one. Any page a user visits can
   attempt requests to `127.0.0.1:<port>`. Countered by a Host-header
   allowlist (DNS rebinding, CWE-346/350), an Origin allowlist with control
   character filtering (CWE-942, CWE-113), and a same-origin check on writes
   (CWE-352), all in `cortex_viz/server/http_security.py`.
2. **Filesystem paths derived from requests.** Static asset serving, wiki
   reads, and git diff paths take a request-derived path. This is the boundary
   with open findings, tracked in
   [#46](https://github.com/cdeust/cortex-viz/issues/46).
3. **The Cortex PostgreSQL store.** Read-only by contract. The connection
   string is user-supplied configuration, not a secret this project mints.
4. **The shipped UI artifact.** The 98 JS files run in the user's browser, so
   their integrity is the product's integrity. Countered by the per-release
   `sha256sum` manifest over the whole `ui/` tree, itself covered by a Sigstore
   build-provenance attestation.

## Build and release

There is no compilation step. `hatchling` builds a wheel and an sdist from
source, and `ui/` ships as static data inside the wheel.
`.github/workflows/Release.yaml` is the only path that produces a release: it
tests, builds, fingerprints `ui/`, emits a CycloneDX SBOM from `uv.lock`,
computes checksums, and attests build provenance for every artifact before
uploading them.

Delivery reality: cortex-viz is installed through the Claude Code plugin
marketplace, pinned in Cortex's manifest. Tagging and publishing a GitHub
Release does not reach installs by itself, so the release checklist ends with
bumping that pin.
