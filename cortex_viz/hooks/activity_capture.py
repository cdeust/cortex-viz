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


def _positive_port(value: object) -> int | None:
    """A usable TCP port from an untrusted value, or None when it names none.

    ``object`` is the honest input type: both sources below are unvalidated
    (a JSON payload on disk, an environment string). ``int`` raises ValueError
    on non-numeric text and TypeError on a value it cannot convert at all;
    each caller names those in its own handler.
    """
    port = int(value or 0)
    return port if port > 0 else None


def _registry_path() -> Path:
    """Where the running server records itself.

    Deliberately a second, independent copy of the location
    ``cortex_viz.server.viz_instance.instance_path`` writes: this module is
    stdlib-only by contract, so it cannot import the writer. The two are held
    in agreement by a test, not by a shared import — a drift in either would
    otherwise leave discovery silently falling back to the default port.
    """
    return Path.home() / ".cache" / "cortex" / "viz-server.json"


def _registry_port() -> int | None:
    """The port the instance registry records, or None when it records none.

    Named failure modes, each meaning the same thing — no recorded server:
    the file is absent or unreadable (OSError), is not JSON or holds a
    non-numeric port (ValueError), decodes to something other than a mapping
    so it has no ``get`` (AttributeError), or carries a port of a type ``int``
    refuses (TypeError). Returning None here is not a swallowed error: the
    hook's hard contract forbids raising, and the caller has two further
    candidate sources to try.
    """
    try:
        registry = json.loads(_registry_path().read_text())
        return _positive_port(registry.get("port"))
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _env_port() -> int | None:
    """The port ``CORTEX_VIZ_PORT`` names, or None when unset or non-numeric.

    A hand-typed or shell-interpolated value is the one named failure mode
    (ValueError); an unusable override must not stop the launcher default from
    being tried.
    """
    try:
        return _positive_port(os.environ.get("CORTEX_VIZ_PORT"))
    except ValueError:
        return None


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
    for port in (_registry_port(), _env_port(), _DEFAULT_PORT):
        if port is not None and port not in ports:
            ports.append(port)
    return [f"http://127.0.0.1:{port}/api/activity" for port in ports]


def _discover_url() -> str:
    """The endpoint capture posts to first.

    Total, never empty: ``_candidate_urls`` always ends with the launcher
    default, so there is always an endpoint to name.
    """
    return _candidate_urls()[0]


def _report_endpoint() -> None:
    """Name the endpoint capture would post to, on stderr.

    Reached only when stdin is a terminal — a human ran the hook by hand to
    check it. A host-invoked hook always receives its event on a pipe, so this
    never writes during a session. Without it an interactive run is completely
    silent, which is indistinguishable from discovery being broken.
    """
    print(f"cortex-viz activity endpoint: {_discover_url()}", file=sys.stderr)


def main() -> None:
    # No stdin (interactive run) → nothing to capture; report what discovery
    # resolved to instead, so the run is not silent.
    try:
        if sys.stdin.isatty():
            _report_endpoint()
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
