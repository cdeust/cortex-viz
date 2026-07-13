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
import subprocess
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


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness check via OpenProcess + WaitForSingleObject.

    ``os.kill(pid, 0)`` on Windows routes signal 0 to
    ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)``, which requires the
    target to share the caller's console process group. For any
    unrelated/detached pid — including a live one, e.g. this server's
    own detached child — it raises ``OSError [WinError 87]`` instead of
    reporting liveness (see cortex-viz#13, reproduced on Windows 11 /
    Python 3.13.13). ``OpenProcess`` + ``WaitForSingleObject`` is the
    Win32-native "is this pid alive" idiom and works for any pid the
    caller has permission to open, detached or not.
    """
    import ctypes
    import ctypes.wintypes as wintypes

    # source: Microsoft Learn, "Process Security and Access Rights"
    # https://learn.microsoft.com/windows/win32/procthread/process-security-and-access-rights
    synchronize = 0x00100000
    # source: Microsoft Learn, "WaitForSingleObject function" (return value)
    # https://learn.microsoft.com/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject
    wait_object_0 = 0x00000000
    # source: Microsoft Learn, "System Error Codes (0-499)"
    # https://learn.microsoft.com/windows/win32/debug/system-error-codes--0-499-
    error_access_denied = 5

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)

    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        # Access denied implies the pid exists but we can't touch it --
        # mirrors the POSIX PermissionError -> alive branch below.
        return kernel32.GetLastError() == error_access_denied
    try:
        # Non-blocking poll (timeout=0): signaled means the process
        # object fired at exit. GetExitCodeProcess is deliberately not
        # used instead — STILL_ACTIVE == 259 is also a legitimate exit
        # code, making that check ambiguous.
        return kernel32.WaitForSingleObject(handle, 0) != wait_object_0
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
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


def _pids_on_port_posix(port: int) -> list[int]:
    """Pids with an open socket on ``port``, via ``lsof``. Best-effort:
    an empty list means either nothing is listening or ``lsof`` is
    unavailable — callers cannot distinguish the two, which is fine
    since both mean "nothing more to kill"."""
    try:
        out = (
            subprocess.check_output(
                ["lsof", "-t", "-i", f":{port}"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return []
    pids = []
    for pid_s in out.splitlines():
        try:
            pids.append(int(pid_s.strip()))
        except ValueError:
            continue
    return pids


def _pids_on_port_windows(port: int) -> list[int]:
    """Pids LISTENING on ``port``, via ``netstat -ano -p tcp``.

    There is no ``lsof`` on Windows and ``psutil`` is not a runtime
    dependency of this project, so parsing ``netstat`` text output is
    the same "shell out, parse columns" idiom already used for the
    POSIX ``lsof`` case above. netstat's row format (Windows 10/11):
    ``  TCP    127.0.0.1:3458    0.0.0.0:0    LISTENING    1234`` —
    columns are whitespace-separated: proto, local addr, foreign addr,
    state, pid. source: Microsoft Learn, "netstat" command reference
    https://learn.microsoft.com/windows-server/administration/windows-commands/netstat
    """
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            stderr=subprocess.DEVNULL,
        ).decode(errors="ignore")
    except Exception:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_addr, state, pid_s = parts[1], parts[3], parts[-1]
        if state.upper() != "LISTENING" or not local_addr.endswith(needle):
            continue
        try:
            pids.append(int(pid_s))
        except ValueError:
            continue
    return pids


def pids_on_port(port: int) -> list[int]:
    """Pids listening on ``port``, cross-platform. Best-effort: an
    empty list on failure or platform-tool absence, never raises."""
    if os.name == "nt":
        return _pids_on_port_windows(port)
    return _pids_on_port_posix(port)
