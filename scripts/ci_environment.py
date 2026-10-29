#!/usr/bin/env python3
"""The environment cell of ci.json: the launcher starts and the Trace view answers.

On a bare runner (``CI_OS`` linux, macos or windows; no PostgreSQL), run the
standalone server exactly as the plugin manifest does, through
``scripts/launcher.py`` (which installs the runtime dependencies itself),
with ``--no-db``, and require ``/api/capabilities`` to answer ``trace-only``
with the Trace view on. Exit 1 on any other outcome, with the server's output.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# source: scripts/launcher.py _ensure_deps pip-installs the nine runtime
# packages (psycopg binary, numpy, igraph, leidenalg...) before the server
# binds; five minutes covers that download on a GitHub-hosted runner.
TIMEOUT_S = 300


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def capabilities(port: int) -> dict | None:
    url = f"http://127.0.0.1:{port}/api/capabilities"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return json.load(resp)
    except (OSError, ValueError):  # refused, reset, or a non-JSON body while booting
        return None


def main() -> int:
    port = free_port()
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "launcher.py"),
        "cortex_viz.server.http_standalone",
        "--type",
        "unified",
        "--port",
        str(port),
        "--no-db",
    ]
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
    proc = subprocess.Popen(
        cmd,
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + TIMEOUT_S
    caps = None
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            caps = capabilities(port)
            if caps is not None:
                break
            time.sleep(1)
    finally:
        proc.terminate()
        try:
            output = proc.communicate(timeout=15)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            output = proc.communicate()[0]
    print(output[-4000:])
    ci_os = os.environ.get("CI_OS", sys.platform)
    if caps is None:
        print(f"environment: no answer from /api/capabilities on port {port} ({ci_os})")
        return 1
    print(f"capabilities: {json.dumps(caps)}")
    trace_on = caps.get("views", {}).get("trace") is True
    if caps.get("mode") != "trace-only" or not trace_on:
        print("environment: the Trace view is not served in trace-only mode")
        return 1
    print(f"environment: launcher started on {ci_os}, Trace view answers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
