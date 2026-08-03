"""Behavioral contracts for the sharded stdio MCP client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from cortex_viz import errors
from cortex_viz.infrastructure import mcp_client_reader as reader
from cortex_viz.infrastructure import mcp_client_spawn as spawn
from cortex_viz.infrastructure import mcp_client_stderr as stderr
from cortex_viz.infrastructure.mcp_client import (
    CLIENT_INFO,
    PROTOCOL_VERSION,
    MCPClient,
)


class DummyTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeStdin:
    def __init__(self, *, close_error: bool = False) -> None:
        self.writes: list[bytes] = []
        self.drains = 0
        self.close_error = close_error

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        self.drains += 1

    def close(self) -> None:
        if self.close_error:
            raise BrokenPipeError("closed")


class FakeStream:
    def __init__(self, values) -> None:
        self.values = list(values)

    async def readline(self):
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeProcess:
    def __init__(self, stdout=(), stderr_values=(), *, close_error=False) -> None:
        self.stdin = FakeStdin(close_error=close_error)
        self.stdout = FakeStream([*stdout, b""])
        self.stderr = FakeStream([*stderr_values, b""])
        self.terminated = False
        self.terminate_error = close_error

    def terminate(self) -> None:
        if self.terminate_error:
            raise ProcessLookupError("gone")
        self.terminated = True


def make_client(**overrides) -> MCPClient:
    client = MCPClient({"command": "python3", **overrides})
    client._proc = FakeProcess()
    return client


def test_typed_errors_keep_codes_and_details():
    assert errors.MethodologyError("x").code == -32000
    assert errors.ValidationError("x", {"field": "x"}).code == -32602
    assert errors.StorageError("x").code == -32001
    assert errors.AnalysisError("x").code == -32002
    failure = errors.McpConnectionError("x", {"server": "ap"})
    assert failure.code == -32003
    assert failure.details == {"server": "ap"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [('{"ok": true}', {"ok": True}), ("[1, 2]", [1, 2]), ("plain", "plain")],
)
def test_decoded_text_block(value, expected):
    assert reader._decoded_text_block(value) == expected


def test_resolve_pending_covers_result_error_and_ignored_messages():
    async def exercise():
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        error = loop.create_future()
        already_done = loop.create_future()
        already_done.set_result("kept")
        pending = {1: result, 2: error, 3: already_done}

        reader._resolve_pending(pending, {})
        reader._resolve_pending(pending, {"id": 99, "result": "ignored"})
        reader._resolve_pending(pending, {"id": 1, "result": {"ok": True}})
        reader._resolve_pending(pending, {"id": 2, "error": {"code": -1}})
        reader._resolve_pending(pending, {"id": 3, "result": "new"})

        assert await result == {"ok": True}
        with pytest.raises(errors.McpConnectionError, match="Unknown error"):
            _ = await error
        assert already_done.result() == "kept"

    asyncio.run(exercise())


def test_dispatch_line_drops_noise_and_routes_json(capsys):
    async def exercise():
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending = {7: future}
        reader._dispatch_line(pending, "not-json")
        reader._dispatch_line(pending, '{"id": 7, "result": 9}')
        assert await future == 9

    asyncio.run(exercise())
    assert "non-JSON line dropped" in capsys.readouterr().err


def test_pump_skips_framing_lines_and_dispatches_response():
    async def exercise():
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client = SimpleNamespace(
            _proc=FakeProcess(
                stdout=[
                    b"\n",
                    b"Content-Length: 10\n",
                    b'{"id": 4, "result": "done"}\n',
                ]
            ),
            _pending={4: future},
        )
        await reader._pump(client)
        assert await future == "done"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "terminal",
    [ConnectionResetError("reset"), RuntimeError("unexpected")],
)
def test_read_loop_fails_pending_and_marks_disconnected(terminal, capsys):
    async def exercise():
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client = SimpleNamespace(
            _proc=SimpleNamespace(stdout=FakeStream([terminal])),
            _pending={1: future},
            _connected=True,
        )
        await reader.read_loop(client)
        assert client._connected is False
        with pytest.raises(errors.McpConnectionError, match=type(terminal).__name__):
            _ = await future

    asyncio.run(exercise())
    assert "reader" in capsys.readouterr().err


def test_read_loop_eof_and_cancelled_are_clean_terminal_paths():
    async def exercise(stream):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client = SimpleNamespace(
            _proc=SimpleNamespace(stdout=stream),
            _pending={1: future},
            _connected=True,
        )
        await reader.read_loop(client)
        assert not client._connected
        with pytest.raises(errors.McpConnectionError, match="EOF"):
            _ = await future

    asyncio.run(exercise(FakeStream([b""])))
    asyncio.run(exercise(FakeStream([asyncio.CancelledError()])))


def test_resolve_command_enforces_allowlist(monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda value: f"/resolved/{value}")
    assert spawn._resolve_command("python3", set()) == "/resolved/python3"
    assert spawn._resolve_command("/opt/bin/custom", {"custom"}) == (
        "/resolved//opt/bin/custom"
    )
    with pytest.raises(errors.McpConnectionError, match="not in allowed list"):
        spawn._resolve_command("sh", set())


def test_spawn_process_passes_bounded_explicit_arguments(monkeypatch):
    async def exercise():
        proc = object()
        create = AsyncMock(return_value=proc)
        monkeypatch.setattr(spawn.asyncio, "create_subprocess_exec", create)
        monkeypatch.setattr(spawn.shutil, "which", lambda value: f"/bin/{value}")
        result = await spawn.spawn_process(
            {
                "command": "python3",
                "args": ["-m", "server"],
                "cwd": "/tmp",
                "env": {"CORTEX_TEST": "1"},
            },
            500,
        )
        assert result is proc
        args = create.await_args.args
        kwargs = create.await_args.kwargs
        assert args == ("/bin/python3", "-m", "server")
        assert kwargs["cwd"] == "/tmp"
        assert kwargs["env"]["CORTEX_TEST"] == "1"
        assert kwargs["limit"] == spawn._LINE_LIMIT

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("failure", "match"),
    [(asyncio.TimeoutError(), "Connect timeout"), (OSError("boom"), "Failed to spawn")],
)
def test_spawn_process_wraps_failures(monkeypatch, failure, match):
    async def exercise():
        monkeypatch.setattr(
            spawn.asyncio, "create_subprocess_exec", AsyncMock(side_effect=failure)
        )
        with pytest.raises(errors.McpConnectionError, match=match):
            await spawn.spawn_process({"command": "python3"}, 25)

    asyncio.run(exercise())


def test_open_stderr_log_sanitizes_name(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    handle = stderr.open_stderr_log({"command": "/tmp/name with spaces"})
    assert handle is not None
    assert "name_with_spaces" in handle.name
    handle.close()


def test_open_stderr_log_fails_open(monkeypatch):
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("readonly")),
    )
    handle = stderr.open_stderr_log({"command": "python3"})
    try:
        assert handle is None
    finally:
        if handle is not None:
            handle.close()


def test_stderr_loop_mirrors_and_persists(monkeypatch, capsys):
    async def exercise():
        log = MagicMock()
        monkeypatch.setattr(stderr, "open_stderr_log", lambda _config: log)
        client = SimpleNamespace(
            _config={"command": "server"},
            _proc=SimpleNamespace(stderr=FakeStream([b"one\n", b"two\xff\n", b""])),
        )
        await stderr.stderr_loop(client)
        assert log.write.call_count == 2
        assert log.flush.call_count == 2
        log.close.assert_called_once()

    asyncio.run(exercise())
    assert "[mcp-client] server: one" in capsys.readouterr().err


def test_stderr_loop_tolerates_log_and_stream_teardown_failures(monkeypatch):
    async def exercise():
        log = MagicMock()
        log.write.side_effect = OSError("disk")
        log.close.side_effect = OSError("close")
        monkeypatch.setattr(stderr, "open_stderr_log", lambda _config: log)
        client = SimpleNamespace(
            _config={"command": "server"},
            _proc=SimpleNamespace(stderr=FakeStream([b"line\n", RuntimeError("gone")])),
        )
        await stderr.stderr_loop(client)

        cancelled = SimpleNamespace(
            _config={"command": "server"},
            _proc=SimpleNamespace(stderr=FakeStream([asyncio.CancelledError()])),
        )
        await stderr.stderr_loop(cancelled)

    asyncio.run(exercise())


def test_client_constructor_and_properties():
    client = make_client(callTimeoutMs=0, connectTimeoutMs=50, idleTimeoutMs=100)
    assert client._call_timeout_ms is None
    assert client._connect_timeout_ms == 50
    assert client._idle_timeout_ms == 100
    assert client.server_info is None
    assert client.protocol_version is None
    assert client.list_tools() == {}
    assert client.max_concurrent_calls == 1
    assert not client.busy
    assert not client.connected

    assert make_client(callTimeoutMs="250")._call_timeout_ms == 250
    assert make_client(maxConcurrentCalls=3).max_concurrent_calls == 3
    assert make_client(maxConcurrentCalls="bad").max_concurrent_calls == 1


def test_connected_and_idle_are_bound_to_the_owning_loop():
    client = make_client(idleTimeoutMs=1)
    client._connected = True
    assert not client.connected

    async def exercise():
        loop = asyncio.get_running_loop()
        client._bound_loop = loop
        client._last_activity = loop.time() - 1
        assert client.connected
        assert client.idle

    asyncio.run(exercise())
    assert not client.connected


def test_notify_serializes_optional_params():
    client = make_client()
    client._notify("initialized")
    client._notify("changed", {"value": 1})
    first, second = [json.loads(line) for line in client._proc.stdin.writes]
    assert first == {"jsonrpc": "2.0", "method": "initialized"}
    assert second["params"] == {"value": 1}


def test_call_requires_connection_and_decodes_supported_results():
    async def exercise():
        client = make_client()
        with pytest.raises(errors.McpConnectionError, match="Not connected"):
            await client.call("x")

        client._connected = True
        client._send = AsyncMock(
            side_effect=[
                {"structuredContent": {"value": 1}},
                {"content": [{"type": "text", "text": '{"value": 2}'}]},
                {"content": [{"type": "text", "text": "plain"}]},
                {},
                {"content": [{"type": "image", "data": "x"}]},
            ]
        )
        assert await client.call("x") == {"value": 1}
        assert await client.call("x") == {"value": 2}
        assert await client.call("x") == "plain"
        assert await client.call("x") is None
        assert await client.call("x") == {"content": [{"type": "image", "data": "x"}]}
        assert client.tool_calls == 5

    asyncio.run(exercise())


def test_perform_handshake_negotiates_and_discovers_tools(monkeypatch):
    async def exercise():
        client = make_client()
        client._send = AsyncMock(
            side_effect=[
                {"protocolVersion": "custom", "serverInfo": {"name": "test"}},
                {"tools": [{"name": "query"}]},
            ]
        )
        client._notify = MagicMock()

        def create_task(coro):
            coro.close()
            return DummyTask()

        monkeypatch.setattr(asyncio, "create_task", create_task)
        await client._perform_handshake()
        assert client._negotiated_version == "custom"
        assert client._server_info == {"name": "test"}
        assert client.list_tools() == {"query": {"name": "query"}}
        assert client._connected
        client._notify.assert_called_once_with("notifications/initialized")
        init = client._send.await_args_list[0].args
        assert init[1]["protocolVersion"] == PROTOCOL_VERSION
        assert init[1]["clientInfo"] == CLIENT_INFO

    asyncio.run(exercise())


def test_perform_handshake_wraps_failure():
    async def exercise():
        client = make_client()
        client._send = AsyncMock(side_effect=RuntimeError("bad handshake"))
        client.close = MagicMock()
        with pytest.raises(errors.McpConnectionError, match="Handshake failed"):
            await client._perform_handshake()
        client.close.assert_called_once()

    asyncio.run(exercise())


def test_connect_success_noop_and_timeout(monkeypatch):
    async def exercise():
        client = make_client()
        client._spawn_process = AsyncMock()
        client._read_loop = AsyncMock()
        client._stderr_loop = AsyncMock()
        client._perform_handshake = AsyncMock()
        await client.connect()
        assert client._bound_loop is asyncio.get_running_loop()
        client._connected = True
        await client.connect()
        assert client._spawn_process.await_count == 1

        timed = make_client(connectTimeoutMs=1)
        timed._spawn_process = AsyncMock()
        timed._read_loop = AsyncMock()
        timed._stderr_loop = AsyncMock()
        timed._perform_handshake = AsyncMock(side_effect=asyncio.TimeoutError())
        timed.close = MagicMock()
        with pytest.raises(errors.McpConnectionError, match="Handshake timed out"):
            await timed.connect()
        timed.close.assert_called_once()

    asyncio.run(exercise())


def test_send_success_and_timeout(monkeypatch):
    async def exercise():
        client = make_client(callTimeoutMs=50)
        send_task = asyncio.create_task(client._send("query", {"x": 1}))
        await asyncio.sleep(0)
        client._pending[1].set_result({"ok": True})
        assert await send_task == {"ok": True}
        sent = json.loads(client._proc.stdin.writes[0])
        assert sent["method"] == "query"

        async def timeout(_awaitable, *, timeout):
            assert timeout == 0.05
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", timeout)
        with pytest.raises(errors.McpConnectionError, match="timed out"):
            await client._send("wedged", {})
        assert 2 not in client._pending

    asyncio.run(exercise())


def test_close_cancels_tasks_fails_pending_and_tolerates_dead_process():
    async def exercise():
        client = make_client()
        client._connected = True
        client._bound_loop = asyncio.get_running_loop()
        client._idle_task = DummyTask()
        client._reader_task = DummyTask()
        client._stderr_task = DummyTask()
        future = asyncio.get_running_loop().create_future()
        client._pending[1] = future
        client._proc = FakeProcess(close_error=True)
        client.close()
        assert not client._connected
        with pytest.raises(errors.McpConnectionError, match="Client closed"):
            _ = await future
        assert client._proc is None
        assert client._idle_task is None
        assert client._reader_task is None
        assert client._stderr_task is None

    asyncio.run(exercise())


def test_wait_until_idle_and_idle_loop_paths(monkeypatch, capsys):
    async def exercise():
        client = make_client()
        sleeps = 0

        async def fake_sleep(_seconds):
            nonlocal sleeps
            sleeps += 1

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        with patch.object(MCPClient, "idle", new_callable=PropertyMock) as idle:
            idle.side_effect = [False, True]
            await client._wait_until_idle()
        assert sleeps == 2

        client._wait_until_idle = AsyncMock()
        client.close = MagicMock()
        await client._idle_loop()
        client.close.assert_called_once()

        client._wait_until_idle = AsyncMock(side_effect=asyncio.CancelledError())
        client.close.reset_mock()
        await client._idle_loop()
        client.close.assert_not_called()

    asyncio.run(exercise())
    assert "Idle timeout" in capsys.readouterr().err


def test_thin_delegates(monkeypatch):
    async def exercise():
        client = make_client()
        read = AsyncMock()
        drain = AsyncMock()
        monkeypatch.setattr("cortex_viz.infrastructure.mcp_client.read_loop", read)
        monkeypatch.setattr("cortex_viz.infrastructure.mcp_client.stderr_loop", drain)
        await client._read_loop()
        await client._stderr_loop()
        read.assert_awaited_once_with(client)
        drain.assert_awaited_once_with(client)

    asyncio.run(exercise())
