"""Distribution, plugin compatibility, and MCP handshake identity gates."""

from __future__ import annotations

import asyncio
import json
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

    assert project["project"]["name"] == DISTRIBUTION_NAME
    assert project["project"]["version"] == VERSION
    assert server["name"] == MCP_REGISTRY_ID
    assert server["version"] == VERSION
    assert plugin["version"] == VERSION
    assert plugin["name"] == "cortex-viz"


def test_both_console_names_resolve_to_the_same_entry_point() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]
    assert scripts["hypermnesia-mcp-viz"] == "cortex_viz.__main__:main"
    assert scripts["cortex-viz"] == scripts["hypermnesia-mcp-viz"]


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
            assert client.server_info == {
                "name": DISTRIBUTION_NAME,
                "version": VERSION,
            }
            assert "open_visualization" in client.list_tools()
        finally:
            client.close()

    asyncio.run(exercise())
