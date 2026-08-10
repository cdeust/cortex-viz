"""Cypher query set for one file's impact orchestration (``trace_impact.py``).

Split out of ``_impact_for_graph`` / its nested ``_run`` (§4 500-line file /
50-line function limits, issue #85). Every function here is a *fetcher*: it
issues one targeted Cypher query against a code-graph via the AP bridge and
returns raw rows (or a bool for the presence gate) — no result-shaping. The
result-shaping (``_file_edges``, ``_rollup``, ``_processes_from``, ...)
stays in ``trace_impact_directions``; the decision of which member-direction
strategy to use (typed fan-out vs. this module's Cypher fallback) stays in
``trace_impact`` because it depends on ``_AST_CONTEXT_CAP``, defined there.

Each fetcher takes ``(bridge, graph_path, esc)`` where ``esc`` is the
caller-escaped repo-relative file path (``rel_path.replace("'", "")``), so
every function here is testable with a bare bridge stub exposing
``query_graph`` — no live AP bridge, no warm AST source.
"""

from __future__ import annotations


async def _query(bridge, graph_path: str, cypher: str) -> list[dict]:
    """Run one Cypher query on ``graph_path`` and normalize the rows.

    LEGITIMATE query_graph use throughout this module: AP exposes no typed
    tool for file-presence, file-membership, direct File→File edges, or
    file-scoped entry-point processes (source: docstrings on each caller in
    ``trace_impact._collect_file_impact``).
    """
    from cortex_viz.infrastructure.workflow_graph_source_ast import _as_list

    rows = await bridge.query_graph(graph_path, cypher)
    return _as_list(rows)


async def _file_present(bridge, graph_path: str, esc: str) -> bool:
    """Cheap presence gate: is ``esc`` a File node in this graph at all?"""
    rows = await _query(
        bridge,
        graph_path,
        f"MATCH (f:File) WHERE f.id = '{esc}' RETURN f.id AS id LIMIT 1",
    )
    return bool(rows)


async def _file_members(bridge, graph_path: str, esc: str) -> list[str]:
    """Qualified names of every Function symbol defined in ``esc``.

    This is the N that drives the typed ``get_context`` fan-out cap in
    ``trace_impact._member_directions``.
    """
    rows = await _query(
        bridge,
        graph_path,
        f"MATCH (s:Function) WHERE s.qualified_name STARTS WITH '{esc}::' "
        "RETURN DISTINCT s.qualified_name AS name LIMIT 200",
    )
    return [r.get("name") for r in rows if r.get("name")]


async def _file_import_edges(
    bridge, graph_path: str, esc: str
) -> tuple[list[dict], list[dict]]:
    """Direct File→File ``Imports_File_File`` edges: (outgoing, incoming).

    AP all-file indexing (>= 0.2.0) — ``.js`` import/require. No typed tool
    covers non-AST direct file edges.
    """
    outgoing = await _query(
        bridge,
        graph_path,
        "MATCH (f:File)-[r:Imports_File_File]->(d:File) "
        f"WHERE f.id = '{esc}' "
        "RETURN DISTINCT d.id AS name, r.confidence AS conf LIMIT 200",
    )
    incoming = await _query(
        bridge,
        graph_path,
        "MATCH (s:File)-[r:Imports_File_File]->(f:File) "
        f"WHERE f.id = '{esc}' "
        "RETURN DISTINCT s.id AS name, r.confidence AS conf LIMIT 200",
    )
    return outgoing, incoming


async def _file_reference_edges(
    bridge, graph_path: str, esc: str
) -> tuple[list[dict], list[dict]]:
    """Direct File→File ``References_File_File`` edges: (outgoing, incoming).

    AP all-file indexing — Markdown/doc links. No typed tool covers these.
    """
    outgoing = await _query(
        bridge,
        graph_path,
        "MATCH (f:File)-[r:References_File_File]->(d:File) "
        f"WHERE f.id = '{esc}' "
        "RETURN DISTINCT d.id AS name, r.confidence AS conf LIMIT 200",
    )
    incoming = await _query(
        bridge,
        graph_path,
        "MATCH (s:File)-[r:References_File_File]->(f:File) "
        f"WHERE f.id = '{esc}' "
        "RETURN DISTINCT s.id AS name, r.confidence AS conf LIMIT 200",
    )
    return outgoing, incoming


async def _entry_point_process_rows(bridge, graph_path: str, esc: str) -> list[dict]:
    """Causal-chain processes ENTERED from this file.

    AP's ``get_processes`` is graph-wide, not file-scoped; this targeted
    Cypher filters to entry points whose ``entry_point_id`` (``file::symbol``)
    starts with this file. Raw rows — ``trace_impact_directions._processes_from``
    does the shaping.
    """
    return await _query(
        bridge,
        graph_path,
        f"MATCH (p:Process) WHERE p.entry_point_id STARTS WITH '{esc}::' "
        "RETURN DISTINCT p.entry_point_id AS entry, p.entry_kind AS kind, "
        "p.depth AS depth, p.symbol_count AS n "
        "ORDER BY p.symbol_count DESC LIMIT 40",
    )
