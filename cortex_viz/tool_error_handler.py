"""Friendly error handling for cortex-viz MCP tool calls.

Minimal counterpart to Cortex's tool_error_handler: wraps a handler so MCP
clients never see raw tracebacks, and offloads the (sync-DB-calling) handler
body to a worker thread so it does not block the event loop. cortex-viz does
NOT bundle Cortex's per-tool admission semaphore or Prometheus metrics — the
viz exposes a handful of read/launch tools, not the full memory surface, so
that machinery is out of scope here.

Contract (Liskov, mirrors Cortex): handler_fn is an async callable returning a
dict; safe_handler returns a dict[str, Any] — never a JSON string (FastMCP 2.x
validates structured content against output_schema and rejects strings).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine


def _run_coroutine_on_thread(
    handler_fn: Callable[..., Coroutine[Any, Any, dict]], args: dict[str, Any]
) -> dict:
    """Run an async handler to completion on a fresh event loop.

    Used under asyncio.to_thread so the handler's synchronous DB calls run on a
    worker thread with its own loop, never the server's main loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(handler_fn(args))
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def safe_handler(
    handler_fn: Callable[..., Coroutine[Any, Any, dict]],
    args: dict[str, Any],
    tool_name: str | None = None,
) -> dict[str, Any]:
    """Call a handler, returning its dict; catch errors into a dict response.

    On success returns the handler's dict verbatim ({} if it returns None). On
    any exception returns {"error": <type>, "message": <str>} — no traceback.
    """
    try:
        result = await asyncio.to_thread(_run_coroutine_on_thread, handler_fn, args)
        return result if result is not None else {}
    except Exception as exc:  # noqa: BLE001 — surface as data, never a traceback
        return {
            "error": type(exc).__name__,
            "message": str(exc),
            "tool": tool_name,
        }
