"""Phase 5 acceptance: the cortex-viz MCP app builds and registers its tools.

Verifies the FastMCP entry point wires the visualization tools without needing
a live database (tool registration is import-time; handler execution is not
exercised here). This is the "cortex-viz MCP loads" half of the extraction's
acceptance bar.
"""

from __future__ import annotations

import asyncio

import pytest


def _tool_names(mcp) -> set[str]:
    """Return registered tool names (FastMCP ``list_tools``)."""
    res = mcp.list_tools()
    if asyncio.iscoroutine(res):
        res = asyncio.run(res)
    if isinstance(res, dict):
        return set(res.keys())
    return {getattr(t, "name", t) for t in res}


def test_mcp_app_builds_and_registers_tools() -> None:
    import cortex_viz.__main__ as app

    assert app.mcp is not None
    names = _tool_names(app.mcp)
    assert "open_visualization" in names
    assert "get_methodology_graph" in names
    # Memory/recall/wiki tools belong to the Cortex MCP, not the viz MCP.
    assert "remember" not in names
    assert "recall" not in names


def test_register_is_idempotent_import() -> None:
    """Importing the entry point twice must not raise (module-level register)."""
    import importlib

    import cortex_viz.__main__ as app

    importlib.reload(app)  # re-runs register() on a fresh FastMCP
    assert app.mcp is not None


def test_safe_handler_catches_errors() -> None:
    from cortex_viz.tool_error_handler import safe_handler

    async def boom(args):
        raise ValueError("nope")

    out = asyncio.run(
        safe_handler(boom, {}, tool_name="t")
    )
    assert out["error"] == "ValueError"
    assert out["message"] == "nope"
    assert out["tool"] == "t"


def test_safe_handler_returns_dict_verbatim() -> None:
    from cortex_viz.tool_error_handler import safe_handler

    async def ok(args):
        return {"ok": True, "echo": args.get("x")}

    out = asyncio.run(
        safe_handler(ok, {"x": 7}, tool_name="t")
    )
    assert out == {"ok": True, "echo": 7}
