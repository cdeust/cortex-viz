#!/usr/bin/env python3
"""Detect drift between the vendored MCP Registry schema and its live source.

A copied file drifts silently -- provenance in scripts/schemas/README.md is
the minimum, not a guarantee. This is the automatable check for it:
scripts/schemas/2025-12-11.json's own `$id` field IS the canonical URL
(https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
-- the same one server.json's `$schema` field points to, the same one a
spec-conformant client would fetch). Fetch it and compare, structurally
(parsed JSON equality, not byte-for-byte, so whitespace/key-order changes
upstream don't produce false alarms) against the vendored copy.

Non-blocking by design (`continue-on-error` at the call site in ci.yml):
this makes the same trade the live semantic pre-flight step makes --
useful signal when the network is reachable, never a required-check
failure when it briefly isn't. Structural validation
(scripts/validate_server_manifest.py) is the actual gate and never depends
on this succeeding.

Real drift was found while building this check (2026-08-10, manual curl +
diff against the three candidate sources -- registry repo tag v1.8.1,
modelcontextprotocol/static main, and the CDN): the vendored copy, first
sourced from the registry repo's v1.8.1 git tag, carried a `maxLength:
255` constraint on `Package.version` that the CDN's currently-served
document does not have. The registry repo's own `sync-schema.yml` is
`workflow_dispatch`-only ("TODO: Add daily schedule later"), so its
embedded copy can silently lag the canonical modelcontextprotocol/static
source it is meant to mirror. The vendored file here was re-pulled from
the CDN itself (the ground truth per the paragraph above, and the URL
this script fetches), not from the registry repo tag, to fix it.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = ROOT / "scripts" / "schemas" / "2025-12-11.json"
TIMEOUT_SECONDS = 15


def main() -> None:
    vendored = json.loads(SCHEMA_FILE.read_text())
    source_url = vendored["$id"]

    try:
        with urllib.request.urlopen(source_url, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            live = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        # Non-blocking by design (see module docstring) -- the caller sets
        # continue-on-error, but exit nonzero anyway so the step is visibly
        # red rather than silently green on an unreachable network.
        print(f"could not reach {source_url}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if vendored == live:
        print(f"{SCHEMA_FILE} matches {source_url}")
        return

    print(
        f"{SCHEMA_FILE} has DRIFTED from {source_url} — re-vendor per "
        "scripts/schemas/README.md.",
        file=sys.stderr,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
