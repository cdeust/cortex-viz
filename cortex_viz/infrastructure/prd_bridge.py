"""Bridge to the ``prd-spec-generator`` sibling MCP (the third live source).

prd-spec-generator is a STATELESS reducer: it turns a feature description into a
9-file Markdown PRD on demand and keeps no persistent queryable graph. So this
bridge has two halves, both degrading gracefully to empty (mirroring
``ap_bridge`` when AP has no graph):

  1. A connectable MCPClient (``PRDBridge``) for read-only liveness/config
     calls — the plumbing so the third MCP is reachable when needed.
  2. An on-disk PRD-artifact reader (``read_prd_graph``) that discovers the
     ``prd-output/<run>/0N-*.md`` files a pipeline run writes and surfaces them
     as PRD document/section nodes linked to their project — the part that
     produces graph nodes the moment any PRD exists.

When no PRD has been generated (the current state on this machine), both halves
return empty — no error, no nodes. Infrastructure layer only; no core imports.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from cortex_viz.errors import McpConnectionError
from cortex_viz.infrastructure.mcp_client import MCPClient

# Read-only tools we may call live (the pipeline-driving tools are NOT here —
# the viz never mutates a PRD run).
_PRD_TOOLS = frozenset({"check_health", "get_config", "read_skill_config"})

# PRD document filenames a run writes (file-export.ts). Index → section label.
_PRD_SECTIONS = {
    "01": "PRD", "02": "Data model", "03": "API spec", "04": "Security",
    "05": "Testing", "06": "Deployment", "07": "JIRA tickets",
    "08": "Source code", "09": "Test code",
}


def _resolve_command() -> dict | None:
    """Resolve the MCP-client config for prd-spec-generator.

    Priority: ``CORTEX_PRD_COMMAND`` env (JSON ``{command,args,env}``) → the
    active ``prd-spec-generator@*`` entry in ``installed_plugins.json`` (launch
    via ``bash <installPath>/bin/ensure-deps.sh <installPath>`` with the
    PRD_GEN_* env the plugin's ``.mcp.json`` declares). Returns None when no
    install is found (graceful degradation).
    """
    raw = os.environ.get("CORTEX_PRD_COMMAND")
    if raw:
        try:
            cfg = json.loads(raw)
        except ValueError:
            return None
        return cfg if isinstance(cfg, dict) and "command" in cfg else None

    installed = Path.home() / ".claude/plugins/installed_plugins.json"
    try:
        data = json.loads(installed.read_text(encoding="utf-8"))
        plugins = data.get("plugins", {}) if isinstance(data, dict) else {}
        for key, entries in plugins.items():
            if not key.startswith("prd-spec-generator@"):
                continue
            if not isinstance(entries, list) or not entries:
                continue
            root = entries[0].get("installPath")
            if not root:
                continue
            ensure = Path(root) / "bin" / "ensure-deps.sh"
            if not ensure.is_file():
                continue
            return {
                "command": "bash",
                "args": [str(ensure), str(root)],
                "env": {
                    "PRD_GEN_SKILL_CONFIG": str(
                        Path(root) / "packages/skill/skill-config.json"
                    ),
                    "PRD_GEN_EVIDENCE_DB": str(Path(root) / ".prd-gen/evidence.db"),
                },
            }
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    return None


def is_enabled() -> bool:
    """True unless explicitly disabled via ``CORTEX_PRD_ENABLED=0``."""
    return (os.environ.get("CORTEX_PRD_ENABLED") or "1").strip() not in {"0", "false"}


class PRDBridge:
    """Thin MCPClient wrapper scoped to prd-spec's read-only tool namespace.

    Lazy-connects; ``connect()`` returns False (not raise) when disabled or
    unreachable, so callers degrade gracefully exactly like ``APBridge``.
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config = config
        self._client: MCPClient | None = None
        self._lock = asyncio.Lock()
        self._connected = False
        self._unavailable_reason: str | None = None

    async def connect(self) -> bool:
        if not is_enabled():
            self._unavailable_reason = "disabled"
            return False
        if self._connected and self._client is not None and self._client.connected:
            return True
        async with self._lock:
            if self._connected and self._client is not None and self._client.connected:
                return True
            cfg = self._config or _resolve_command()
            if cfg is None:
                self._unavailable_reason = "no_command_resolved"
                return False
            try:
                self._client = MCPClient({**cfg, "callTimeoutMs": 0})
                self._client._extra_allowed_commands = {"bash", "node"}
                await self._client.connect()
                self._connected = True
                self._unavailable_reason = None
                return True
            except (McpConnectionError, Exception) as exc:
                self._connected = False
                self._client = None
                self._unavailable_reason = f"{type(exc).__name__}: {exc}"
                print(
                    f"[cortex] PRD bridge disabled: {self._unavailable_reason}",
                    file=sys.stderr,
                )
                return False

    async def call(self, tool: str, args: dict | None = None) -> Any:
        if tool not in _PRD_TOOLS:
            raise ValueError(f"PRD tool not in allowlist: {tool!r}")
        if not await self.connect():
            return None
        try:
            return await self._client.call(tool, args or {})
        except Exception as exc:
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            print(f"[cortex] PRD call {tool} failed: {exc}", file=sys.stderr)
            return None

    async def health_check(self) -> Any:
        return await self.call("check_health", {})

    async def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False


def discover_prd_artifacts() -> list[Path]:
    """Find PRD run directories (``prd-output/<run>/``) under the dev roots.

    prd-spec writes ``prd-output/<8-char-run-id>/0N-*.md`` relative to the cwd
    of whoever ran the pipeline. We scan a small set of likely roots (home,
    cwd, the Developments tree) one level deep for ``prd-output`` dirs. Empty
    when no PRD has been generated — the common case today.
    """
    roots = [
        Path.cwd(),
        Path.home() / "Developments",
        Path.home(),
    ]
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        po = root / "prd-output"
        if not po.is_dir():
            continue
        for run_dir in sorted(po.iterdir()):
            s = str(run_dir)
            if run_dir.is_dir() and s not in seen:
                seen.add(s)
                found.append(run_dir)
    return found


def read_prd_graph() -> dict[str, list]:
    """Discovered PRD artifacts → ``{nodes, edges}`` (empty when none exist).

    Each run directory becomes a ``prd`` document node; each present section
    file becomes a ``prd_section`` node edged ``has_section`` from the
    document. Symbol/file cross-links (claim↔symbol) require the in-memory
    PRD-input bundle, which the stateless pipeline does not persist — those
    arrive in P4 (node unification) once a run exports its affected-symbols.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    for run_dir in discover_prd_artifacts():
        run = run_dir.name
        doc_id = f"prd:{run}"
        nodes.append({"id": doc_id, "kind": "prd", "type": "prd",
                      "label": f"PRD {run}"})
        for path in sorted(run_dir.glob("0[1-9]-*.md")):
            idx = path.name[:2]
            sid = f"prd_section:{run}:{idx}"
            nodes.append({"id": sid, "kind": "prd_section", "type": "prd_section",
                          "label": _PRD_SECTIONS.get(idx, path.stem)})
            edges.append({"id": f"{doc_id}->{sid}", "source": doc_id,
                          "target": sid, "kind": "has_section",
                          "type": "has_section"})
    return {"nodes": nodes, "edges": edges}


__all__ = ["PRDBridge", "is_enabled", "read_prd_graph", "discover_prd_artifacts"]
