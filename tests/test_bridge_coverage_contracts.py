"""Behavioral contracts for sibling MCP bridges and PRD artifact discovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from cortex_viz.infrastructure import ap_bridge as ap
from cortex_viz.infrastructure import memory_config
from cortex_viz.infrastructure import prd_bridge as prd


class FakeMCPClient:
    instances: ClassVar[list] = []
    connect_failure: ClassVar[BaseException | None] = None
    call_failure: ClassVar[BaseException | None] = None

    def __init__(self, config):
        self.config = config
        self.connected = False
        self.calls = []
        self.closed = False
        self._extra_allowed_commands = set()
        self.instances.append(self)

    async def connect(self):
        if self.connect_failure:
            raise self.connect_failure
        self.connected = True

    async def call(self, tool, args):
        if self.call_failure:
            raise self.call_failure
        self.calls.append((tool, args))
        return {"tool": tool, "args": args}

    def close(self):
        self.closed = True
        self.connected = False


def reset_fake_client():
    FakeMCPClient.instances = []
    FakeMCPClient.connect_failure = None
    FakeMCPClient.call_failure = None


def test_ap_is_enabled_reads_settings_and_fails_open(monkeypatch):
    memory_config.get_memory_settings.cache_clear()
    monkeypatch.setattr(
        memory_config, "get_memory_settings", lambda: SimpleNamespace(AP_ENABLED=False)
    )
    assert not ap.is_enabled()
    monkeypatch.setattr(
        memory_config,
        "get_memory_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )
    assert ap.is_enabled()


def test_resolve_graph_path_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_AP_GRAPH_PATH", " /explicit/graph ")
    assert ap.resolve_graph_path() == "/explicit/graph"

    monkeypatch.delenv("CORTEX_AP_GRAPH_PATH")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default = tmp_path / ".cortex" / "ap_graph" / "graph"
    default.parent.mkdir(parents=True)
    default.write_text("graph")
    assert ap.resolve_graph_path() == str(default)

    default.unlink()
    monkeypatch.setattr(ap, "resolve_graph_paths", lambda: ["/roster/graph"])
    assert ap.resolve_graph_path() == "/roster/graph"
    monkeypatch.setattr(ap, "resolve_graph_paths", lambda: [])
    assert ap.resolve_graph_path() is None


def test_resolve_graph_paths_discovers_both_rosters_and_deduplicates(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".cortex" / "ap_graph" / "graph"
    old = tmp_path / ".cortex" / "ap_graphs" / "old" / "graph"
    current = tmp_path / ".cache" / "cortex" / "code-graphs" / "new" / "graph"
    for path in (legacy, old, current):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name)
    monkeypatch.setenv("CORTEX_AP_GRAPH_PATH", str(legacy))
    paths = ap.resolve_graph_paths()
    assert paths == [str(legacy), str(old), str(current)]


def test_resolve_graph_paths_tolerates_stat_and_roster_errors(monkeypatch):
    monkeypatch.setenv("CORTEX_AP_GRAPH_PATH", "/denied")
    monkeypatch.setattr(Path, "home", lambda: Path("/denied-home"))
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr(
        Path,
        "is_dir",
        lambda self: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert ap.resolve_graph_paths() == []


def test_ap_resolve_command_env_symlink_and_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_AP_COMMAND", '{"command":"custom","args":["x"]}')
    assert ap._resolve_command()["command"] == "custom"
    monkeypatch.setenv("CORTEX_AP_COMMAND", "bad-json")
    assert ap._resolve_command() is None
    monkeypatch.setenv("CORTEX_AP_COMMAND", "[]")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    bin_path = tmp_path / ".claude" / "methodology" / "bin" / "mcp-server"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text("binary")
    bin_path.chmod(0o755)
    monkeypatch.delenv("CORTEX_AP_COMMAND")
    assert ap._resolve_command() == {"command": str(bin_path), "args": []}

    bin_path.unlink()
    install = tmp_path / "plugin"
    binary = install / "target" / "release" / "automatised-pipeline"
    binary.parent.mkdir(parents=True)
    binary.write_text("binary")
    binary.chmod(0o755)
    manifest = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "plugins": {
                    "other@x": [{"installPath": "/ignored"}],
                    "automatised-pipeline@x": [{"installPath": str(install)}],
                }
            }
        )
    )
    assert ap._resolve_command() == {"command": str(binary), "args": []}
    manifest.write_text("bad-json")
    assert ap._resolve_command() is None


def test_ap_bridge_connect_call_retry_and_close(monkeypatch, capsys):
    async def exercise():
        reset_fake_client()
        monkeypatch.setattr(ap, "MCPClient", FakeMCPClient)
        monkeypatch.setattr(ap, "is_enabled", lambda: False)
        disabled = ap.APBridge({"command": "ap"})
        assert not await disabled.connect()
        assert disabled.unavailable_reason == "disabled"
        assert not disabled.available

        monkeypatch.setattr(ap, "is_enabled", lambda: True)
        monkeypatch.setattr(ap, "_resolve_command", lambda: None)
        missing = ap.APBridge()
        assert not await missing.connect()
        assert missing.unavailable_reason == "no_command_resolved"

        bridge = ap.APBridge({"command": "ap"})
        assert await bridge.connect()
        client = FakeMCPClient.instances[-1]
        assert client.config["callTimeoutMs"] == 0
        assert client._extra_allowed_commands == {"node", "automatised-pipeline"}
        assert await bridge.connect()
        assert bridge.available

        response = await bridge.call("health_check")
        assert response["tool"] == "health_check"
        with pytest.raises(ValueError, match="not in allowlist"):
            await bridge.call("delete_everything")

        client.connected = False
        assert await bridge.connect()
        assert FakeMCPClient.instances[-1] is not client

        FakeMCPClient.call_failure = RuntimeError("call failed")
        assert await bridge.call("health_check") is None
        assert "RuntimeError" in bridge.unavailable_reason
        FakeMCPClient.call_failure = None

        current = bridge._client
        await bridge.close()
        assert current.closed
        assert bridge._client is None
        assert not bridge._connected

        FakeMCPClient.connect_failure = RuntimeError("spawn failed")
        failed = ap.APBridge({"command": "ap"})
        assert not await failed.connect()
        assert "spawn failed" in failed.unavailable_reason

    asyncio.run(exercise())
    assert "AP call health_check failed" in capsys.readouterr().err


def test_ap_bridge_convenience_wrappers_include_documented_process_tool(monkeypatch):
    async def exercise():
        bridge = ap.APBridge()
        bridge.call = AsyncMock(return_value={"ok": True})
        assert await bridge.health_check() == {"ok": True}
        await bridge.index_codebase("/src", output_dir="/out", language="python")
        await bridge.query_graph("/graph", "MATCH (n) RETURN n")
        await bridge.get_symbol("/graph", "pkg::symbol")
        await bridge.get_context("/graph", "pkg::symbol")
        await bridge.get_processes("/graph")
        await bridge.resolve_graph("/graph")
        await bridge.cluster_graph("/graph", resolution_param=2.0)
        await bridge.search_codebase("/graph", "needle", limit=4)
        await bridge.detect_changes("/graph", diff_text="diff")
        await bridge.detect_changes(
            "/graph", codebase_path="/src", base_ref="main", head_ref="HEAD"
        )
        await bridge.detect_changes("/graph")
        await bridge.get_impact("/graph", "pkg::symbol")
        await bridge.analyze_codebase("/src", output_dir="/out")
        tools = [call.args[0] for call in bridge.call.await_args_list]
        assert "get_processes" in tools
        assert set(tools) <= ap._AP_TOOLS

    asyncio.run(exercise())


def test_ap_close_tolerates_client_teardown_failure():
    class BrokenClient:
        def close(self):
            raise RuntimeError("already gone")

    bridge = ap.APBridge()
    bridge._client = BrokenClient()
    bridge._connected = True
    asyncio.run(bridge.close())
    assert bridge._client is None
    assert not bridge._connected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("1", True), ("false", False), ("0", False), (" FALSE ", True)],
)
def test_prd_is_enabled(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("CORTEX_PRD_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CORTEX_PRD_ENABLED", value)
    assert prd.is_enabled() is expected


def test_prd_resolve_command_env_and_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEX_PRD_COMMAND", '{"command":"node","args":[]}')
    assert prd._resolve_command()["command"] == "node"
    monkeypatch.setenv("CORTEX_PRD_COMMAND", "bad")
    assert prd._resolve_command() is None
    monkeypatch.setenv("CORTEX_PRD_COMMAND", "[]")
    assert prd._resolve_command() is None

    monkeypatch.delenv("CORTEX_PRD_COMMAND")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "spec-plugin"
    ensure = root / "bin" / "ensure-deps.sh"
    ensure.parent.mkdir(parents=True)
    ensure.write_text("#!/bin/sh")
    manifest = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "plugins": {
                    "prd-spec-generator@legacy": [
                        {"installPath": str(tmp_path / "legacy-plugin")}
                    ],
                    "ai-architect-mcp-spec@cortex-plugins": [
                        {"installPath": str(root)}
                    ],
                }
            }
        )
    )
    resolved = prd._resolve_command()
    assert resolved["command"] == "bash"
    assert resolved["args"] == [str(ensure), str(root)]
    assert resolved["env"]["PRD_GEN_SKILL_CONFIG"].endswith("skill-config.json")
    manifest.write_text("bad")
    assert prd._resolve_command() is None


def test_prd_bridge_connect_call_and_close(monkeypatch, capsys):
    async def exercise():
        reset_fake_client()
        monkeypatch.setattr(prd, "MCPClient", FakeMCPClient)
        monkeypatch.setattr(prd, "is_enabled", lambda: False)
        bridge = prd.PRDBridge({"command": "node"})
        assert not await bridge.connect()
        assert bridge._unavailable_reason == "disabled"

        monkeypatch.setattr(prd, "is_enabled", lambda: True)
        monkeypatch.setattr(prd, "_resolve_command", lambda: None)
        assert not await prd.PRDBridge().connect()

        bridge = prd.PRDBridge({"command": "node"})
        assert await bridge.connect()
        assert await bridge.connect()
        client = bridge._client
        assert client._extra_allowed_commands == {"bash", "node"}
        assert (await bridge.health_check())["tool"] == "check_health"
        with pytest.raises(ValueError, match="not in allowlist"):
            await bridge.call("start_pipeline")

        FakeMCPClient.call_failure = RuntimeError("bad call")
        assert await bridge.call("get_config") is None
        FakeMCPClient.call_failure = None
        await bridge.close()
        assert client.closed

        FakeMCPClient.connect_failure = RuntimeError("bad connect")
        failed = prd.PRDBridge({"command": "node"})
        assert not await failed.connect()

    asyncio.run(exercise())
    err = capsys.readouterr().err
    assert "PRD call get_config failed" in err
    assert "PRD bridge disabled" in err


def test_prd_close_tolerates_client_failure():
    bridge = prd.PRDBridge()
    bridge._client = SimpleNamespace(
        close=lambda: (_ for _ in ()).throw(RuntimeError("gone"))
    )
    bridge._connected = True
    asyncio.run(bridge.close())
    assert bridge._client is None


def test_discover_and_read_prd_artifacts(tmp_path, monkeypatch):
    cwd = tmp_path / "workspace"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: cwd)
    monkeypatch.setattr(Path, "home", lambda: home)

    run_a = cwd / "prd-output" / "run-a"
    run_b = home / "prd-output" / "run-b"
    run_c = home / "Developments" / "prd-output" / "run-c"
    for run in (run_a, run_b, run_c):
        run.mkdir(parents=True)
    (run_a / "01-prd.md").write_text("# PRD")
    (run_a / "04-security.md").write_text("# Security")
    (run_b / "09-tests.md").write_text("# Tests")
    (run_b / "notes.txt").write_text("ignored")

    found = prd.discover_prd_artifacts()
    assert found == [run_a, run_c, run_b]
    graph = prd.read_prd_graph()
    assert len([node for node in graph["nodes"] if node["kind"] == "prd"]) == 3
    assert {node["label"] for node in graph["nodes"]} >= {
        "PRD",
        "Security",
        "Test code",
    }
    assert len(graph["edges"]) == 3


def test_discover_prd_artifacts_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert prd.discover_prd_artifacts() == []
    assert prd.read_prd_graph() == {"nodes": [], "edges": []}
