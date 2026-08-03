"""Lifecycle, response, and routing contracts for the core HTTP servers."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import ClassVar

import pytest

from cortex_viz.server import http_common, http_server, http_standalone_static


class _Handler:
    def __init__(self, *, path="/", headers=None):
        self.path = path
        self.headers = headers or {}
        self.wfile = io.BytesIO()
        self.responses = []
        self.response_headers = []
        self.ended = 0

    def send_response(self, status):
        self.responses.append(status)

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        self.ended += 1


class _Timer:
    instances: ClassVar[list] = []

    def __init__(self, seconds, function, args=()):
        self.seconds = seconds
        self.function = function
        self.args = args
        self.daemon = False
        self.started = False
        self.cancelled = False
        type(self).instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


class _Thread:
    instances: ClassVar[list] = []

    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon
        self.started = False
        type(self).instances.append(self)

    def start(self):
        self.started = True


class _Server:
    def __init__(self, port=8765):
        self.server_address = ("127.0.0.1", port)
        self.shutdowns = 0

    def serve_forever(self):
        return None

    def shutdown(self):
        self.shutdowns += 1


def test_server_manager_starts_reuses_times_out_and_shuts_down(monkeypatch, capsys):
    _Timer.instances.clear()
    _Thread.instances.clear()
    binds = []

    def server_factory(address, handler_cls):
        binds.append((address, handler_cls))
        if address[1] == 3456:
            raise OSError("busy")
        return _Server()

    monkeypatch.setattr(http_common, "HTTPServer", server_factory)
    monkeypatch.setattr(http_common.threading, "Timer", _Timer)
    monkeypatch.setattr(http_common.threading, "Thread", _Thread)
    manager = http_common.ServerManager("test", idle_seconds=3)
    assert not manager.is_running and manager.url is None

    url = manager.get_or_start(object, 3456)
    assert url == "http://127.0.0.1:8765"
    assert manager.is_running and manager.url == url
    assert [address[0][1] for address in binds] == [3456, 0]
    assert _Thread.instances[-1].started
    assert _Timer.instances[-1].started and _Timer.instances[-1].daemon

    first_timer = _Timer.instances[-1]
    assert manager.get_or_start(object, 9999) == url
    assert first_timer.cancelled
    active_server = manager._server_state["server"]
    _Timer.instances[-1].fire()
    assert active_server.shutdowns == 1
    assert not manager.is_running
    assert "idle timeout" in capsys.readouterr().err

    manager.shutdown()
    assert _Timer.instances[-1].cancelled
    manager._stop_running_server("already stopped")


def test_server_manager_reraises_last_bind_failure(monkeypatch):
    def always_busy(*_args):
        raise OSError("busy")

    monkeypatch.setattr(http_common, "HTTPServer", always_busy)
    manager = http_common.ServerManager("test")
    with pytest.raises(OSError, match="busy"):
        manager._start_server(object, 1)


def test_ui_root_resolution_order_and_failure(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    (plugin / "ui").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    assert http_common.get_ui_root() == plugin / "ui"

    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    cwd = tmp_path / "cwd"
    (cwd / "ui").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert http_common.get_ui_root() == cwd / "ui"

    monkeypatch.chdir(Path(http_common.__file__).parents[2])
    assert http_common.get_ui_root() == Path(http_common.__file__).parents[2] / "ui"

    monkeypatch.setattr(Path, "is_dir", lambda _self: False)
    with pytest.raises(RuntimeError, match="UI files not found"):
        http_common.get_ui_root()


def test_file_and_response_helpers(tmp_path, capsys):
    html = tmp_path / "page.html"
    html.write_text("<p>ok</p>", encoding="utf-8")
    assert http_common.read_html_file(html, "page") == "<p>ok</p>"
    with pytest.raises(RuntimeError, match="Could not read missing"):
        http_common.read_html_file(tmp_path / "missing", "missing")

    handler = _Handler(headers={"Origin": "http://localhost:8000"})
    http_common.send_json_response(handler, {"value": object()}, status=201)
    assert handler.responses == [201]
    assert json.loads(handler.wfile.getvalue())["value"].startswith("<object object")
    assert ("Cache-Control", "no-cache") in handler.response_headers

    error_handler = _Handler()
    http_common.send_error_response(error_handler, ValueError("secret /tmp/path"))
    assert error_handler.responses == [500]
    assert json.loads(error_handler.wfile.getvalue()) == {"error": "ValueError"}
    assert "secret /tmp/path" in capsys.readouterr().err

    html_handler = _Handler()
    http_common.send_html_response(html_handler, html, b"fallback")
    assert html_handler.wfile.getvalue() == b"<p>ok</p>"
    fallback_handler = _Handler()
    http_common.send_html_response(fallback_handler, tmp_path / "none", b"fallback")
    assert fallback_handler.wfile.getvalue() == b"fallback"

    options = _Handler(headers={"Origin": "http://127.0.0.1"})
    http_common.send_cors_options(options)
    assert options.responses == [204]
    assert ("Access-Control-Allow-Methods", "GET, POST, OPTIONS") in (
        options.response_headers
    )


def test_static_file_guard_rejects_invalid_missing_and_symlink_escape(tmp_path):
    base = tmp_path / "static"
    base.mkdir()
    (base / "ok.js").write_bytes(b"ok")
    outside = tmp_path / "secret.js"
    outside.write_bytes(b"secret")
    (base / "escape.js").symlink_to(outside)

    for name in (".hidden", "bad name", "nul\x00.js", ""):
        handler = _Handler()
        http_common.serve_static_file(handler, base, name, "text/javascript")
        assert handler.responses == [403]

    missing = _Handler()
    http_common.serve_static_file(missing, base, "missing.js", "text/javascript")
    assert missing.responses == [404]
    escaped = _Handler()
    http_common.serve_static_file(escaped, base, "escape.js", "text/javascript")
    assert escaped.responses == [404]
    served = _Handler()
    http_common.serve_static_file(served, base, "../ok.js", "text/javascript")
    assert served.responses == [200]
    assert served.wfile.getvalue() == b"ok"


def test_ui_server_timer_reuse_and_shutdown(monkeypatch):
    _Timer.instances.clear()
    monkeypatch.setattr(http_server.threading, "Timer", _Timer)
    server = _Server()
    http_server._active_server = {"server": server, "url": "http://old"}
    http_server._idle_timer = _Timer(1, lambda: None)

    url = http_server.start_ui_server({"nodes": [1]})
    assert url == "http://old"
    assert http_server._active_server["graph_json"] == '{"nodes": [1]}'
    assert _Timer.instances[0].cancelled

    _Timer.instances[-1].fire()
    assert server.shutdowns == 1
    assert http_server._active_server is None

    http_server._active_server = {"server": server}
    http_server.shutdown_server()
    assert server.shutdowns == 2
    assert http_server._idle_timer is None
    http_server.shutdown_server()


def test_ui_server_start_resolves_reads_and_delegates_bind(tmp_path, monkeypatch):
    html = tmp_path / "custom.html"
    html.write_text("page", encoding="utf-8")
    http_server._active_server = None
    received = {}
    monkeypatch.setattr(
        http_server, "_build_handler_class", lambda state: ("handler", state)
    )

    def bind(handler_cls, state):
        received.update(handler=handler_cls, state=state)
        return "http://new"

    monkeypatch.setattr(http_server, "_bind_and_start_ui", bind)
    assert (
        http_server.start_ui_server({"nodes": []}, html_file=str(html)) == "http://new"
    )
    assert received["state"]["html"] == "page"
    assert http_server._resolve_html_path(str(html)) == html

    monkeypatch.setattr(http_server, "get_ui_root", lambda: tmp_path)
    assert http_server._resolve_html_path(None) == tmp_path / "methodology-viz.html"
    with pytest.raises(RuntimeError, match="Could not read UI file"):
        http_server._read_html(tmp_path / "missing")


def _handler_instance(handler_cls, path):
    handler = object.__new__(handler_cls)
    handler.path = path
    handler.wfile = io.BytesIO()
    handler.responses = []
    handler.response_headers = []
    handler.ended = 0
    handler.send_response = lambda status: handler.responses.append(status)
    handler.send_header = lambda name, value: handler.response_headers.append(
        (name, value)
    )
    handler.end_headers = lambda: setattr(handler, "ended", handler.ended + 1)
    return handler


def test_ui_handler_routes_graph_assets_shared_and_html(tmp_path, monkeypatch):
    meth = tmp_path / "methodology"
    (meth / "js").mkdir(parents=True)
    (meth / "css").mkdir()
    (tmp_path / "shared").mkdir()
    monkeypatch.setattr(http_server, "get_ui_root", lambda: tmp_path)
    monkeypatch.setattr(http_server, "_reset_idle_timer", lambda: None)
    calls = []
    monkeypatch.setattr(
        http_server,
        "_serve_graph_json",
        lambda handler, state: calls.append(("graph", state)),
    )
    monkeypatch.setattr(
        http_server,
        "_serve_static",
        lambda _handler, base, name, kind: calls.append(("static", base, name, kind)),
    )
    monkeypatch.setattr(
        http_server,
        "_serve_html_page",
        lambda _handler, state: calls.append(("html", state)),
    )
    monkeypatch.setattr(
        http_standalone_static,
        "serve_shared_asset",
        lambda _handler, base, name: calls.append(("shared", base, name)),
    )
    state = {"graph_json": "{}", "html": "page"}
    handler_cls = http_server._build_handler_class(state)

    for path in (
        "/graph",
        "/methodology/js/app.js",
        "/methodology/css/app.css",
        "/shared/tokens.css?v=1",
        "/",
    ):
        handler_cls.do_GET(_handler_instance(handler_cls, path))
    assert [call[0] for call in calls] == [
        "graph",
        "static",
        "static",
        "shared",
        "html",
    ]

    options = _handler_instance(handler_cls, "/")
    handler_cls.do_OPTIONS(options)
    assert options.responses == [204]
    assert handler_cls.log_message(options, "ignored") is None


def test_ui_serve_helpers_and_bind_fallback(tmp_path, monkeypatch):
    handler = _Handler()
    http_server._serve_graph_json(handler, {"graph_json": '{"x": 1}'})
    assert handler.wfile.getvalue() == b'{"x": 1}'
    html_handler = _Handler()
    http_server._serve_html_page(html_handler, {"html": "page"})
    assert html_handler.wfile.getvalue() == b"page"

    calls = []
    monkeypatch.setattr(
        http_server,
        "serve_static",
        lambda *_args: (_ for _ in ()).throw(FileNotFoundError()),
    )
    missing = _Handler()
    http_server._serve_static(missing, tmp_path, "none.js", "text/javascript")
    assert missing.responses == [404]
    monkeypatch.setattr(http_server, "serve_static", lambda *args: calls.append(args))
    http_server._serve_static(handler, tmp_path, "ok.js", "text/javascript")
    assert calls

    _Thread.instances.clear()
    attempts = []

    def server_factory(address, _handler_cls):
        attempts.append(address[1])
        if address[1] == 3456:
            raise OSError("busy")
        return _Server(4321)

    monkeypatch.setattr(http_server, "HTTPServer", server_factory)
    monkeypatch.setattr(http_server.threading, "Thread", _Thread)
    monkeypatch.setattr(http_server, "_reset_idle_timer", lambda: None)
    http_server._active_server = None
    assert http_server._bind_and_start_ui(object, {"graph_json": "{}"}) == (
        "http://127.0.0.1:4321"
    )
    assert attempts == [3456, 0]
    assert http_server._active_server["graph_json"] == "{}"

    monkeypatch.setattr(
        http_server,
        "HTTPServer",
        lambda *_args: (_ for _ in ()).throw(OSError("all busy")),
    )
    with pytest.raises(OSError, match="all busy"):
        http_server._bind_and_start_ui(object, {})
