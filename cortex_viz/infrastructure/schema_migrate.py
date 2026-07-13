"""Best-effort schema migration via the installed Cortex plugin.

When ``schema_preflight.check_schema`` reports missing objects,
``open_visualization`` calls ``run_schema_migration`` here to invoke the
migration entry point Cortex ships in its own plugin package
(``mcp_server.migrate``, developed in parallel on Cortex branch
``feat/migrate-entrypoint``). This module owns discovery of the
installed plugin and the subprocess contract only — it never touches
the database directly and never assumes the migrate module exists (an
older installed Cortex plugin will not have it; that surfaces as a
non-zero exit, not an import inside this process).

Discovery mirrors how Claude Code itself launches the plugin's MCP
server (``.claude-plugin/plugin.json`` -> ``mcpServers.cortex`` ->
``python3 scripts/launcher.py mcp_server``, see
``scripts/launcher.py`` in the installed plugin): the launcher resolves
``CLAUDE_PLUGIN_ROOT``/``sys.path``/``cwd`` itself, so invoking
``scripts/launcher.py mcp_server.migrate`` the same way reuses that
exact bootstrap instead of re-deriving it here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# source: .claude-plugin/plugin.json mcpServers.cortex — the same path
# Claude Code itself launches the Cortex MCP server through.
_PLUGIN_CACHE_ROOT = (
    Path.home() / ".claude" / "plugins" / "cache" / "cortex-plugins" / "cortex"
)
_LAUNCHER_RELATIVE = Path("scripts") / "launcher.py"
_MIGRATE_MODULE = "mcp_server.migrate"

# Operational default, not a measured value: no prior migration-runtime
# data exists yet (the entry point is still in development on Cortex
# branch feat/migrate-entrypoint). 120s gives a schema migration on a
# typical single-tenant local Postgres store generous headroom without
# hanging open_visualization indefinitely on a stuck migration.
# source: engineering default pending measurement — revisit once
# mcp_server.migrate ships real timing data.
DEFAULT_MIGRATION_TIMEOUT_S: float = 120.0


def _parse_version(name: str) -> tuple[int, ...]:
    """Parse a ``X.Y.Z``-style directory name into a comparable tuple.

    Non-numeric segments sort as 0 so a malformed directory name never
    raises — it just loses ties against well-formed versions.
    """
    parts = []
    for segment in name.split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def find_cortex_plugin_root(cache_root: Path | None = None) -> Path | None:
    """Return the highest-version installed Cortex plugin root, if any.

    Precondition: none. Postcondition: returns the directory of the
    highest-version subdirectory under ``cache_root`` (defaults to the
    conventional Claude Code plugin cache path) that contains
    ``scripts/launcher.py``, or ``None`` when the cache root doesn't
    exist or no version directory qualifies.
    """
    root = cache_root or _PLUGIN_CACHE_ROOT
    if not root.is_dir():
        return None
    candidates = [
        d for d in root.iterdir() if d.is_dir() and (d / _LAUNCHER_RELATIVE).is_file()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda d: _parse_version(d.name))


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of one ``run_schema_migration`` attempt.

    ``plugin_found`` False means no installed Cortex plugin was
    discoverable at all — ``exit_code``/``stderr``/``timed_out`` are
    then meaningless (no subprocess ran). Otherwise ``exit_code == 0``
    means the migrate entry point reported the schema up to date or
    successfully migrated; any other value (including ``None`` for a
    timeout) is a failure, with the reason in ``stderr``.
    """

    plugin_found: bool
    exit_code: int | None
    stderr: str
    timed_out: bool


def run_schema_migration(
    database_url: str,
    *,
    timeout_s: float = DEFAULT_MIGRATION_TIMEOUT_S,
    plugin_root: Path | None = None,
) -> MigrationResult:
    """Invoke ``mcp_server.migrate`` in the installed Cortex plugin.

    Precondition: ``database_url`` is the same DSN cortex-viz already
    reads its store from (schema state must match what the preflight
    just checked). Postcondition: no in-process import of Cortex code
    occurs — the migration runs in a fresh subprocess so an old plugin
    lacking ``mcp_server.migrate`` fails as a subprocess exit code, not
    an exception here.
    """
    root = plugin_root or find_cortex_plugin_root()
    if root is None:
        return MigrationResult(
            plugin_found=False, exit_code=None, stderr="", timed_out=False
        )
    launcher = root / _LAUNCHER_RELATIVE
    env = {**os.environ, "DATABASE_URL": database_url, "CLAUDE_PLUGIN_ROOT": str(root)}
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(launcher), _MIGRATE_MODULE],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return MigrationResult(
            plugin_found=True,
            exit_code=None,
            stderr=f"migration timed out after {timeout_s:.0f}s",
            timed_out=True,
        )
    return MigrationResult(
        plugin_found=True,
        exit_code=proc.returncode,
        stderr=proc.stderr or "",
        timed_out=False,
    )


__all__ = [
    "DEFAULT_MIGRATION_TIMEOUT_S",
    "MigrationResult",
    "find_cortex_plugin_root",
    "run_schema_migration",
]
