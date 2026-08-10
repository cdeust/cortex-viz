"""Behavioral contracts for the trace-impact Cypher fetchers (issue #85).

Each fetcher in ``trace_impact_graph`` issues exactly one targeted query and
returns raw rows/bool — no shaping. These tests exercise every fetcher in
isolation against a bare stub exposing ``query_graph``, asserting both the
Cypher branch selected (by a distinguishing substring, mirroring the style
already used by ``test_trace_impact_coverage_contracts.ImpactBridge``) and
the row-normalization each function performs.
"""

from __future__ import annotations

import asyncio

from cortex_viz.server import trace_impact_graph as graph


class RecordingBridge:
    """Stub bridge: records every Cypher query issued and replies by shape."""

    def __init__(self):
        self.queries: list[str] = []

    async def query_graph(self, _graph_path, cypher):
        self.queries.append(cypher)
        # AP's columns/rows response shape (exercises _as_list's zip path).
        if "RETURN f.id AS id" in cypher:
            return {"columns": ["id"], "rows": [["pkg/file.py"]]}
        if "MATCH (s:Function) WHERE s.qualified_name" in cypher:
            return {
                "columns": ["name"],
                "rows": [["pkg/file.py::a"], ["pkg/file.py::b"], [None]],
            }
        if "Imports_File_File" in cypher and cypher.startswith(
            "MATCH (f:File)-[r:Imports_File_File]"
        ):
            return [{"name": "outbound-import.py", "conf": 0.9}]
        if "Imports_File_File" in cypher:
            return [{"name": "inbound-import.py", "conf": 0.8}]
        if "References_File_File" in cypher and cypher.startswith(
            "MATCH (f:File)-[r:References_File_File]"
        ):
            return [{"name": "outbound-ref.md", "conf": 1}]
        if "References_File_File" in cypher:
            return [{"name": "inbound-ref.md", "conf": 1}]
        if "MATCH (p:Process)" in cypher:
            return [{"entry": "pkg/file.py::main", "kind": "main", "depth": 1, "n": 3}]
        return []


class EmptyPresenceBridge:
    async def query_graph(self, _graph_path, _cypher):
        return []


def test_query_normalizes_columns_rows_and_plain_list_shapes():
    bridge = RecordingBridge()
    rows = asyncio.run(graph._query(bridge, "/g", "MATCH (p:Process) RETURN 1"))
    assert rows == [{"entry": "pkg/file.py::main", "kind": "main", "depth": 1, "n": 3}]
    assert bridge.queries == ["MATCH (p:Process) RETURN 1"]


def test_file_present_true_and_false():
    assert asyncio.run(graph._file_present(RecordingBridge(), "/g", "pkg/file.py"))
    assert not asyncio.run(
        graph._file_present(EmptyPresenceBridge(), "/g", "pkg/file.py")
    )


def test_file_members_extracts_names_and_drops_null_rows():
    names = asyncio.run(graph._file_members(RecordingBridge(), "/g", "pkg/file.py"))
    assert names == ["pkg/file.py::a", "pkg/file.py::b"]


def test_file_import_edges_returns_outgoing_then_incoming():
    bridge = RecordingBridge()
    outgoing, incoming = asyncio.run(
        graph._file_import_edges(bridge, "/g", "pkg/file.py")
    )
    assert outgoing == [{"name": "outbound-import.py", "conf": 0.9}]
    assert incoming == [{"name": "inbound-import.py", "conf": 0.8}]
    # Order preserved: outgoing query issued before incoming.
    import_queries = [q for q in bridge.queries if "Imports_File_File" in q]
    assert import_queries[0].startswith("MATCH (f:File)-[r:Imports_File_File]")
    assert import_queries[1].startswith("MATCH (s:File)-[r:Imports_File_File]")


def test_file_reference_edges_returns_outgoing_then_incoming():
    bridge = RecordingBridge()
    outgoing, incoming = asyncio.run(
        graph._file_reference_edges(bridge, "/g", "pkg/file.py")
    )
    assert outgoing == [{"name": "outbound-ref.md", "conf": 1}]
    assert incoming == [{"name": "inbound-ref.md", "conf": 1}]
    ref_queries = [q for q in bridge.queries if "References_File_File" in q]
    assert ref_queries[0].startswith("MATCH (f:File)-[r:References_File_File]")
    assert ref_queries[1].startswith("MATCH (s:File)-[r:References_File_File]")


def test_entry_point_process_rows_filters_to_this_files_entry_points():
    bridge = RecordingBridge()
    rows = asyncio.run(graph._entry_point_process_rows(bridge, "/g", "pkg/file.py"))
    assert rows == [{"entry": "pkg/file.py::main", "kind": "main", "depth": 1, "n": 3}]
    assert "STARTS WITH 'pkg/file.py::'" in bridge.queries[-1]
