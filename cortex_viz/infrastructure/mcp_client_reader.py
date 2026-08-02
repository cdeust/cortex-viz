"""JSON-RPC reader loop for :class:`MCPClient`.

Split out of ``mcp_client`` (§4 class-size limit), following the same
delegation pattern as ``mcp_client_stderr``: the client keeps the thin
method, the body lives here. The functions take the client so the
pending-future map and the child process stay owned by one object.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from cortex_viz.errors import McpConnectionError

# Stream failures that mean "the transport is gone", as opposed to a
# malformed payload the loop can skip. Most often a single response line
# exceeded the configured ``limit`` bytes.
_STREAM_FAILURES = (
    asyncio.LimitOverrunError,
    asyncio.IncompleteReadError,
    ConnectionResetError,
    BrokenPipeError,
)


def _decoded_text_block(text: str) -> Any:
    """A tool result's text block as JSON when it parses, else verbatim."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _resolve_pending(pending: dict, msg: dict) -> None:
    """Deliver one JSON-RPC response to the future waiting on its id."""
    msg_id = msg.get("id")
    if msg_id is None or msg_id not in pending:
        return
    future = pending.pop(msg_id)
    if future.done():
        return
    if msg.get("error"):
        future.set_exception(
            McpConnectionError(msg["error"].get("message", "Unknown error"))
        )
    else:
        future.set_result(msg.get("result"))


def _dispatch_line(pending: dict, decoded: str) -> None:
    """Route one decoded stdout line, tolerating non-JSON noise.

    A bad payload from the upstream is recoverable — it is logged and the
    loop continues rather than tearing the connection down.
    """
    try:
        msg = json.loads(decoded)
    except (json.JSONDecodeError, ValueError):
        print(f"[mcp-client] non-JSON line dropped: {decoded[:200]}", file=sys.stderr)
        return
    _resolve_pending(pending, msg)


async def _pump(client) -> None:
    """Read framed responses until the child closes stdout."""
    while True:
        line = await client._proc.stdout.readline()  # type: ignore  # noqa: SLF001
        if not line:
            # EOF — child closed stdout. Fall through to fail pending
            # futures so callers do not block forever.
            return
        decoded = line.decode("utf-8").strip()
        if not decoded or decoded.startswith("Content-Length"):
            continue
        _dispatch_line(client._pending, decoded)  # noqa: SLF001


def _fail_pending(client, terminal_exc: BaseException | None) -> None:
    """Wake every pending caller once the reader has exited.

    Without this, ``_send``'s ``await future`` blocks forever (deadlock
    observed on long upstream responses).
    """
    cause = type(terminal_exc).__name__ if terminal_exc else "EOF"
    for fut in list(client._pending.values()):  # noqa: SLF001
        if not fut.done():
            fut.set_exception(
                McpConnectionError(f"Upstream reader terminated: {cause}")
            )
    client._pending.clear()  # noqa: SLF001


async def read_loop(client) -> None:
    """The client's stdout reader task.

    Tracks the terminal cause so all pending futures get a real error
    instead of hanging forever when the reader exits.
    """
    terminal_exc: BaseException | None = None
    try:
        await _pump(client)
    except asyncio.CancelledError:
        terminal_exc = None
    except _STREAM_FAILURES as exc:
        # Surface the stream-level failure as the terminal cause for every
        # pending future, so callers see a clear McpConnectionError.
        terminal_exc = exc
        print(
            f"[mcp-client] reader stream error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        terminal_exc = exc
        print(
            f"[mcp-client] reader unexpected error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    finally:
        # Reader is exiting → the child's stdout is gone, so the connection
        # is dead. Mark it disconnected at the ROOT here (not only in
        # close()) so the pool's ``existing.connected`` check discards this
        # client and reconnects on the next call. Without this the flag
        # stayed True after a child crash and the pool handed back a dead
        # client, whose next stdin write raised ``ConnectionResetError:
        # Connection lost`` — the fast failure seen on every ingest retry.
        # source: ingest_codebase ConnectionResetError RCA 2026-06-09.
        client._connected = False  # noqa: SLF001
        _fail_pending(client, terminal_exc)
