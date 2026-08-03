"""Process, platform, and lifecycle contracts for standalone HTTP launch."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from cortex_viz.core import tile_renderer
from cortex_viz.infrastructure import ap_bridge, db_probe
from cortex_viz.server import (
    graph_build,
    http_standalone_activity,
    http_standalone_wiki,
    viz_instance,
)
from cortex_viz.server import (
    http_launcher as launcher,
)
from cortex_viz.server import (
    http_standalone as standalone,
)


class _Handler:
    def __init__(self, path="/", headers=None):
        self.path = path
        self.headers = headers or {}
        self.responses = []
        self.response_headers = []
        self.ended = 0

    def send_response(self, status):
        self.responses.append(status)

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        self.ended += 1


def _handler_instance(handler_cls, path="/", headers=None):
    handler = object.__new__(handler_cls)
    handler.path = path
    handler.headers = headers or {}
    handler.responses = []
    handler.response_headers = []
    handler.ended = 0
    handler.send_response = lambda status: handler.responses.append(status)
    handler.send_header = lambda name, value: handler.response_headers.append(
        (name, value)
    )
    handler.end_headers = lambda: setattr(handler, "ended", handler.ended + 1)
    return handler


class _Server:
    def __init__(self, port=4567):
        self.server_address = ("127.0.0.1", port)
        self.shutdowns = 0
        self.served = 0

    def shutdown(self):
        self.shutdowns += 1

    def serve_forever(self):
        self.served += 1


class _Thread:
    instances: ClassVar[list] = []

    def __init__(self, *, target, args=(), daemon, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.started = False
        type(self).instances.append(self)

    def start(self):
        self.started = True


class _ImmediateThread(_Thread):
    def start(self):
        super().start()
        self.target(*self.args)


def test_idle_watchdog_and_ui_root_resolution(tmp_path, monkeypatch, capsys):
    server = _Server()
    elapsed = iter([1, standalone.IDLE_TIMEOUT])
    monkeypatch.setattr(standalone.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(standalone, "seconds_since_last_request", lambda: next(elapsed))
    standalone._idle_watchdog(server)
    assert server.shutdowns == 1
    assert "idle" in capsys.readouterr().err

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "unified-viz.html").write_text("ui", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "is_file", lambda self: self == ui / "unified-viz.html")
    assert standalone._get_ui_root() == ui

    monkeypatch.setattr(Path, "is_file", lambda _self: False)
    monkeypatch.setattr(
        Path,
        "cwd",
        lambda: (_ for _ in ()).throw(FileNotFoundError("deleted cwd")),
    )
    with pytest.raises(RuntimeError, match="UI files not found"):
        standalone._get_ui_root()


def test_store_modes_and_unified_handler_security_routes(tmp_path, monkeypatch):
    assert standalone._get_store(True) is None
    monkeypatch.setattr(db_probe, "open_store_or_none", lambda: "db")
    assert standalone._get_store(False) == "db"

    ui = tmp_path
    calls = []
    monkeypatch.setattr(standalone, "touch", lambda: calls.append("touch"))
    monkeypatch.setattr(
        standalone,
        "_route_unified_get",
        lambda *args: calls.append(("get", args[0].path)),
    )
    monkeypatch.setattr(
        http_standalone_activity,
        "serve_activity_ingest",
        lambda *_args: calls.append("activity"),
    )
    monkeypatch.setattr(
        http_standalone_wiki,
        "serve_wiki_save",
        lambda *_args: calls.append("wiki"),
    )
    monkeypatch.setattr(
        standalone, "_apply_cors_headers", lambda _handler: calls.append("cors")
    )
    handler_cls = standalone._build_unified_handler(ui, "db")
    assert handler_cls.protocol_version == "HTTP/1.1"

    monkeypatch.setattr(standalone, "validate_host_header", lambda _handler: False)
    blocked = _handler_instance(handler_cls)
    assert not handler_cls._guard_host(blocked)
    assert blocked.responses == [421]
    handler_cls.do_GET(_handler_instance(handler_cls))
    handler_cls.do_OPTIONS(_handler_instance(handler_cls))

    monkeypatch.setattr(standalone, "validate_host_header", lambda _handler: True)
    options = _handler_instance(handler_cls)
    handler_cls.do_OPTIONS(options)
    assert options.responses == [204]
    assert "cors" in calls

    activity = _handler_instance(handler_cls, "/api/activity")
    handler_cls.do_POST(activity)
    assert "activity" in calls

    monkeypatch.setattr(standalone, "enforce_same_origin_write", lambda _handler: False)
    forbidden = _handler_instance(handler_cls, "/api/wiki/save")
    handler_cls.do_POST(forbidden)
    assert forbidden.responses == [403]

    monkeypatch.setattr(standalone, "enforce_same_origin_write", lambda _handler: True)
    handler_cls.do_POST(_handler_instance(handler_cls, "/api/wiki/save?x=1"))
    assert "wiki" in calls
    missing = _handler_instance(handler_cls, "/api/other")
    handler_cls.do_POST(missing)
    assert missing.responses == [404]
    handler_cls.do_GET(_handler_instance(handler_cls, "/api/graph"))
    assert ("get", "/api/graph") in calls
    assert handler_cls.log_message(missing, "ignored") is None


def test_bind_announce_ap_discovery_and_tile_warmup(tmp_path, monkeypatch):
    attempts = []

    def threaded_factory(address, _handler_cls):
        attempts.append(address[1])
        if address[1] == 3458:
            raise OSError("busy")
        return _Server(9999)

    monkeypatch.setattr(standalone, "_ThreadedHTTPServer", threaded_factory)
    assert standalone._bind_server(object, 3458).server_address[1] == 9999
    assert attempts == [3458, 0]
    monkeypatch.setattr(
        standalone,
        "_ThreadedHTTPServer",
        lambda *_args: (_ for _ in ()).throw(OSError("all busy")),
    )
    with pytest.raises(OSError, match="all busy"):
        standalone._bind_server(object, 3458)

    class Output(io.StringIO):
        was_closed = False

        def close(self):
            self.was_closed = True

    output = Output()
    monkeypatch.setattr(standalone.sys, "stdout", output)
    monkeypatch.setattr(standalone.os, "getpid", lambda: 42)
    standalone._announce("http://localhost")
    assert output.getvalue() == '{"url": "http://localhost", "pid": 42}\n'
    assert output.was_closed

    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    monkeypatch.delenv("CORTEX_AP_AUTO_INDEX", raising=False)
    monkeypatch.setattr(standalone.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    standalone._auto_enable_ap()
    assert "CORTEX_AP_COMMAND" not in os.environ

    binary = (
        tmp_path
        / "Developments/anthropic-partnership/automatised-pipeline"
        / "target/release/automatised-pipeline"
    )
    binary.parent.mkdir(parents=True)
    binary.write_text("bin", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    standalone._auto_enable_ap()
    assert json.loads(os.environ["CORTEX_AP_COMMAND"])["command"] == str(binary)

    rendered = []
    monkeypatch.setattr(
        tile_renderer,
        "render_tile_png",
        lambda *args, **kwargs: rendered.append((args, kwargs)),
    )
    standalone._warm_tile_renderer()
    assert rendered


def test_auto_index_roster_resolves_existing_and_indexes_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(standalone.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CORTEX_AP_COMMAND", '{"command":"ap","args":[]}')
    monkeypatch.setenv("CORTEX_AP_AUTO_INDEX", "1")
    projects = tmp_path / "Developments/anthropic-partnership"
    existing = projects / "existing"
    missing = projects / "missing"
    for project in (existing, missing):
        (project / ".git").mkdir(parents=True)
    graph = tmp_path / ".cortex/ap_graphs/existing/graph"
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"x" * 10_001)
    calls = []

    class Bridge:
        async def call(self, name, payload):
            calls.append((name, payload))

        async def analyze_codebase(self, project, **kwargs):
            calls.append(("analyze", project, kwargs))

        async def close(self):
            calls.append(("close",))

    monkeypatch.setattr(ap_bridge, "APBridge", Bridge)
    monkeypatch.setattr(standalone.threading, "Thread", _ImmediateThread)
    standalone._auto_enable_ap()
    assert {call[0] for call in calls} == {"resolve_graph", "analyze", "close"}


def test_parse_args_and_main_with_and_without_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["server", "--type", "unified", "--port", "3458", "--no-db"]
    )
    args = standalone._parse_args()
    assert (args.type, args.port, args.no_db) == ("unified", 3458, True)

    server = _Server(7890)
    monkeypatch.setattr(
        standalone,
        "_parse_args",
        lambda: SimpleNamespace(type="unified", port=3458, no_db=False),
    )
    monkeypatch.setattr(standalone, "_auto_enable_ap", lambda: None)
    monkeypatch.setattr(standalone, "_get_ui_root", lambda: tmp_path)
    monkeypatch.setattr(standalone, "_get_store", lambda no_db: None if no_db else "db")
    monkeypatch.setattr(standalone, "_build_unified_handler", lambda *_args: object)
    monkeypatch.setattr(standalone, "_bind_server", lambda *_args: server)
    monkeypatch.setattr(standalone, "_announce", lambda _url: None)
    monkeypatch.setattr(standalone.threading, "Thread", _Thread)
    monkeypatch.setattr(db_probe, "no_db_requested", lambda: False)
    builds = []
    monkeypatch.setattr(
        graph_build, "ensure_build_started", lambda store: builds.append(store)
    )
    registrations = []
    monkeypatch.setattr(
        viz_instance, "write_instance", lambda port: registrations.append(port)
    )
    standalone.main()
    assert builds == ["db"]
    assert registrations == [7890]
    assert server.served == 1
    assert len(_Thread.instances) >= 2

    monkeypatch.setattr(db_probe, "no_db_requested", lambda: True)
    standalone.main()
    assert builds == ["db"]
    assert server.served == 2


def _launcher_root(path):
    (path / "mcp_server").mkdir(parents=True)
    (path / "ui").mkdir()
    (path / "ui/unified-viz.html").write_text("ui", encoding="utf-8")
    return path


def test_launcher_kill_detection_binary_resolution_and_graph_setup(
    tmp_path, monkeypatch
):
    killed = []
    monkeypatch.setattr(viz_instance, "pids_on_port", lambda port: [port + 1])
    monkeypatch.setattr(viz_instance, "kill_and_wait", lambda pid: killed.append(pid))
    launcher._kill_port(10)
    assert killed == [11]

    root = _launcher_root(tmp_path / "dev")
    monkeypatch.setenv("CORTEX_DEV_ROOT", str(root))
    monkeypatch.setenv("CORTEX_DEV_SOURCE_SYNC", "1")
    assert launcher._detect_dev_source() == root
    monkeypatch.delenv("CORTEX_DEV_ROOT")
    monkeypatch.delenv("CORTEX_DEV_SOURCE_SYNC")

    monkeypatch.setattr(ap_bridge, "_resolve_command", lambda: {"command": "/bin/ap"})
    monkeypatch.delenv("CORTEX_AP_COMMAND", raising=False)
    assert launcher._find_ap_binary() == "/bin/ap"
    monkeypatch.setenv("CORTEX_AP_COMMAND", "configured")
    assert launcher._find_ap_binary() is None

    env = {}
    monkeypatch.delenv("CORTEX_AP_COMMAND")
    monkeypatch.setattr(launcher, "_find_ap_binary", lambda: "/bin/ap")
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)
    graph = tmp_path / ".cortex/ap_graph/graph"
    graph.parent.mkdir(parents=True)
    graph.write_text("graph", encoding="utf-8")
    launcher._ensure_ap_graph(root, env)
    assert json.loads(env["CORTEX_AP_COMMAND"])["command"] == "/bin/ap"
    assert env["CORTEX_AP_GRAPH_PATH"] == str(graph)

    custom = tmp_path / "custom-graph"
    custom.write_text("graph", encoding="utf-8")
    env["CORTEX_AP_GRAPH_PATH"] = str(custom)
    launcher._ensure_ap_graph(root, env)
    assert env["CORTEX_AP_GRAPH_PATH"] == str(custom)


def test_launcher_sync_probe_and_spawn_paths(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "mcp_server").mkdir(parents=True)
    (src / "mcp_server/file.py").write_text("new", encoding="utf-8")
    (dst / "mcp_server").mkdir(parents=True)
    (dst / "mcp_server/old.py").write_text("old", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    launcher._sync_dev_source(src, dst)
    assert (dst / "mcp_server/file.py").exists()
    assert not (dst / "mcp_server/old.py").exists()

    class Response:
        def read(self):
            return b"ok"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert launcher._probe_port(12) == "http://127.0.0.1:12"
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")),
    )
    assert launcher._probe_port(12) is None

    monkeypatch.setattr(launcher, "_detect_dev_source", lambda: None)
    monkeypatch.setattr(viz_instance, "reusable_instance", lambda _src: {"port": 7777})
    assert launcher.launch_server("unified") == "http://127.0.0.1:7777"

    monkeypatch.setattr(viz_instance, "reusable_instance", lambda _src: None)
    monkeypatch.setattr(launcher, "_probe_port", lambda _port: None)
    monkeypatch.setattr(launcher, "_ensure_ap_graph", lambda *_args: None)

    class Stdout:
        def readline(self):
            return b'{"url":"http://127.0.0.1:8888"}'

        def close(self):
            return None

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=Stdout()),
    )
    assert launcher.launch_server("unified") == "http://127.0.0.1:8888"


def test_launcher_dev_reuse_stale_cleanup_and_spawn_failure(tmp_path, monkeypatch):
    root = _launcher_root(tmp_path / "dev")
    monkeypatch.setattr(launcher, "_detect_dev_source", lambda: root)
    monkeypatch.setattr(launcher, "_sync_dev_source", lambda *_args: None)
    monkeypatch.setattr(viz_instance, "reusable_instance", lambda _src: {"port": 6000})
    assert launcher.launch_server("unified") == "http://127.0.0.1:6000"

    monkeypatch.setattr(viz_instance, "reusable_instance", lambda _src: None)
    monkeypatch.setattr(viz_instance, "read_instance", lambda: {"pid": 99})
    killed = []
    monkeypatch.setattr(viz_instance, "kill_and_wait", lambda pid: killed.append(pid))
    monkeypatch.setattr(launcher, "_kill_port", lambda port: killed.append(port))
    probes = iter([None, "http://127.0.0.1:3458"])
    monkeypatch.setattr(launcher, "_probe_port", lambda _port: next(probes))
    monkeypatch.setattr(launcher, "_ensure_ap_graph", lambda *_args: None)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=SimpleNamespace(readline=lambda: b"invalid", close=lambda: None)
        ),
    )
    assert launcher.launch_server("unified") == "http://127.0.0.1:3458"
    assert killed == [99, 3458]

    monkeypatch.setattr(launcher, "_probe_port", lambda _port: None)
    with pytest.raises(RuntimeError, match="Failed to start"):
        launcher.launch_server("unified")


def test_open_in_browser_rejects_external_and_uses_platform_openers(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda command, **_kwargs: calls.append(command)
    )
    launcher.open_in_browser("https://example.com")
    launcher.open_in_browser("http://127.0.0.1:3458/page")
    assert calls == [["open", "http://127.0.0.1:3458/page"]]

    calls.clear()
    attempts = iter([FileNotFoundError(), None])

    def fallback(command, **_kwargs):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        calls.append(command)

    monkeypatch.setattr(subprocess, "Popen", fallback)
    launcher.open_in_browser("http://127.0.0.1:3458")
    assert calls == [["xdg-open", "http://127.0.0.1:3458"]]

    monkeypatch.setattr(
        launcher,
        "os",
        SimpleNamespace(
            name="nt", startfile=lambda url: calls.append(["startfile", url])
        ),
    )
    launcher.open_in_browser("http://127.0.0.1:3458")
    assert calls[-1] == ["startfile", "http://127.0.0.1:3458"]
