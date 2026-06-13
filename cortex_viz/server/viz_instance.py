"""Single-instance registry for the standalone visualization server.

Why this exists (observed 2026-06-12): every ``open_visualization``
call killed port 3458 and spawned a fresh server. SIGTERM is
asynchronous — the old process often still held the socket when the
new one tried to bind, so ``_bind_server`` fell back to an
OS-assigned ephemeral port. Once an instance lives on an ephemeral
port no later call can find it: probes of 3458 miss, another server
is spawned, and a fully built 135k-node graph (minutes of work) is
discarded while instances leak on random ports (:56746, :57167).

The fix is a tiny registry file: the standalone server records
``{pid, port, started_at}`` right after binding. Launchers read it to

  * discover the live instance whatever port it actually bound,
  * reuse it when its code is not older than the dev source
    (``started_at`` >= newest source mtime — the always-fresh policy
    only requires a respawn when the source actually changed), and
  * kill exactly that pid and WAIT for it to exit before respawning,
    closing the bind race that caused the ephemeral-port fallback.

Stdlib only — the launcher-side consumers (``visualize_bootstrap``)
must stay importable from a bare checkout.
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
import urllib.request
from pathlib import Path

# Source subtrees whose mtimes define "the code the server runs".
# Mirrors the launcher sync set: server code + served UI assets.
_FRESHNESS_SUBTREES: tuple[str, ...] = ("mcp_server", "ui")

# Directories that get fresh mtimes as a side effect of merely RUNNING
# the server (bytecode caches) or that are not part of the served code.
_SKIP_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".git", "node_modules", ".pytest_cache"}
)


def instance_path() -> Path:
    """Registry file location — next to the graph snapshot cache."""
    return Path.home() / ".cache" / "cortex" / "viz-server.json"


def write_instance(port: int) -> None:
    """Record this process as the live viz server. Atomic replace so a
    concurrent reader never sees a torn file. Best-effort: a registry
    write failure must never block server startup."""
    payload = {
        "pid": os.getpid(),
        "port": int(port),
        "started_at": time.time(),
    }
    path = instance_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError:
        pass


def read_instance() -> dict | None:
    """Return the registered instance iff its pid is still alive.

    A dead pid (server exited via the idle watchdog, crash, or kill)
    invalidates the registry — callers treat ``None`` as "no instance".
    """
    try:
        data = json.loads(instance_path().read_text())
        pid = int(data["pid"])
        port = int(data["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _pid_alive(pid):
        return None
    data["pid"], data["port"] = pid, port
    return data


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A terminated CHILD of this process stays visible to kill(pid, 0)
    # as a zombie until reaped — which would stall kill_and_wait for
    # its full timeout when a launcher kills a server it spawned
    # itself. Reap-if-ours; not-our-child raises ChildProcessError.
    try:
        done, _status = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    return True


def probe(port: int, timeout: float = 1.0) -> bool:
    """True when an HTTP server answers on ``port``. Pid-alive alone is
    not enough — the pid could be recycled by an unrelated process."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=timeout
        ) as resp:
            resp.read(64)
        return True
    except Exception:
        return False


def newest_source_mtime(src_root: Path) -> float:
    """Newest file mtime under the source subtrees the server runs from.

    ``__pycache__`` (and friends) are excluded: importing a module
    refreshes its ``.pyc`` mtime, which would mark every RUNNING server
    permanently stale against its own source tree.
    """
    newest = 0.0
    for sub in _FRESHNESS_SUBTREES:
        root = src_root / sub
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                if fname.endswith(".pyc"):
                    continue
                try:
                    mtime = os.stat(os.path.join(dirpath, fname)).st_mtime
                except OSError:
                    continue
                if mtime > newest:
                    newest = mtime
    return newest


def is_current(instance: dict, src_root: Path) -> bool:
    """True when the registered server started AFTER the last source
    edit — i.e. it already runs the current code and the always-fresh
    policy does not require a respawn."""
    try:
        started_at = float(instance["started_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return started_at >= newest_source_mtime(src_root)


def reusable_instance(src_root: Path | None) -> dict | None:
    """The registered instance when it is alive, answering HTTP, and
    (if a dev source is given) running code at least as new as it.
    ``None`` means the caller should kill + respawn."""
    inst = read_instance()
    if inst is None or not probe(inst["port"]):
        return None
    if src_root is not None and not is_current(inst, src_root):
        return None
    return inst


def kill_and_wait(pid: int, timeout: float = 5.0) -> bool:
    """SIGTERM ``pid`` and wait for it to actually exit (SIGKILL after
    ``timeout``). Returns True when the process is gone.

    Waiting is the point: spawning while the old listener still holds
    the socket is exactly the bind race that produced the
    ephemeral-port fallback.
    """
    if not _pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_alive(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return not _pid_alive(pid)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return False
