"""Canonical identity of the upstream codebase-intelligence MCP server.

The producer renamed itself twice — ``automatised-pipeline`` ->
``ai-architect-codebase`` -> ``ai-architect-mcp-codebase`` (canonical since
v0.9.0, 2026-08-04).

cortex-viz depends on that identity in two coupled places: the *resolver*
(marketplace registry key and binary path) and the *security allowlist* that
validates whatever the resolver produced, by basename. Correcting one without
the other trades a resolution failure for an "Command not in allowed list"
rejection, so both read this module and neither can drift alone.

Values mirror the producer's ``mcp-contract.json`` and are verified against it
in CI (.github/workflows/upstream-identity.yml), pinned to a commit SHA rather
than a tag because the producer's README is explicit that tags can be moved:
https://raw.githubusercontent.com/cdeust/ai-architect-mcp-codebase/37728cc5747cebe39ea9d4011b7424de90f0a57b/mcp-contract.json

Legacy names stay resolvable but never preferred: a host installed before
v0.9.0 keeps its old registry key on disk, and the producer still ships an
``automatised-pipeline`` ``[[bin]]`` alias labelled "Compatibility alias only"
— a bridge, not a contract.
"""

from __future__ import annotations

CANONICAL_REPO = "cdeust/ai-architect-mcp-codebase"
CANONICAL_PLUGIN = "ai-architect-mcp-codebase"
CANONICAL_MARKETPLACE = f"{CANONICAL_PLUGIN}-marketplace"
CANONICAL_PLUGIN_KEY = f"{CANONICAL_PLUGIN}@{CANONICAL_MARKETPLACE}"
CANONICAL_BINARY = "ai-architect-mcp-codebase"
REPOSITORY_URL = f"https://github.com/{CANONICAL_REPO}"

LEGACY_PLUGIN_KEY_PREFIXES = ("automatised-pipeline@",)
LEGACY_BINARIES = ("automatised-pipeline",)

#: Registry-key prefix paired with the binary that install ships, canonical
#: first so a stale install can never win over a current one.
PLUGIN_KEY_BINARIES = (
    (f"{CANONICAL_PLUGIN_KEY.split('@')[0]}@", CANONICAL_BINARY),
    *zip(LEGACY_PLUGIN_KEY_PREFIXES, LEGACY_BINARIES),
)

BINARY_NAMES = (CANONICAL_BINARY, *LEGACY_BINARIES)

#: Commands the spawn layer may execute. Validated by basename: omitting the
#: canonical name here turns a correct resolution into a refused connection.
ALLOWED_UPSTREAM_COMMANDS = frozenset(BINARY_NAMES)
