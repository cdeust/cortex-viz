"""Cross-platform lifecycle contracts for the fresh-code visualization bootstrap."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from types import SimpleNamespace

from cortex_viz.server import visualize_bootstrap as bootstrap


def _cortex_root(path):
    (path / "mcp_server").mkdir(parents=True)
    (path / "ui").mkdir()
    (path / "ui" / "unified-viz.html").write_text("ui", encoding="utf-8")
    return path


def test_root_detection_and_dev_source_requires_explicit_env_opt_in(
    tmp_path, monkeypatch
):
    root = _cortex_root(tmp_path / "dev")
    assert bootstrap._is_cortex_root(root)
    assert not bootstrap._is_cortex_root(tmp_path / "missing")

    monkeypatch.setenv("CORTEX_DEV_ROOT", str(root))
    monkeypatch.delenv("CORTEX_DEV_SOURCE_SYNC", raising=False)
    monkeypatch.setattr(bootstrap.Path, "home", lambda: tmp_path / "empty-home")
    assert bootstrap._find_dev_source() is None

    monkeypatch.setenv("CORTEX_DEV_SOURCE_SYNC", "1")
    assert bootstrap._find_dev_source() == root


def test_dev_source_prefers_new_fallback_then_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("CORTEX_DEV_ROOT", raising=False)
    monkeypatch.delenv("CORTEX_DEV_SOURCE_SYNC", raising=False)
    monkeypatch.setattr(bootstrap.Path, "home", lambda: tmp_path)
    modern = _cortex_root(tmp_path / "Developments/anthropic-partnership/Cortex")
    legacy = _cortex_root(tmp_path / "Developments/Cortex")
    assert bootstrap._find_dev_source() == modern
    shutil.rmtree(modern)
    assert bootstrap._find_dev_source() == legacy


def test_cache_roots_find_plugin_marketplace_and_both_uv_layouts(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.Path, "home", lambda: tmp_path)
    plugin = tmp_path / ".claude/plugins/cache/cortex-plugins/cortex/1.0"
    marketplace = tmp_path / ".claude/plugins/marketplaces/cdeust-cortex"
    nested = tmp_path / ".cache/uv/archive-v0/a/lib/python3.12/site-packages"
    flat = tmp_path / ".cache/uv/archive-v0/b"
    for root in (plugin, marketplace, nested / "mcp_server", flat / "mcp_server"):
        root.mkdir(parents=True)

    assert set(bootstrap._cache_roots()) == {plugin, marketplace, nested, flat}


def test_sync_uses_rsync_when_available(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "mcp_server").mkdir(parents=True)
    (src / "hooks.json").write_text("{}", encoding="utf-8")
    dst.mkdir()
    calls = []
    copies = []
    monkeypatch.setattr(bootstrap, "_cache_roots", lambda: [dst])
    monkeypatch.setattr(shutil, "which", lambda _name: "/bin/rsync")
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: calls.append(args[0])
    )
    monkeypatch.setattr(
        shutil, "copy2", lambda source, target: copies.append((source, target))
    )

    assert bootstrap._sync(src) == 1
    assert calls == [
        [
            "/bin/rsync",
            "-a",
            "--delete",
            f"{src / 'mcp_server'}/",
            f"{dst / 'mcp_server'}/",
        ]
    ]
    assert copies == [(src / "hooks.json", dst / "hooks.json")]


def test_sync_copy_fallback_replaces_trees_and_tolerates_failures(
    tmp_path, monkeypatch
):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "mcp_server").mkdir(parents=True)
    (src / "mcp_server" / "new.py").write_text("new", encoding="utf-8")
    (src / "README.md").write_text("readme", encoding="utf-8")
    (dst / "mcp_server").mkdir(parents=True)
    (dst / "mcp_server" / "old.py").write_text("old", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_cache_roots", lambda: [dst])
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    assert bootstrap._sync(src) == 1
    assert not (dst / "mcp_server" / "old.py").exists()
    assert (dst / "mcp_server" / "new.py").read_text() == "new"
    assert (dst / "README.md").read_text() == "readme"

    monkeypatch.setattr(
        shutil,
        "copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tree")),
    )
    monkeypatch.setattr(
        shutil,
        "copy2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("file")),
    )
    assert bootstrap._sync(src) == 1


def test_viz_module_kill_and_spawn_contracts(tmp_path, monkeypatch):
    assert bootstrap._viz_instance_mod(tmp_path).__name__.endswith("viz_instance")

    killed = []

    class VI:
        @staticmethod
        def read_instance():
            return {"pid": 10}

        @staticmethod
        def pids_on_port(port):
            assert port == bootstrap.PORT
            return [11, 12]

        @staticmethod
        def kill_and_wait(pid):
            killed.append(pid)
            if pid == 11:
                raise ProcessLookupError(pid)

    bootstrap._kill_stale(tmp_path, VI)
    assert killed == [10, 11, 12]

    assert bootstrap._spawn_server(tmp_path) is None
    standalone = tmp_path / "mcp_server/server/http_standalone.py"
    standalone.parent.mkdir(parents=True)
    standalone.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setenv("PYTHONPATH", "/existing")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    bootstrap._spawn_server(tmp_path)
    command = calls[0][0][0]
    options = calls[0][1]
    assert command[:2] == [sys.executable, str(standalone)]
    assert command[-2:] == ["--port", str(bootstrap.PORT)]
    assert options["env"]["PYTHONPATH"] == f"{tmp_path}:/existing"
    assert options["start_new_session"] is True

    calls.clear()
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    bootstrap._spawn_server(tmp_path)
    assert calls[0][1]["env"]["PYTHONPATH"] == str(tmp_path)


def test_extras_probe_reports_import_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        importlib, "import_module", lambda name: SimpleNamespace(name=name)
    )
    assert bootstrap._extras_available(tmp_path)

    def fail_on_arrow(name):
        if name == "pyarrow":
            raise ImportError(name)
        return SimpleNamespace(name=name)

    monkeypatch.setattr(importlib, "import_module", fail_on_arrow)
    assert not bootstrap._extras_available(tmp_path)


class _ImmediateThread:
    def __init__(self, *, target, name, daemon):
        assert (name, daemon) == ("cortex-prepare", True)
        self.target = target

    def start(self):
        self.target()


class _Response:
    def __init__(self, body):
        self.body = body

    def read(self, _size=None):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_prepare_then_render_primes_waits_recomputes_and_opens(monkeypatch):
    opened = []
    urls = []

    def urlopen(url, timeout):
        urls.append((url, timeout))
        if url.endswith("/progress"):
            return _Response(b'{"baseline_ready": true}')
        return _Response(b"ok")

    monkeypatch.setattr(threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    target = bootstrap._drive_prepare_then_render("http://localhost", timeout_s=9)
    assert target == "http://localhost/?viz=force"
    assert [url for url, _ in urls] == [
        "http://localhost/api/graph",
        "http://localhost/api/graph/progress",
        "http://localhost/api/recompute_layout",
    ]
    assert opened == [target]


def test_prepare_then_render_tolerates_network_and_browser_errors(monkeypatch):
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(
        webbrowser,
        "open",
        lambda *_args: (_ for _ in ()).throw(OSError("headless")),
    )
    assert bootstrap._drive_prepare_then_render("http://localhost", timeout_s=0) == (
        "http://localhost/?viz=force"
    )


def test_wait_for_instance_rejects_old_registration_then_accepts_new(monkeypatch):
    instances = iter(
        [
            {"started_at": 1, "port": 1},
            {"started_at": 10, "port": 2},
        ]
    )
    vi = SimpleNamespace(
        read_instance=lambda: next(instances), probe=lambda port: port == 2
    )
    clock = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    assert bootstrap._wait_for_instance(vi, spawned_after=5, timeout=1) == {
        "started_at": 10,
        "port": 2,
    }

    clock = iter([0.0, 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    assert bootstrap._wait_for_instance(vi, spawned_after=5, timeout=0.5) is None


def test_main_reports_absence_reuse_and_both_spawn_modes(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(bootstrap, "_find_dev_source", lambda: None)
    bootstrap.main()
    assert capsys.readouterr().out == "no_dev_source\n"

    vi = SimpleNamespace(reusable_instance=lambda _src: {"pid": 7, "port": 4567})
    monkeypatch.setattr(bootstrap, "_find_dev_source", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "_viz_instance_mod", lambda _src: vi)
    monkeypatch.setattr(bootstrap, "_sync", lambda _src: 4)
    bootstrap.main()
    assert "ok reused pid=7 synced=4" in capsys.readouterr().out

    vi.reusable_instance = lambda _src: None
    monkeypatch.setattr(bootstrap, "_kill_stale", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "_spawn_server", lambda _src: None)
    monkeypatch.setattr(
        bootstrap,
        "_wait_for_instance",
        lambda *_args: {"port": 9876},
    )
    monkeypatch.setattr(bootstrap, "_extras_available", lambda _src: True)
    monkeypatch.setattr(
        bootstrap,
        "_drive_prepare_then_render",
        lambda base: f"{base}/?viz=force",
    )
    bootstrap.main()
    assert "url=http://127.0.0.1:9876/?viz=force extras=ok" in capsys.readouterr().out

    monkeypatch.setattr(bootstrap, "_wait_for_instance", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "_extras_available", lambda _src: False)
    bootstrap.main()
    assert f"url=http://127.0.0.1:{bootstrap.PORT}/?viz=force extras=missing" in (
        capsys.readouterr().out
    )
