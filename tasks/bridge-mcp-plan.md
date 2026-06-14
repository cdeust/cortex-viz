# cortex-viz → Live Bridge Visualization MCP

**Decision (2026-06-14, supersedes the mechanical extraction plan):** `cortex-viz`
is the **sole** visualization MCP. Cortex ships **no** viz. cortex-viz is a
**bridge** that live-queries three MCPs and overlays a **live session-activity
stream**.

> Raw intent (user, verbatim): *"The visualization should be a bridged mcp that
> query from live data on cortex mcp, automatised-pipeline mcp, prd-spec-generator
> mcp. The mcp is a live streaming data of ALL NODES FROM ALL PROJECTS ACROSS ALL
> FILES WITH FULL DIRECTIONAL AND IMPACT MAPPING. IT LOGS ALL ACTION FROM CLAUDE
> FROM SESSION OPENING TO CLOSING THE SESSION, SHOWING ALL TOOLS USED, ALL MCP
> CALLED, ALL FILES ACCESSED, ALL FILES READ, ALL FILES MODIFIED, ALL SKILLS USED,
> ALL COMMANDS EXECUTED, ALL TERMINAL COMMAND EXECUTED. CORTEX IS NOT INCLUDING
> VISUALIZATION ANYMORE, ONLY THIS NEW BRIDGED MCP IS MAKING THE VISUALIZATION."*

## Three live sources (read-only bridges)

| Source | Bridge | Provides |
|---|---|---|
| **cortex** MCP | shared PG (`MemoryReader`) + recall tools | memories, entities, relationships, causal chains |
| **automatised-pipeline** MCP | `infrastructure/ap_bridge.py` (exists) | AST symbols, **directional** edges (calls/imports/defined_in/member_of), **impact** (`get_impact` blast radius), processes |
| **prd-spec-generator** MCP | `infrastructure/prd_bridge.py` (NEW, mirror ap_bridge) | PRD claims, sections, verdicts, codebase-grounding symbols |

## Live session-activity capture (the core new capability)

Claude Code **hooks** fire on every action. A capture hook fire-and-forgets a
POST to the running viz server; the server stores it and streams it to the live
graph. Confirmed mechanics:
- `PostToolUse`/`PreToolUse` fire for **every** tool, including `mcp__*` (MCP
  calls), `Skill` (skills/slash-commands), `Bash` (terminal), `Task`/`Agent`
  (subagents), `Read`/`Edit`/`Write` (files). Matcher `""`/`.*` = all.
- Payload (stdin JSON): `tool_name`, `tool_input` (`file_path`/`command`/
  `pattern`/`skill`/MCP args), `tool_response`, `cwd`. `session_id` present on
  `SessionStart`/`Stop`; for `PostToolUse` correlate via `cwd`+transcript or a
  SessionStart-stamped sidecar.
- `SessionStart`/`Stop` delimit the session; `UserPromptSubmit` carries prompts.

```
Claude session ──hooks(every action)──▶ activity_capture.py ──POST /api/activity──▶
  cortex-viz server ──▶ activity_store (PG session_activity) ──▶ SSE /api/activity/stream ──▶ live graph
                                       └─▶ enrich: AP get_impact on edits, cortex entities, prd claims
```

Each event → directional nodes/edges:
`session → prompt → action(tool/mcp/skill/command) → target(file/symbol/mcp/entity)`,
plus `action → impact(symbol…)` from AP on file writes.

## What already exists (reuse, do not rebuild)

- **Trace view** (`/api/trace/*`): L0 domains → L1 sessions → L2 causal chain
  (`prompt→action→file`, live `since` cursor) → L3 file (AST+git) → **L4 impact
  (already calls AP: callers/importers/calls/imports/processes)**. Schema
  `trace.v1`. Post-hoc JSONL poll @4s — to be fed by the live stream instead.
- **AP bridge** (`ap_bridge.py`) + L6 AST in the graph build.
- **SSE infra** (`graph_event_stream.py`, `/api/graph/events`).
- **Durable graph snapshot** (`/api/graph/full`, `snapshot_pg_store`) — stable
  full-graph serving (added this session).

## Gaps vs vision

1. Activity capture is post-hoc JSONL @4s, not live hooks; **misses MCP calls,
   skills, slash/terminal commands**.
2. No prd-spec bridge.
3. Trace is per-session; no unified "all nodes / all projects / all files".
4. Cross-source node unification (file↔symbol↔entity↔claim) not done.
5. The 4 non-graph views (Wiki/Knowledge/Board/Pipeline) were stubbed to 410 in
   the extraction — orphaned UI. Either serve from PG/bridges or remove tabs.

## Phases (each independently shippable + verifiable)

- **P0 — Live activity spine.** `activity_store` (PG `session_activity`) +
  `POST /api/activity` ingest + `GET /api/activity/stream` SSE + `activity_capture.py`
  hook + core event→nodes/edges mapping. Verify: a simulated + a real hook event
  appears in the live graph within ~1s. **(building now)**
- **P1 — Full action taxonomy.** Map every action: tool, mcp_call, file
  read/write/edit, skill, slash command, terminal Bash, subagent, prompt →
  typed directional nodes/edges. Capture all hook events (Pre/Post/Prompt/Start/Stop).
- **P2 — prd-spec bridge.** `prd_bridge.py`; fuse PRD claims/sections; link
  claim↔symbol↔file.
- **P3 — Live impact mapping.** On file edit, async AP `get_impact` → blast-radius
  edges drawn live (extend the L4 impact path into the live graph).
- **P4 — Unified all-projects view + node unification** (file↔symbol↔entity↔claim
  by path / qualified-name) across every project's AP graph.
- **P5 — Resolve the 4 orphaned views** (serve from PG/bridges or drop) →
  parity-plus → **then** merge `viz-extraction-strip` into Cortex main (the gated
  irreversible step) and ship cortex-viz as the plugin MCP.

## Hard gate (unchanged)
Do **not** merge the strip to Cortex `main` until cortex-viz is parity-plus
(all 5 views work + live activity + 3 bridges). Cortex `main` keeps the working
viz until then.
