"""Behaviour tests for open_visualization helpers and the handler flow.

Filesystem work happens in tmp_path under a redirected HOME; subprocess,
the HTTP launcher and the browser are the faked external boundaries.
"""

from __future__ import annotations

import asyncio
import http.server
import subprocess
import threading

import pytest

import cortex_viz.handlers.open_visualization as ov
from cortex_viz.infrastructure.schema_migrate import MigrationResult


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CORTEX_DEV_SOURCE_SYNC", raising=False)
    monkeypatch.delenv("CORTEX_DEV_ROOT", raising=False)
    return tmp_path


def _make_cortex_root(p):
    (p / "mcp_server" / "server").mkdir(parents=True)
    (p / "ui").mkdir()
    (p / "ui" / "unified-viz.html").write_text("<html>")
    return p


# ── _find_dev_source ───────────────────────────────────────────────────


def test_find_dev_source_none_without_checkout(home):
    assert ov._find_dev_source() is None


def test_find_dev_source_conventional_locations(home):
    legacy = _make_cortex_root(home / "Developments" / "Cortex")
    assert ov._find_dev_source() == legacy
    new = _make_cortex_root(home / "Developments" / "anthropic-partnership" / "Cortex")
    assert ov._find_dev_source() == new  # new layout wins


def test_find_dev_source_env_root_requires_opt_in(home, monkeypatch, tmp_path):
    root = _make_cortex_root(tmp_path / "elsewhere")
    monkeypatch.setenv("CORTEX_DEV_ROOT", str(root))
    assert ov._find_dev_source() is None
    monkeypatch.setenv("CORTEX_DEV_SOURCE_SYNC", "1")
    assert ov._find_dev_source() == root


# ── _auto_sync_all_caches ──────────────────────────────────────────────


def _cache_roots(home):
    plugin = (
        home / ".claude" / "plugins" / "cache" / "cortex-plugins" / "cortex" / "1.0"
    )
    market = home / ".claude" / "plugins" / "marketplaces" / "cdeust-cortex"
    uv = (
        home
        / ".cache"
        / "uv"
        / "archive-v0"
        / "abc"
        / "lib"
        / "python3.12"
        / "site-packages"
    )
    for d in (plugin, market, uv):
        d.mkdir(parents=True)
    return plugin, market, uv


def test_auto_sync_copies_without_rsync(home, monkeypatch, tmp_path):
    src = _make_cortex_root(tmp_path / "src")
    (src / "mcp_server" / "a.py").write_text("new")
    plugin, market, uv = _cache_roots(home)
    (plugin / "mcp_server").mkdir()
    (plugin / "mcp_server" / "stale.py").write_text("old")
    monkeypatch.setattr(ov.shutil, "which", lambda name: None)
    synced = ov._auto_sync_all_caches(src)
    assert set(synced) == {str(plugin), str(market), str(uv)}
    assert (plugin / "mcp_server" / "a.py").read_text() == "new"
    assert not (plugin / "mcp_server" / "stale.py").exists()
    assert (market / "ui" / "unified-viz.html").is_file()


def test_auto_sync_uses_rsync_when_present(home, monkeypatch, tmp_path):
    src = _make_cortex_root(tmp_path / "src")
    _plugin, *_ = _cache_roots(home)
    monkeypatch.setattr(ov.shutil, "which", lambda name: "/usr/bin/rsync")
    cmds = []
    monkeypatch.setattr(ov.subprocess, "run", lambda cmd, **kw: cmds.append(cmd))
    ov._auto_sync_all_caches(src)
    assert cmds and cmds[0][:3] == ["/usr/bin/rsync", "-a", "--delete"]
    assert any(c[3].endswith("/mcp_server/") for c in cmds)


def test_auto_sync_skips_failing_target(home, monkeypatch, tmp_path):
    src = _make_cortex_root(tmp_path / "src")
    plugin, *_ = _cache_roots(home)
    monkeypatch.setattr(ov.shutil, "which", lambda name: "/usr/bin/rsync")

    def _boom(*a, **kw):
        raise OSError("rsync died")

    monkeypatch.setattr(ov.subprocess, "run", _boom)
    assert str(plugin) in ov._auto_sync_all_caches(src)


