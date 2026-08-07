"""hypermnesia-mcp-viz MCP server entry point.

A standalone visualization MCP for Cortex. Reads Cortex's shared PostgreSQL
store (read-only, via MemoryReader) and the ~/.claude artifacts; serves the
neural-graph galaxy UI and methodology map. Memory/recall/wiki tools remain in
the Cortex MCP — this server is the visualization surface only.

Run: ``python -m cortex_viz`` (stdio MCP transport), or the canonical
``hypermnesia-mcp-viz`` console script.
"""

from __future__ import annotations

import signal
import sys

from fastmcp import FastMCP

from cortex_viz.identity import DISTRIBUTION_NAME, VERSION
from cortex_viz.server import mcp_tools

mcp = FastMCP(
    name=DISTRIBUTION_NAME,
    version=VERSION,
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
        # Shutdown is already underway and sys.exit(0) runs regardless; a server
        # that is already down must not turn the signal handler into a traceback.
        pass
    sys.exit(0)


def _export(out_dir: str) -> int:
    """``--export <dir>``: write the static wiki bundle and report on it.

    A one-shot command, not a server mode: it prints a summary and exits, so it
    is usable from a shell or a release script without an MCP host. Exits
    non-zero when the artifact carries a remote reference or a local asset went
    missing — a bundle that needs the network is the one thing #112 forbids, and
    reporting it as success would hide exactly the failure that matters.
    """
    from pathlib import Path

    from cortex_viz.handlers.wiki_export_bundle import export_wiki
    from cortex_viz.infrastructure.db_probe import open_store_or_none
    from cortex_viz.server.http_standalone_wiki import _dispatch_get

    store = open_store_or_none()
    ui_root = Path(__file__).resolve().parent.parent / "ui"
    manifest = export_wiki(
        out_dir=Path(out_dir),
        ui_root=ui_root,
        respond=lambda path, params: _dispatch_get(store, path, params),
    )
    print(
        f"wiki export: {manifest['path']} "
        f"({manifest['bytes'] / 1024:.0f} KB, {manifest['page_count']} pages, "
        f"{manifest['request_count']} responses)",
        file=sys.stderr,
    )
    print(
        "  renders as source (not bundled): "
        + ", ".join(manifest["omitted_capabilities"]),
        file=sys.stderr,
    )
    failed = False
    for label, items in (
        ("remote references", manifest["remote_references"]),
        ("missing local assets", manifest["missing_assets"]),
    ):
        if items:
            failed = True
            print(f"  ERROR {label}: {', '.join(items)}", file=sys.stderr)
    return 1 if failed else 0


def main() -> None:
    # argparse would be the reflex, but this entry point's contract is "stdio
    # MCP server" and every argument it will ever take is a one-shot side door.
    # A two-line check keeps the server path free of parser setup.
    if len(sys.argv) >= 2 and sys.argv[1] == "--export":
        if len(sys.argv) < 3:
            print("usage: python -m cortex_viz --export <dir>", file=sys.stderr)
            sys.exit(2)
        sys.exit(_export(sys.argv[2]))
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
