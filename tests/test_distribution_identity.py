"""Distribution, plugin compatibility, and MCP handshake identity gates."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import tomllib

from cortex_viz.identity import DISTRIBUTION_NAME, MCP_REGISTRY_ID, VERSION
from cortex_viz.infrastructure.mcp_client import MCPClient

ROOT = Path(__file__).resolve().parents[1]


def test_source_manifests_share_one_versioned_identity() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    server = json.loads((ROOT / "server.json").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    claude_marketplace = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )
    codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
    codex_marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    gemini = json.loads((ROOT / "gemini-extension.json").read_text())

    assert project["project"]["name"] == DISTRIBUTION_NAME
    assert project["project"]["version"] == VERSION
    assert server["name"] == MCP_REGISTRY_ID
    assert server["version"] == VERSION
    assert plugin["version"] == VERSION
    assert plugin["name"] == DISTRIBUTION_NAME
    assert DISTRIBUTION_NAME in plugin["mcpServers"]
    assert claude_marketplace["name"] == f"{DISTRIBUTION_NAME}-marketplace"
    assert claude_marketplace["plugins"][0]["name"] == DISTRIBUTION_NAME
    assert codex["name"] == DISTRIBUTION_NAME
    assert DISTRIBUTION_NAME in codex["mcpServers"]
    assert codex_marketplace["name"] == f"{DISTRIBUTION_NAME}-marketplace"
    assert codex_marketplace["plugins"][0]["name"] == DISTRIBUTION_NAME
    assert gemini["name"] == DISTRIBUTION_NAME
    assert gemini["version"] == VERSION
    assert DISTRIBUTION_NAME in gemini["mcpServers"]


def test_only_canonical_console_name_is_published() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]
    assert scripts["hypermnesia-mcp-viz"] == "cortex_viz.__main__:main"
    assert "cortex-viz" not in scripts


def test_breaking_plugin_migration_is_explicit() -> None:
    readme = (ROOT / "README.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    for text in (readme, changelog):
        assert "claude plugin uninstall cortex-viz@cortex-plugins" in text
        assert "plugin:cortex-viz:*" in text
        assert "plugin:hypermnesia-mcp-viz:*" in text
        assert "hypermnesia-mcp-viz" in text
    assert "cdeust/Cortex#351" in changelog


def test_artifact_guard_remains_active_under_python_optimization() -> None:
    guard_program = "; ".join(
        [
            "from scripts.check_distribution_artifact import require",
            "require(False, 'optimization guard')",
        ]
    )
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            guard_program,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "optimization guard" in result.stderr


def test_stdio_handshake_advertises_canonical_identity() -> None:
    async def exercise() -> None:
        client = MCPClient(
            {
                "command": sys.executable,
                "args": ["-m", "cortex_viz"],
                "connectTimeoutMs": 20_000,
            }
        )
        try:
            await client.connect()
            assert client.server_info["name"] == DISTRIBUTION_NAME
            assert client.server_info["version"] == VERSION
            assert "open_visualization" in client.list_tools()
        finally:
            client.close()

    asyncio.run(exercise())
