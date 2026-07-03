"""Fresh-from-disk bootstrap for ``cortex-visualize``.

The MCP plugin runs as a long-lived Python process: once it imports
``handlers/open_visualization`` and ``server/http_launcher`` there is no
cheap way to pick up new code on disk without reloading the whole
module tree. That meant every ``cortex-visualize`` call in a long
session kept firing the handler snapshot the plugin had loaded on
startup — auto-sync and live-stream fixes stayed invisible until the
user restarted Claude Code.

This file is the fix: a minimal script that is always re-parsed from
disk when the handler ``subprocess.Popen``s it. It takes care of:

  1. Locating the Cortex dev checkout (same detection the handler uses).
  2. Rsyncing the dev source onto every known plugin / UV cache root.
  3. Killing any stale HTTP server on port 3458.
  4. Spawning ``http_standalone.py --type unified --port 3458`` from
     the just-synced package path.

Because step 4 is a separate Python process that imports from the
freshly-synced cache, it always runs the current code. The long-lived
MCP plugin process just invokes this helper via subprocess and returns
the URL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PORT = 3458


def _is_cortex_root(p: Path) -> bool:
    return (
        p.is_dir()
        and (p / "mcp_server").is_dir()
        and (p / "ui" / "unified-viz.html").is_file()
    )


def _find_dev_source() -> Path | None:
    """Locate the dev source. See GHSA-gvpp-v77h-5w8g gating in
    ``mcp_server/handlers/open_visualization._find_dev_source`` — the
    bootstrap script inherits the parent process environment, so any
    untrusted env var consulted here would re-open the same hole the
    handler closes.

    ``CLAUDE_PROJECT_DIR`` is therefore NOT consulted. ``CORTEX_DEV_ROOT``
    requires the explicit ``CORTEX_DEV_SOURCE_SYNC=1`` opt-in flag
    (exact value ``"1"``). The ``~/Developments/Cortex``
    fallback is preserved (user-controlled filesystem).
    """
    if os.environ.get("CORTEX_DEV_SOURCE_SYNC") == "1":
        v = os.environ.get("CORTEX_DEV_ROOT")
        if v and _is_cortex_root(Path(v)):
            return Path(v)
    # New ecosystem layout (anthropic-partnership/, 2026-06) first, then
    # the legacy top-level ``~/Developments/Cortex`` checkout.
    for default in (
        Path.home() / "Developments" / "anthropic-partnership" / "Cortex",
        Path.home() / "Developments" / "Cortex",
    ):
        if _is_cortex_root(default):
            return default
    return None


def _cache_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    for d in (
        home / ".claude" / "plugins" / "cache" / "cortex-plugins" / "cortex"
    ).glob("*"):
        if d.is_dir():
            roots.append(d)
    for name in ("cdeust-cortex", "cortex-plugins"):
        p = home / ".claude" / "plugins" / "marketplaces" / name
        if p.is_dir():
            roots.append(p)
    # EVERY uv archive that contains an ``mcp_server`` package — uv
    # hashes env + wheel-set so different plugin versions end up in
    # different archive roots. If we only rsync one, whichever archive
    # happens to be the resolved plugin env at launch runs stale code.
    #
    # Two archive layouts exist in the wild: nested
    # (``<hash>/lib/python*/site-packages/mcp_server``) for full venv
    # installs, and flat (``<hash>/mcp_server``) for editable / wheel
    # installs. We must hit BOTH or the plugin loads stale handlers.
    arch_root = home / ".cache" / "uv" / "archive-v0"
    for arch in arch_root.glob("*/lib/python*/site-packages"):
        if (arch / "mcp_server").is_dir():
            roots.append(arch)
    for arch in arch_root.glob("*"):
        if arch.is_dir() and (arch / "mcp_server").is_dir():
            roots.append(arch)
    return roots


# Subtrees that must propagate from the dev source into every plugin /
# marketplace cache so the running plugin picks up code, UI, prompts,
# slash-commands, and lifecycle hooks. Without this list, edits to
# ``agents/cortex-wiki-groomer.md`` or new skill files would land in
# the repo but never reach the plugin Claude Code actually loads.
#
# 2026-05-18 (user direction: "update marketplace cache sync"): added
# ``agents``, ``skills``, ``commands``, and ``hooks.json`` to the
# previously code+ui-only sync. The groomer policy change earlier this
# turn was invisible to the live plugin until this expansion landed.
_SYNC_SUBTREES: tuple[str, ...] = (
    "mcp_server",
    "ui",
    "agents",
    "skills",
    "commands",
    "scripts",
)

# Single-file artefacts that also need to propagate (Claude Code reads
# these from the plugin root).
_SYNC_FILES: tuple[str, ...] = (
    "hooks.json",
    "plugin.json",
    "CLAUDE.md",
    "README.md",
)


def _sync(src: Path) -> int:
    rsync = shutil.which("rsync")
    count = 0
    for dst in _cache_roots():
        for sub in _SYNC_SUBTREES:
            s = src / sub
            d = dst / sub
            if not s.is_dir():
                continue
            try:
                if rsync:
                    subprocess.run(
                        [rsync, "-a", "--delete", f"{s}/", f"{d}/"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    if d.exists():
                        shutil.rmtree(d, ignore_errors=True)
                    shutil.copytree(s, d, symlinks=True)
            except Exception:
                continue
        for fname in _SYNC_FILES:
            s = src / fname
            d = dst / fname
            if not s.is_file():
                continue
            try:
                shutil.copy2(s, d)
            except Exception:
                continue
        count += 1
    return count


def _viz_instance_mod(src: Path):
    """Import ``viz_instance`` from the dev checkout this script lives
    in. The bootstrap is always re-parsed from disk, so importing its
    sibling module preserves the fresh-from-disk property."""
    sys.path.insert(0, str(src))
    from cortex_viz.server import viz_instance

    return viz_instance


def _kill_stale(src: Path, vi) -> None:
    """Terminate the registered instance (whatever port it bound) plus
    any squatter on the well-known port, WAITING for exit. Spawning
    before the old listener releases the socket is the bind race that
    pushed servers onto ephemeral ports."""
    inst = vi.read_instance()
    if inst is not None:
        vi.kill_and_wait(inst["pid"])
    try:
        out = (
            subprocess.check_output(
                ["lsof", "-t", "-i", f":{PORT}"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return
    for pid_s in out.splitlines():
        try:
            vi.kill_and_wait(int(pid_s.strip()))
        except Exception:
            pass


def _spawn_server(src: Path) -> None:
    """Spawn ``http_standalone.py`` from the freshly-synced source so
    the new server process always runs the latest code."""
    standalone = src / "mcp_server" / "server" / "http_standalone.py"
    if not standalone.is_file():
        return
    env = {**os.environ}
    existing = env.get("PYTHONPATH", "")
    pkg_root = str(src)
    if pkg_root not in existing:
        env["PYTHONPATH"] = f"{pkg_root}:{existing}" if existing else pkg_root
    subprocess.Popen(
        [
            sys.executable,
            str(standalone),
            "--type",
            "unified",
            "--port",
            str(PORT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )


def _extras_available(src: Path) -> bool:
    """Probe the standalone-server's Python for the viz-tile extras.

    The MCP handler's URL/message logic is cached in ``sys.modules`` of
    the long-lived plugin process and may be stale; this helper makes
    the bootstrap (always re-parsed from disk) authoritative about
    whether the dense tilemap path is reachable. ``standalone`` runs
    under whatever Python we use here, so importing igraph/datashader
    in *this* process is the right test.
    """
    try:
        import importlib

        for mod in ("igraph", "datashader", "pyarrow", "PIL"):
            importlib.import_module(mod)
        return True
    except Exception:
        return False


def _drive_prepare_then_render(base: str, timeout_s: int = 600) -> str | None:
    """Wait for graph baseline, fire /api/recompute_layout, return the
    force-directed graph URL on success.

    Previously this opened the tilemap (Datashader CPU-layout renderer)
    which doesn't share the skeleton-first / live-SSE-stream / phase-
    loader path. The force-directed renderer (``?viz=force``) does:
    skeleton_ready in ~1 s, live batches via /api/graph/events, and the
    per-phase loader (/api/graph/progress + /api/graph/phase) as the
    only graph-delivery path. See commits 0204da8, d9d8a98,
    972bb9a, f21e255.

    Idempotent — recompute_layout skips when fingerprint matches PG.
    Runs in a daemon thread so the bootstrap script returns immediately;
    the browser tab self-heals via the phase poller + SSE subscriber
    that the force renderer already wires up.
    """
    import json as _json
    import threading as _thr
    import time as _time
    import urllib.request as _ur
    import webbrowser as _wb

    def _run() -> None:
        try:
            _ur.urlopen(f"{base}/api/graph", timeout=5).read(1024)
        except Exception:
            pass
        deadline = _time.monotonic() + timeout_s
        while _time.monotonic() < deadline:
            try:
                with _ur.urlopen(f"{base}/api/graph/progress", timeout=5) as r:
                    p = _json.loads(r.read().decode("utf-8"))
                if p.get("baseline_ready") or p.get("full_ready"):
                    break
            except Exception:
                pass
            _time.sleep(2)
        try:
            _ur.urlopen(f"{base}/api/recompute_layout", timeout=timeout_s).read()
        except Exception:
            pass
        try:
            _wb.open(f"{base}/?viz=force")
        except Exception:
            pass

    _thr.Thread(target=_run, name="cortex-prepare", daemon=True).start()
    return f"{base}/?viz=force"


def _wait_for_instance(vi, spawned_after: float, timeout: float = 15.0):
    """Wait for the just-spawned server to register itself + answer
    HTTP. Returns the instance dict (carrying the ACTUAL bound port,
    which may differ from PORT on bind fallback) or ``None``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inst = vi.read_instance()
        if (
            inst is not None
            and float(inst.get("started_at", 0)) >= spawned_after
            and vi.probe(inst["port"])
        ):
            return inst
        time.sleep(0.25)
    return None


