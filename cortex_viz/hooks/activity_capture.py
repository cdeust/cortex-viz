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


def _discover_url() -> str | None:
    """Resolve the live viz server's ``/api/activity`` URL.

    Precedence: explicit ``CORTEX_VIZ_URL`` env → the instance registry the
    server writes (``~/.cache/cortex/viz-server.json``, pid+port) → the
    ``CORTEX_VIZ_PORT`` env → the dev default 3503. Returns None only if every
    source is unusable (then capture silently no-ops).
    """
    env_url = os.environ.get("CORTEX_VIZ_URL")
    if env_url:
        return env_url.rstrip("/") + "/api/activity"
    port = None
    try:
        reg = json.loads(
            (Path.home() / ".cache" / "cortex" / "viz-server.json").read_text()
        )
        port = int(reg.get("port") or 0) or None
    except (OSError, ValueError, KeyError, TypeError):
        port = None
    if not port:
        try:
            port = int(os.environ.get("CORTEX_VIZ_PORT") or 0) or None
        except ValueError:
            port = None
    port = port or 3503
    return f"http://127.0.0.1:{port}/api/activity"


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

    url = _discover_url()
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(event).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=_TIMEOUT_S).read()
    except Exception:
        # Server down, slow, or unreachable — capture is best-effort and must
        # never affect the session. Swallow everything.
        return


if __name__ == "__main__":
    main()
