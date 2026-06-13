"""No-op native-AST graph source — thin-viz boundary stub.

In Cortex, ``WorkflowGraphNativeASTSource`` parses source files locally with
tree-sitter to enrich the codebase graph with symbol depth for files the
automatised-pipeline (AP) hasn't indexed. That local parser is a full
subsystem (``ast_extractors*`` + ``codebase_*``, 12 modules) whose job AP
already does. Per the thin-viz extraction decision (do not duplicate Cortex
subsystems; delegate to the sibling MCP), cortex-viz does NOT bundle the local
parser — codebase symbols come from AP via ``workflow_graph_source_ast``.

This stub preserves the interface so ``handlers/workflow_graph`` imports and
runs unchanged. The viz's graph build passes ``defer_native_ast=True`` (see
that handler), so this source is never exercised on the hot path; when a
caller does invoke it (non-deferred mode), it returns empty rather than
raising — the codebase graph then shows AP-indexed symbols without the local
parse enrichment. This is a deliberate, documented capability reduction, not a
silent failure: enrichment is delegated to AP, and its absence degrades detail,
never correctness.
"""

from __future__ import annotations

from typing import Any, Iterable


class WorkflowGraphNativeASTSource:
    """Interface-compatible no-op. Local AST enrichment is delegated to AP."""

    def load_symbols(self, file_paths: Iterable[str]) -> list[dict[str, Any]]:
        # Local tree-sitter parsing is not bundled in cortex-viz; AP supplies
        # codebase symbols via workflow_graph_source_ast.
        return []

    def load_ast_edges(self, file_paths: Iterable[str]) -> list[dict[str, Any]]:
        return []
