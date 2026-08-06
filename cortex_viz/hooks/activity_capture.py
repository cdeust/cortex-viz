#!/usr/bin/env python3
"""Claude Code hook → live session-activity capture for the viz bridge MCP.

Registered on PostToolUse (every tool, including ``mcp__*`` MCP calls, ``Skill``
slash-commands, ``Bash`` terminal commands, file Read/Edit/Write) and on
UserPromptSubmit (prompts). On each fire it reads the hook event from stdin,
stamps it with the event type (argv[1]) + a timestamp, discovers the running
viz server's port, and fire-and-forget POSTs it to ``/api/activity``.

Hard contract (same as every Cortex hook): NEVER block, NEVER raise, NEVER slow
the session. TTY-safe, ~0.5 s timeout, all errors swallowed, always exit 0.
Stdlib only — runs even when the cortex_viz package is not importable.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_TIMEOUT_S = 0.5
_DEFAULT_PORT = 3458


def _candidate_urls() -> list[str]:
    """Ordered activity endpoints within the hook's one shared time budget.

    Precedence: explicit ``CORTEX_VIZ_URL`` env → the instance registry the
    server writes (``~/.cache/cortex/viz-server.json``, pid+port) → the
    ``CORTEX_VIZ_PORT`` env → the launcher default 3458. The registry is a
    discovery hint, not proof of liveness: a server can exit after writing it,
    so ``main`` tries the remaining candidates when connection refusal is
    immediate. Duplicate endpoints are removed without changing precedence.
    """
    env_url = os.environ.get("CORTEX_VIZ_URL")
    if env_url:
        return [env_url.rstrip("/") + "/api/activity"]
    ports: list[int] = []
    try:
        reg = json.loads(
            (Path.home() / ".cache" / "cortex" / "viz-server.json").read_text()
        )
        port = int(reg.get("port") or 0)
        if port:
            ports.append(port)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        configured = int(os.environ.get("CORTEX_VIZ_PORT") or 0)
        if configured:
            ports.append(configured)
    except ValueError:
        pass
    ports.append(_DEFAULT_PORT)
    seen: set[int] = set()
    return [
        f"http://127.0.0.1:{port}/api/activity"
        for port in ports
        if not (port in seen or seen.add(port))
    ]


def _discover_url() -> str | None:
    """Backward-compatible first endpoint for diagnostics/tests."""
    urls = _candidate_urls()
    return urls[0] if urls else None


def main() -> None:
    # No stdin (interactive run) → nothing to capture.
    try:
        if sys.stdin.isatty():
            return
        raw = sys.stdin.read().strip()
    except Exception:
        return
    if not raw:
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return

    event.setdefault("event_type", sys.argv[1] if len(sys.argv) > 1 else "PostToolUse")
    event.setdefault("ts", time.time())

    payload = json.dumps(event).encode("utf-8")
    deadline = time.monotonic() + _TIMEOUT_S
    for url in _candidate_urls():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=remaining).read()
            return
        except Exception:
            # A stale registry commonly refuses immediately; use the remaining
            # shared budget to try the configured/default launcher port. A slow
            # endpoint consumes the budget and therefore never delays the host
            # beyond the original 0.5 s hard contract.
            continue


if __name__ == "__main__":
    main()
