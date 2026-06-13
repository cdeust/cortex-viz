"""cortex-viz MCP server entry point.

A standalone visualization MCP for Cortex. Reads Cortex's shared PostgreSQL
store (read-only, via MemoryReader) and the ~/.claude artifacts; serves the
neural-graph galaxy UI and methodology map. Memory/recall/wiki tools remain in
the Cortex MCP — this server is the visualization surface only.

Run: ``python -m cortex_viz`` (stdio MCP transport), or the ``cortex-viz``
console script.
"""

from __future__ import annotations

import signal
import sys

from fastmcp import FastMCP

from cortex_viz.server import mcp_tools

mcp = FastMCP(
    name="cortex-viz",
    version="0.1.0",
    instructions=(
        "Visualization MCP for Cortex. Call open_visualization to launch the "
        "neural-graph galaxy in the browser, or get_methodology_graph for the "
        "methodology-map graph data. Reads Cortex's shared PostgreSQL store "
        "read-only; it does not write memories."
    ),
)

mcp_tools.register(mcp)


def _shutdown(sig=None, frame=None) -> None:
    from cortex_viz.server.http_server import shutdown_server

    try:
        shutdown_server()
    except Exception:
        pass
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
