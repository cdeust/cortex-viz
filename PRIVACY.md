# Privacy Policy — cortex-viz

_Last updated: 2026-07-22_

cortex-viz is a **read-only, local** visualization server for Cortex and
Claude Code session data. This policy states exactly what it reads, what it
serves, and what leaves your machine.

## What the server processes

- Your local Cortex database (PostgreSQL or SQLite), read-only, when present.
- Claude Code artifacts under `~/.claude/` — session transcripts
  (`projects/**/*.jsonl`), wiki pages, profiles — and local git state.
- In `--no-db` mode, only the `~/.claude/` artifacts and git.

The UI is served on **127.0.0.1 only**, with host-header and same-origin
guards; it is not reachable from your network.

## What leaves your machine

- **Your data: nothing.** No memory content, session content, code, or
  metadata is transmitted to the author, to Anthropic, or to any analytics
  service. There is no telemetry.
- **One disclosed exception — a CDN asset fetch:** the 3D brain view loads
  the three.js library (and related loaders) from the public unpkg CDN at
  page load. This transfers a standard library request to the CDN (your IP
  and a static file path — no user content). The other views use only
  vendored, locally-served assets and work fully offline.

## Your controls

- The activity-capture hooks no-op when no visualization instance is open.
- Close the server (idle watchdog also stops it) to end all processing.
- Avoid the brain view to avoid the CDN fetch entirely; all other views are
  offline-safe.

## Contact

admin@ai-architect.tools · https://github.com/cdeust/cortex-viz/issues