def main() -> None:
    src = _find_dev_source()
    if src is None:
        print("no_dev_source", flush=True)
        return
    vi = _viz_instance_mod(src)
    synced = _sync(src)

    # Reuse a live instance running current code instead of discarding
    # its (possibly minutes-long) completed graph build. The always-
    # fresh policy only demands a respawn when the source CHANGED since
    # the server started — viz_instance.is_current checks exactly that.
    inst = vi.reusable_instance(src)
    if inst is not None:
        print(
            f"ok reused pid={inst['pid']} synced={synced} "
            f"url=http://127.0.0.1:{inst['port']}/?viz=force",
            flush=True,
        )
        return

    _kill_stale(src, vi)
    spawned_after = time.time()
    _spawn_server(src)
    new_inst = _wait_for_instance(vi, spawned_after)
    base = (
        f"http://127.0.0.1:{new_inst['port']}"
        if new_inst is not None
        else f"http://127.0.0.1:{PORT}"
    )
    if _extras_available(src):
        target = _drive_prepare_then_render(base)
        print(
            f"ok synced={synced} url={target} extras=ok",
            flush=True,
        )
    else:
        # Explicit ?viz=force so the HTML's inline auto-redirect probe
        # doesn't bounce a bare URL to the tilemap default.
        print(
            f"ok synced={synced} url={base}/?viz=force extras=missing",
            flush=True,
        )


if __name__ == "__main__":
    main()
