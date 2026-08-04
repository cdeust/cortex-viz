"""Child-process spawn + command allowlisting for :class:`MCPClient`.

Split out of ``mcp_client`` (§4 class-size limit), following the same
delegation pattern as ``mcp_client_stderr`` and ``mcp_client_reader``.
The allowlist lives here because it is the security boundary of this
step: nothing else in the client decides what may be executed.
"""

from __future__ import annotations

import asyncio
import os
import shutil

from cortex_viz.errors import McpConnectionError
from cortex_viz.infrastructure.upstream_identity import ALLOWED_UPSTREAM_COMMANDS

# Allowlisted MCP server commands. Only these binaries may be spawned.
# Config-supplied commands are validated against this list to prevent
# command injection (CodeQL py/command-line-injection, CWE-78).
ALLOWED_COMMANDS = frozenset(
    {
        "node",
        "npx",
        "python",
        "python3",
        "cortex",
        "mcp-server",
        # The codebase-intelligence server ships a compiled Rust MCP binary;
        # the bridge resolves it from installed_plugins.json and invokes it
        # directly (not via node). Names come from upstream_identity so this
        # allowlist cannot drift from the resolver that feeds it.
        # source: ap_bridge._resolve_command.
        *ALLOWED_UPSTREAM_COMMANDS,
    }
)

# Stream-buffer cap per JSON-RPC frame. Sized for the L6 path, where AP
# responses with 100k+ symbols + edges legitimately exceed 100MB. Keep an
# upper bound large enough that we never cap real workloads; OS-level
# subprocess pipe buffering still provides backpressure.
_LINE_LIMIT = 1024 * 1024 * 1024  # 1 GB


def _resolve_command(raw_command: str, extra_allowed: set[str]) -> str:
    """Validate ``raw_command`` against the allowlist and resolve its path.

    CWE-78 mitigation. Matching is on the basename so an absolute path to
    an allowed binary passes, and resolution goes through ``shutil.which``
    so a PATH entry cannot substitute a different binary silently.
    """
    allowed = ALLOWED_COMMANDS | extra_allowed
    base_cmd = raw_command.split("/")[-1] if "/" in raw_command else raw_command
    if base_cmd not in allowed:
        raise McpConnectionError(
            f"Command '{raw_command}' not in allowed list: {sorted(allowed)}"
        )
    return shutil.which(raw_command) or raw_command


async def spawn_process(
    config: dict,
    connect_timeout_ms: int,
    extra_allowed: set[str] | None = None,
) -> asyncio.subprocess.Process:
    """Spawn the child MCP server process.

    Security: the command must be in the allowlist. Args are passed as a
    list (no shell=True). The environment is merged from os.environ +
    config, not constructed from user input.
    """
    args = config.get("args") or []
    cwd = config.get("cwd")
    env = config.get("env")
    merged_env = {**os.environ, **(env or {})}
    command = _resolve_command(config["command"], extra_allowed or set())

    try:
        return await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=merged_env,
                limit=_LINE_LIMIT,
            ),
            timeout=connect_timeout_ms / 1000,
        )
    except asyncio.TimeoutError as e:
        raise McpConnectionError(
            f"Connect timeout after {connect_timeout_ms}ms",
            {"command": command, "args": args},
        ) from e
    except Exception as e:
        raise McpConnectionError(
            f"Failed to spawn: {e}",
            {"command": command, "args": args},
        ) from e
