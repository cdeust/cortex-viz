"""Upstream-stderr capture for the MCP stdio client.

Split out of ``mcp_client.py`` (was 533 lines) to respect the 500-line
file limit. Persists each upstream MCP server's stderr to a per-server
log file and mirrors it to this process's stderr. Pure infrastructure —
the codec/lifecycle stays in ``mcp_client``; these helpers take the
client (or its config) explicitly so there is no import cycle.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any


def open_stderr_log(config: dict):
    """Open a per-server stderr log file under ~/.cache/cortex/mcp-logs/.

    Persists upstream MCP stderr (e.g. ai-architect-mcp indexer progress)
    for post-hoc investigation. Returns None on any error — logging
    failure must not break the connection.
    """
    import os
    import pathlib

    try:
        base = pathlib.Path.home() / ".cache" / "cortex" / "mcp-logs"
        base.mkdir(parents=True, exist_ok=True)
        raw = config.get("command") or "unknown"
        stem = raw.split("/")[-1] or "unknown"
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem)
        pid = os.getpid()
        return open(base / f"{safe}.{pid}.log", "a", encoding="utf-8")
    except Exception:
        return None


async def stderr_loop(client: Any) -> None:
    """Drain the child's stderr, mirroring to this process + the log file."""
    log_fh = open_stderr_log(client._config)
    try:
        while True:
            line = await client._proc.stderr.readline()  # type: ignore
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip()
            print(
                f"[mcp-client] {client._config['command']}: {decoded}",
                file=sys.stderr,
            )
            if log_fh is not None:
                try:
                    log_fh.write(decoded + "\n")
                    log_fh.flush()
                except Exception:
                    # The line was already mirrored to this process's stderr above; losing the
                    # log-file copy must not kill the drain loop.
                    pass
    except asyncio.CancelledError:
        # Cooperative cancellation of the stderr drain.
        pass
    except Exception:
        # The drain is best-effort diagnostics; its failure must not surface as an
        # error from the subprocess it is only observing.
        pass
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except Exception:
                # Teardown of the log file handle.
                pass
