"""MCP tool registration for the cortex-viz server.

Registers the visualization tools whose handlers live in cortex-viz:
  * open_visualization   — launch the bundled neural-graph UI (galaxy view).
  * get_methodology_graph — return methodology-map graph data.

Memory/recall/wiki tools stay in the Cortex MCP. Graph-data tools that still
depend on Cortex's storage layer (query_workflow_graph, graph_inspect,
ingest_codebase_graph) are intentionally NOT registered here yet — the galaxy
graph is served over HTTP by the server open_visualization launches.
"""

from __future__ import annotations

from fastmcp import FastMCP

from cortex_viz.handlers import get_methodology_graph, open_visualization
from cortex_viz.handlers._tool_meta import tool_kwargs
from cortex_viz.tool_error_handler import safe_handler


def register(mcp: FastMCP) -> None:
    """Register cortex-viz tools on the FastMCP instance."""
    _register_open_visualization(mcp)
    _register_get_methodology_graph(mcp)


def _register_open_visualization(mcp: FastMCP) -> None:
    @mcp.tool(
        name="open_visualization",
        **tool_kwargs(open_visualization.schema),
    )
    async def tool_open_visualization(
        domain: str | None = None, view: str = "galaxy"
    ) -> dict:
        return await safe_handler(
            open_visualization.handler,
            {"domain": domain, "view": view},
            tool_name="open_visualization",
        )


def _register_get_methodology_graph(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_methodology_graph",
        **tool_kwargs(get_methodology_graph.schema),
    )
    async def tool_get_methodology_graph(domain: str | None = None) -> dict:
        return await safe_handler(
            get_methodology_graph.handler,
            {"domain": domain},
            tool_name="get_methodology_graph",
        )