# ── _kill_port ─────────────────────────────────────────────────────────


def test_kill_port_signals_each_pid_and_survives_errors(monkeypatch):
    monkeypatch.setattr(
        ov.subprocess, "check_output", lambda *a, **kw: b"101\nbad\n202\n"
    )
    killed = []

    def _kill(pid, sig):
        killed.append((pid, sig))
        if pid == 101:
            raise ProcessLookupError

    monkeypatch.setattr(ov.os, "kill", _kill)
    ov._kill_port(3458)
    assert killed == [(101, 15), (202, 15)]


def test_kill_port_no_lsof(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(ov.subprocess, "check_output", _boom)
    ov._kill_port(3458)


# ── _url_from_bootstrap ────────────────────────────────────────────────


@pytest.fixture
def live_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_url_from_bootstrap_live_server(live_server):
    url = f"http://127.0.0.1:{live_server}"
    assert ov._url_from_bootstrap(f"ok started url={url}/?viz=force") == url


def test_url_from_bootstrap_rejections(live_server):
    assert ov._url_from_bootstrap("failed url=http://127.0.0.1:1/") is None
    assert ov._url_from_bootstrap("ok no url token") is None
    assert ov._url_from_bootstrap("ok url=http://evil.example:80/?x") is None
    # valid shape but nothing listening
    assert ov._url_from_bootstrap("ok url=http://127.0.0.1:1/?viz=force") is None


# ── migration messages ─────────────────────────────────────────────────


def _mig(**kw):
    base = {"exit_code": 1, "plugin_found": True, "timed_out": False, "stderr": ""}
    base.update(kw)
    return MigrationResult(**base)


def test_migration_failure_detail_variants():
    assert ov._migration_failure_detail(_mig(exit_code=0)) is None
    assert "aucune installation" in ov._migration_failure_detail(
        _mig(plugin_found=False)
    )
    assert ov._migration_failure_detail(_mig(timed_out=True, stderr="slow")) == "slow"
    assert ov._migration_failure_detail(_mig(stderr="oops")) == (
        "code de sortie 1 — oops"
    )
    assert ov._migration_failure_detail(_mig(exit_code=None)) == "aucun code de sortie"
    long = ov._migration_failure_detail(_mig(stderr="x" * 3000))
    assert long.endswith("…") and len(long) < 2100


def test_schema_error_result_message_lists_missing():
    out = ov._schema_error_result(("table a", "col b"), "boom")
    assert out["error"] == "schema_preflight_failed"
    assert out["missing"] == ["table a", "col b"]
    assert "table a; col b" in out["message"]
    assert out["message"].endswith(ov._SUPPORT_INSTRUCTION)


def test_ensure_schema_ready_still_incomplete_after_migration(monkeypatch):
    from cortex_viz.infrastructure.schema_preflight import SchemaPreflightResult

    class R:
        url = "u"

        def close(self):
            pass

    monkeypatch.setattr(ov, "_build_memory_reader", lambda: R())
    monkeypatch.setattr(
        ov, "check_schema", lambda r: SchemaPreflightResult(ok=False, missing=("t",))
    )
    monkeypatch.setattr(ov, "run_schema_migration", lambda url: _mig(exit_code=0))
    out = ov._ensure_schema_ready()
    assert out["migration_failure"] == "le schéma reste incomplet après la migration"


def test_ensure_schema_ready_no_db_requested(monkeypatch):
    monkeypatch.setenv("CORTEX_VIZ_NO_DB", "1")
    monkeypatch.setattr(
        ov,
        "_build_memory_reader",
        lambda: pytest.fail("no connection when no-DB requested"),
    )
    assert ov._ensure_schema_ready() is None


def test_ensure_schema_ready_db_unreachable(monkeypatch):
    class R:
        url = "u"
        closed = False

        def close(self):
            R.closed = True

    def _check(r):
        raise ov.DB_UNREACHABLE_ERRORS[0]("down")

    monkeypatch.delenv("CORTEX_VIZ_NO_DB", raising=False)
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: R())
    monkeypatch.setattr(ov, "check_schema", _check)
    assert ov._ensure_schema_ready() is None
    assert R.closed is True


