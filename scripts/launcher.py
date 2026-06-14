#!/usr/bin/env python3
"""Cross-platform launcher for the cortex-viz MCP server and hooks.

Sets up PYTHONPATH + a self-contained deps dir, ensures the runtime
dependencies are present, then runs the target module over stdio. Works on
Windows (cmd.exe), macOS, and Linux — no shell-specific syntax required.

Usage:
    python3 scripts/launcher.py cortex_viz          # the MCP server (stdio)
    python3 scripts/launcher.py <module>            # any module entry point

Modelled on Cortex's launcher (husk-detection, atomic --target install,
PEP 668 handling), trimmed to cortex-viz's needs: the base MCP runtime plus
the Postgres trio the read-only bridges hard-import. The heavy viz-tile
extras (datashader/igraph/pyarrow) are NOT installed here — they back only
the opt-in ``?viz=tilemap`` renderer, whose startup warmup and request paths
already degrade gracefully when the extra is absent.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _resolve_paths() -> tuple[str, str]:
    """Resolve the plugin root and its persistent deps directory."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root or not Path(plugin_root).is_dir():
        plugin_root = str(Path(__file__).resolve().parent.parent)
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    deps_dir = (
        os.path.join(plugin_data, "deps")
        if plugin_data
        else os.path.join(plugin_root, "deps")
    )
    return plugin_root, deps_dir


# (import_name, pip_spec) — the base MCP runtime + the Postgres trio that
# memory_read/pg_store hard-import at module load. Mirrors pyproject's base
# deps + the ``data`` extra.
_REQUIRED = [
    ("fastmcp", "fastmcp>=2.0.0"),
    ("pydantic", "pydantic>=2.0.0"),
    ("pydantic_settings", "pydantic-settings>=2.0.0"),
    ("numpy", "numpy>=1.24.0"),
    ("psycopg", "psycopg[binary]>=3.1"),
    ("psycopg_pool", "psycopg_pool>=3.2"),
    ("pgvector", "pgvector>=0.3"),
]


def _importable(import_name: str, deps_dir: str) -> bool:
    """True iff ``import_name`` resolves to a REAL package (not a husk).

    An interrupted ``pip install --target`` leaves a directory with no
    ``__init__.py``; Python imports it as a namespace package (``__file__
    is None``) which then shadows any healthy install. Detect that, evict
    the husk from deps_dir, and report missing so the reinstall lands clean.
    """
    import importlib

    try:
        mod = importlib.import_module(import_name)
    except ImportError:
        return False
    if getattr(mod, "__file__", None) is not None:
        return True
    sys.modules.pop(import_name, None)
    husk = os.path.join(deps_dir, import_name)
    if os.path.isdir(husk):
        import shutil

        shutil.rmtree(husk, ignore_errors=True)
    return False


def _pip_install(deps_dir: str, packages: list[str]) -> None:
    """Atomically install ``packages`` into ``deps_dir``, surfacing failures.

    Installs into a sibling temp dir and moves only fully-installed entries
    in, so a mid-install kill never leaves a shadowing husk. Retries with
    ``--break-system-packages`` only when pip reports a PEP 668
    externally-managed interpreter (safe: ``--target`` never touches system
    site-packages).
    """
    import shutil

    tmp_dir = f"{deps_dir}.tmp-{os.getpid()}"
    base = [sys.executable, "-m", "pip", "install", "-q", "--target", tmp_dir, *packages]
    try:
        proc = subprocess.run(base, capture_output=True, text=True)
        err = (proc.stderr or "") + (proc.stdout or "")
        if proc.returncode != 0 and "externally-managed-environment" in err:
            proc = subprocess.run(
                base + ["--break-system-packages"], capture_output=True, text=True
            )
            err = (proc.stderr or "") + (proc.stdout or "")
        if proc.returncode != 0:
            print(
                "[cortex-viz-launcher] dependency install failed for "
                f"{', '.join(packages)} (python {sys.executable}).\n"
                f"[cortex-viz-launcher] pip said:\n{err.strip()[-2000:]}\n"
                "[cortex-viz-launcher] Fix the pip failure above (network/"
                "proxy/permissions) or pre-install the packages, then reconnect "
                "the cortex-viz MCP server.",
                file=sys.stderr,
            )
            return
        os.makedirs(deps_dir, exist_ok=True)
        for entry in os.listdir(tmp_dir):
            dest = os.path.join(deps_dir, entry)
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
            elif os.path.exists(dest):
                os.remove(dest)
            os.replace(os.path.join(tmp_dir, entry), dest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _ensure_deps(deps_dir: str) -> None:
    os.makedirs(deps_dir, exist_ok=True)
    missing = [spec for name, spec in _REQUIRED if not _importable(name, deps_dir)]
    if missing:
        _pip_install(deps_dir, missing)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/launcher.py <module>", file=sys.stderr)
        sys.exit(1)
    module = sys.argv[1]
    plugin_root, deps_dir = _resolve_paths()

    path_sep = ";" if sys.platform == "win32" else ":"
    current = os.environ.get("PYTHONPATH", "")
    parts = [plugin_root, deps_dir] + ([current] if current else [])
    os.environ["PYTHONPATH"] = path_sep.join(parts)
    for p in (plugin_root, deps_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = "postgresql://127.0.0.1:5432/cortex"

    _ensure_deps(deps_dir)
    os.chdir(plugin_root)

    sys.argv = [module] + sys.argv[2:]
    try:
        from runpy import run_module

        run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit:
        raise
    except Exception as e:  # pragma: no cover - surfaced to the MCP client
        print(f"[cortex-viz-launcher] failed to run {module}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
