"""Launch the console entry point and query a real file-backed methodology graph.

Sources: cortex_viz/server/mcp_tools.py, infrastructure/profile_store.py;
MCP 1.28.1 client/stdio/__init__.py and client/session.py from uv.lock.
This checks stdio and disk reads; it does not claim browser or PostgreSQL E2E.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(command: str, home: Path, version: str) -> None:
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("PYTHON")
    }
    env.update(HOME=str(home), USERPROFILE=str(home))
    parameters = StdioServerParameters(command=command, args=[], env=env, cwd=str(home))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "hypermnesia-mcp-viz"
            assert initialized.serverInfo.version == version
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "open_visualization",
                "get_methodology_graph",
            }
            response = await session.call_tool(
                "get_methodology_graph", {"domain": "ci"}
            )
            assert not response.isError, response
            graph = response.structuredContent
            if graph is None:
                graph = json.loads(response.content[0].text)
            assert any(
                node["domain"] == "ci" and node["label"] == "CI fixture"
                for node in graph["nodes"]
            ), graph
            assert isinstance(graph["edges"], list)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="viz-mcp-") as temporary:
        home = Path(temporary)
        directory = home / ".claude" / "methodology" / "domains"
        directory.mkdir(parents=True)
        # Source: profile_store.py v2 split-domain persistence format.
        (directory.parent / "index.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "domain_ids": ["ci"],
                    "globalStyle": None,
                }
            )
        )
        (directory / "ci.json").write_text(json.dumps({"label": "CI fixture"}))
        asyncio.run(probe(str(Path(sys.argv[1]).resolve()), home, version))
    print(
        "Installed command answered initialize, discovery and file-backed graph query"
    )


if __name__ == "__main__":
    main()
