"""Tests for the cortex_viz.__main__ entry point."""

from __future__ import annotations

import runpy
import signal
import sys

import pytest

from cortex_viz import __main__ as entry


def manifest(**over):
    m = {
        "path": "/out/wiki.html",
        "bytes": 2048,
        "page_count": 4,
        "request_count": 9,
        "omitted_capabilities": ["graph", "search"],
        "remote_references": [],
        "missing_assets": [],
        "domain_count": 2,
        "largest_bundle_bytes": 1024,
    }
    m.update(over)
    return m


@pytest.fixture
def export_env(monkeypatch):
    from cortex_viz.handlers import wiki_export_bundle
    from cortex_viz.infrastructure import db_probe
    from cortex_viz.server import http_standalone_wiki

    calls = {}
    store = object()
    monkeypatch.setattr(db_probe, "open_store_or_none", lambda: store)
    monkeypatch.setattr(
        http_standalone_wiki,
        "_dispatch_get",
        lambda s, path, params: ("dispatched", s is store, path, params),
    )

    def make(name, result):
        def fn(*, out_dir, ui_root, respond):
            calls[name] = {
                "out_dir": out_dir,
                "ui_root": ui_root,
                "resp": respond("/api/x", {"a": 1}),
            }
            return result

        return fn

    state = {"result": manifest()}
    monkeypatch.setattr(
        wiki_export_bundle,
        "export_wiki",
        lambda **kw: make("single", state["result"])(**kw),
    )
    monkeypatch.setattr(
        wiki_export_bundle,
        "export_wiki_per_domain",
        lambda **kw: make("per_domain", state["result"])(**kw),
    )
    return calls, state


def test_export_single_reports_and_succeeds(export_env, capsys, tmp_path):
    calls, _ = export_env
    assert entry._export(str(tmp_path / "o")) == 0
    err = capsys.readouterr().err
    assert "wiki export: /out/wiki.html (2 KB, 4 pages, 9 responses)" in err
    assert "graph, search" in err and "domains" not in err
    c = calls["single"]
    assert c["out_dir"] == tmp_path / "o" and c["ui_root"].name == "ui"
    assert c["resp"] == ("dispatched", True, "/api/x", {"a": 1})


def test_export_per_domain_prints_domain_summary(export_env, capsys):
    calls, _ = export_env
    assert entry._export("out", per_domain=True) == 0
    assert "per_domain" in calls
    assert "2 domains, largest bundle 1 KB" in capsys.readouterr().err


def test_export_fails_on_remote_refs_or_missing_assets(export_env, capsys):
    _, state = export_env
    state["result"] = manifest(
        remote_references=["https://cdn/x.js"], missing_assets=["a.css"]
    )
    assert entry._export("out") == 1
    err = capsys.readouterr().err
    assert "ERROR remote references: https://cdn/x.js" in err
    assert "ERROR missing local assets: a.css" in err


def test_main_export_dispatch(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        entry, "_export", lambda d, per_domain=False: seen.append((d, per_domain)) or 0
    )
    monkeypatch.setattr(sys, "argv", ["prog", "--export", "dir", "--per-domain"])
    with pytest.raises(SystemExit) as ei:
        entry.main()
    assert ei.value.code == 0 and seen == [("dir", True)]
    monkeypatch.setattr(sys, "argv", ["prog", "--export", "dir"])
    with pytest.raises(SystemExit):
        entry.main()
    assert seen[-1] == ("dir", False)


def test_main_export_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prog", "--export"])
    with pytest.raises(SystemExit) as ei:
        entry.main()
    assert ei.value.code == 2
    assert "usage: python -m cortex_viz --export" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["prog", "--export", "a", "b"])
    with pytest.raises(SystemExit) as ei:
        entry.main()
    assert ei.value.code == 2


def test_main_runs_stdio_server_with_signal_handlers(monkeypatch):
    handlers = {}
    runs = []
    monkeypatch.setattr(sys, "argv", ["prog"])
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.setdefault(sig, h))
    monkeypatch.setattr(entry.mcp, "run", lambda transport: runs.append(transport))
    entry.main()
    assert runs == ["stdio"]
    assert handlers[signal.SIGTERM] is entry._shutdown
    assert handlers[signal.SIGINT] is entry._shutdown


def test_shutdown_stops_server_then_exits(monkeypatch):
    from cortex_viz.server import http_server

    called = []
    monkeypatch.setattr(http_server, "shutdown_server", lambda: called.append(1))
    with pytest.raises(SystemExit) as ei:
        entry._shutdown()
    assert ei.value.code == 0 and called == [1]


def test_shutdown_swallows_server_errors(monkeypatch):
    from cortex_viz.server import http_server

    def boom():
        raise RuntimeError("already down")

    monkeypatch.setattr(http_server, "shutdown_server", boom)
    with pytest.raises(SystemExit) as ei:
        entry._shutdown(signal.SIGTERM, None)
    assert ei.value.code == 0


def test_module_executed_as_script(monkeypatch):
    import fastmcp

    runs = []
    monkeypatch.setattr(sys, "argv", ["prog"])
    monkeypatch.setattr(signal, "signal", lambda sig, h: None)
    monkeypatch.setattr(
        fastmcp.FastMCP, "run", lambda self, transport: runs.append(transport)
    )
    monkeypatch.delitem(sys.modules, "cortex_viz.__main__")
    runpy.run_module("cortex_viz.__main__", run_name="__main__")
    assert runs == ["stdio"]