# ── handler ────────────────────────────────────────────────────────────


@pytest.fixture
def launched(monkeypatch):
    rec = {"opened": [], "launch": []}
    monkeypatch.setattr(ov, "_ensure_schema_ready", lambda: None)
    monkeypatch.setattr(
        ov, "launch_server", lambda mode: rec["launch"].append(mode) or "http://h:1/"
    )
    monkeypatch.setattr(ov, "open_in_browser", lambda u: rec["opened"].append(u))
    return rec


def test_handler_schema_error_short_circuits(monkeypatch):
    monkeypatch.setattr(ov, "_ensure_schema_ready", lambda: {"error": "e"})
    assert asyncio.run(ov.handler({})) == {"error": "e"}


def test_handler_default_galaxy_without_dev_source(home, launched):
    out = asyncio.run(ov.handler(None))
    assert out["url"] == "http://h:1/?viz=force"
    assert out["bootstrap"] == "no_dev_source" and out["dev_source"] is None
    assert launched["launch"] == ["unified"]
    assert launched["opened"] == [out["url"]]
    assert out["layout"]["status"] == "not_triggered"


def test_handler_brain_view(home, launched):
    out = asyncio.run(ov.handler({"view": "brain"}))
    assert out["url"] == "http://h:1/brain"
    assert "3D anatomical brain" in out["message"]


def test_handler_runs_bootstrap_and_honours_its_url(home, launched, monkeypatch):
    root = _make_cortex_root(home / "Developments" / "Cortex")
    boot = root / "mcp_server" / "server" / "visualize_bootstrap.py"
    boot.write_text("print('ok url=http://127.0.0.1:9/?viz=force')")
    ran = {}

    def _run(cmd, **kw):
        ran["cmd"] = cmd
        ran["env"] = kw["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="ok url=X\n", stderr="")

    monkeypatch.setattr(ov.subprocess, "run", _run)
    monkeypatch.setattr(ov, "_url_from_bootstrap", lambda s: "http://127.0.0.1:9")
    out = asyncio.run(ov.handler({}))
    assert ran["cmd"][1] == str(boot)
    assert ran["env"]["PYTHONPATH"]
    assert out["bootstrap"] == "ok url=X"
    assert out["url"] == "http://127.0.0.1:9/?viz=force"
    assert out["dev_source"] == str(root)
    assert launched["launch"] == []


def test_handler_bootstrap_stderr_and_failure(home, launched, monkeypatch):
    root = _make_cortex_root(home / "Developments" / "Cortex")
    (root / "mcp_server" / "server" / "visualize_bootstrap.py").write_text("")
    monkeypatch.setattr(
        ov.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="err\n"
        ),
    )
    assert asyncio.run(ov.handler({}))["bootstrap"] == "err"

    def _boom(*a, **kw):
        raise subprocess.TimeoutExpired("x", 30)

    monkeypatch.setattr(ov.subprocess, "run", _boom)
    out = asyncio.run(ov.handler({}))
    assert out["bootstrap"].startswith("bootstrap_failed: TimeoutExpired")
    assert launched["launch"] == ["unified", "unified"]


def test_handler_legacy_path_when_bootstrap_script_missing(home, launched, monkeypatch):
    _make_cortex_root(home / "Developments" / "Cortex")
    calls = []
    monkeypatch.setattr(
        ov, "_auto_sync_all_caches", lambda s: calls.append(("sync", s))
    )
    monkeypatch.setattr(ov, "_kill_port", lambda p: calls.append(("kill", p)))
    out = asyncio.run(ov.handler({}))
    assert [c[0] for c in calls] == ["sync", "kill"]
    assert calls[1] == ("kill", 3458)
    assert out["bootstrap"] == "no_dev_source"
