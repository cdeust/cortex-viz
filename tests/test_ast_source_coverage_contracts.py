"""Behavioral contracts for the AP-backed workflow AST source."""

from __future__ import annotations

import asyncio
import os
import subprocess
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cortex_viz.errors import McpConnectionError
from cortex_viz.infrastructure import workflow_graph_source_ast as source_mod
from cortex_viz.infrastructure import workflow_graph_source_ast_async as async_mod
from cortex_viz.infrastructure import workflow_graph_source_ast_edges as edge_mod
from cortex_viz.infrastructure.workflow_graph_source_ast import WorkflowGraphASTSource
from cortex_viz.infrastructure.workflow_graph_source_native_ast import (
    WorkflowGraphNativeASTSource,
)


async def collect(agen):
    return [item async for item in agen]


class SymbolBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def call(self, tool, args=None):
        args = args or {}
        self.calls.append((tool, args))
        query = args.get("query", "")
        if "MATCH (s:Function)" in query:
            return {
                "columns": ["qualified_name", "name"],
                "rows": [
                    ["pkg/mod.py::run", "run"],
                    ["other.py::skip", "skip"],
                    ["missing_separator", "bad"],
                    [None, "none"],
                ],
            }
        if "MATCH (s:Method)" in query:
            return [{"qualified_name": "pkg/mod.py::Thing.go", "name": "go"}]
        if "MATCH (s:Import)" in query:
            return {
                "columns": ["qualified_name", "name"],
                "rows": [["pkg/mod.py::json", "json"]],
            }
        return {"columns": ["qualified_name", "name"], "rows": []}

    async def search_codebase(self, graph_path, query, *, limit):
        assert graph_path == "/graph"
        assert query == "needle"
        assert limit == 3
        return [
            {
                "qualified_name": "pkg.mod.run",
                "file_path": "pkg/mod.py",
                "score": "0.75",
                "signature": "run()",
            },
            {"name": "fallback", "abs_path": "/tmp/f.py"},
            {"file_path": "ignored.py"},
        ]

    async def close(self):
        self.closed = True


class EdgeBridge:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call(self, tool, args=None):
        assert tool == "query_graph"
        args = args or {}
        self.calls.append(args)
        query = args.get("query", "")
        if "Calls_Function_Function" in query:
            return [
                {
                    "src_name": "pkg/mod.py::run",
                    "dst_name": "pkg/mod.py::helper",
                    "confidence": "0.8",
                    "reason": "'exact'",
                },
                {
                    "src_name": "pkg/mod.py::run",
                    "dst_name": "pkg/mod.py::bad",
                    "confidence": "not-a-number",
                    "reason": None,
                },
                {"src_name": "pkg/mod.py::run", "dst_name": ""},
                {"src_name": "other.py::run", "dst_name": "other.py::helper"},
            ]
        if "Imports_File_Function" in query:
            return [
                {
                    "src_name": "pkg/mod.py",
                    "dst_name": "dep.py::symbol",
                    "confidence": 0.5,
                    "reason": '"resolver"',
                },
                {"src_name": "other.py", "dst_name": "dep.py::skip"},
            ]
        if "Defines_File_Import" in query:
            return [{"src_name": "pkg/mod.py", "dst_name": "pkg/mod.py::json"}]
        if "HasMethod_Struct_Method" in query:
            return [
                {
                    "src_name": "pkg/mod.py::Thing",
                    "dst_name": "pkg/mod.py::Thing.go",
                }
            ]
        return []

    async def close(self):
        return None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, []),
        ("bad", []),
        ([{"x": 1}, "bad"], [{"x": 1}]),
        ({"status": "error", "rows": [{"x": 1}]}, []),
        (
            {"columns": ["a", "b"], "rows": [[1, 2], [1], {"a": 3}]},
            [{"a": 1, "b": 2}, {"a": 3}],
        ),
        ({"content": [{"type": "text", "text": '[{"x": 1}]'}]}, [{"x": 1}]),
        ({"content": [{"type": "text", "text": "bad-json"}]}, []),
        ({"data": [{"x": 1}, 2]}, [{"x": 1}]),
        (
            {"content": [{"type": "text", "text": "{}"}]},
            [{"type": "text", "text": "{}"}],
        ),
        ({"other": 1}, []),
    ],
)
def test_as_list_normalizes_supported_ap_shapes(payload, expected):
    assert async_mod._as_list(payload) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Function", "function"),
        ("Method", "method"),
        ("Struct", "class"),
        ("Protocol", "class"),
        ("Module", "module"),
        ("Namespace", "module"),
        ("Constant", "constant"),
        ("Variable", "constant"),
        ("Custom", "custom"),
    ],
)
def test_symbol_type_palette_is_bounded(label, expected):
    assert async_mod._symbol_type_from_label(label) == expected


def test_repo_relative_and_tail_matching(monkeypatch):
    assert async_mod._repo_relative_for_match("./pkg/mod.py") == "pkg/mod.py"
    assert async_mod._tail_matches(["pkg/mod.py"], "mod.py")
    assert async_mod._tail_matches(["mod.py"], "pkg/mod.py")
    assert not async_mod._tail_matches(["one.py"], "two.py")

    monkeypatch.setattr(os.path, "realpath", lambda _path: "/repo/pkg/mod.py")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="/repo\n"),
    )
    assert async_mod._repo_relative_for_match("/alias/mod.py") == "pkg/mod.py"

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert async_mod._repo_relative_for_match("/alias/mod.py") == ""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git missing")),
    )
    assert async_mod._repo_relative_for_match("/alias/mod.py") == ""


def test_symbol_batches_filter_normalize_and_stream_queries():
    bridge = SymbolBridge()
    batches = asyncio.run(
        collect(async_mod._symbol_batches_async(bridge, "/graph", ["pkg/mod.py"]))
    )
    rows = [row for batch in batches for row in batch]
    assert {row["qualified_name"] for row in rows} == {
        "pkg/mod.py::run",
        "pkg/mod.py::Thing.go",
        "pkg/mod.py::json",
    }
    assert rows[0]["file_path"] == "pkg/mod.py"
    assert len(bridge.calls) == len(async_mod._SYMBOL_LABELS)
    assert "STARTS WITH 'pkg/mod.py::'" in bridge.calls[0][1]["query"]
    import_query = next(
        args["query"] for _, args in bridge.calls if "MATCH (s:Import)" in args["query"]
    )
    assert "s.id STARTS WITH" in import_query


def test_symbol_batches_load_all_without_where_clause():
    bridge = SymbolBridge()
    batches = asyncio.run(
        collect(async_mod._symbol_batches_async(bridge, "/graph", []))
    )
    assert batches
    assert all(" WHERE " not in args["query"] for _, args in bridge.calls)


def test_edge_batches_cover_provenance_structural_and_path_filters():
    bridge = EdgeBridge()
    batches = asyncio.run(
        collect(edge_mod._edge_batches_async(bridge, "/graph", ["/repo/pkg/mod.py"]))
    )
    rows = [row for batch in batches for row in batch]
    assert len(bridge.calls) == len(edge_mod._AP_REL_TABLES)
    assert any(row["kind"] == "calls" and row["confidence"] == 0.8 for row in rows)
    assert any(row["kind"] == "calls" and row["confidence"] is None for row in rows)
    assert any(row["reason"] == "exact" for row in rows)
    assert any(row["reason"] == "resolver" for row in rows)
    structural = next(row for row in rows if row["kind"] == "member_of")
    assert structural["confidence"] is None
    assert any(row["dst_name"] == "pkg/mod.py::json" for row in rows)

    queries = [call["query"] for call in bridge.calls]
    assert any("src.id AS src_name" in query for query in queries)
    assert any("dst.id AS dst_name" in query for query in queries)
    assert any("r.confidence" in query for query in queries)


def test_edge_batches_accept_all_paths_when_filter_empty():
    rows = asyncio.run(
        collect(edge_mod._edge_batches_async(EdgeBridge(), "/graph", []))
    )
    flattened = [row for batch in rows for row in batch]
    assert any(row["src_file"] == "other.py" for row in flattened)


def test_sync_loop_runs_coroutines_streams_and_forwards_none():
    owner = async_mod._SyncLoop()
    try:
        assert owner.run(asyncio.sleep(0, result=7)) == 7

        async def values():
            yield 1
            yield None
            yield 3

        assert list(owner.run_iter(values())) == [1, None, 3]
        loop = owner._ensure_loop()
        assert owner._ensure_loop() is loop
    finally:
        owner.close()
    owner.close()
    assert owner._loop is None


def test_sync_loop_timeout_paths_cancel_future(monkeypatch):
    class TimedOutFuture:
        def __init__(self) -> None:
            self.cancelled = False

        def result(self, *, timeout):
            assert timeout == 0.01
            raise FutureTimeoutError

        def cancel(self):
            self.cancelled = True

    owner = async_mod._SyncLoop()
    fake_loop = MagicMock()
    fake_loop.is_closed.return_value = False
    owner._loop = fake_loop
    owner._thread = MagicMock()
    created: list[TimedOutFuture] = []

    def submit(coro, _loop):
        coro.close()
        future = TimedOutFuture()
        created.append(future)
        return future

    monkeypatch.setattr(async_mod, "_ap_sync_timeout_s", lambda: 0.01)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", submit)
    with pytest.raises(McpConnectionError, match="reader-thread call"):
        owner.run(asyncio.sleep(0))

    async def values():
        yield 1

    stream = values()
    with pytest.raises(McpConnectionError, match="reader-thread step"):
        next(owner.run_iter(stream))
    asyncio.run(stream.aclose())
    assert all(future.cancelled for future in created)


def test_sync_loop_teardown_is_best_effort():
    class BrokenLoop:
        def is_closed(self):
            return False

        def stop(self):
            return None

        def call_soon_threadsafe(self, _callback):
            raise RuntimeError("stopping")

        def close(self):
            raise RuntimeError("closed")

    class BrokenThread:
        def join(self, *, timeout):
            raise RuntimeError("joined")

    owner = async_mod._SyncLoop()
    owner._loop = BrokenLoop()
    owner._thread = BrokenThread()
    owner.close()
    assert owner._loop is None


def test_legacy_run_uses_asyncio_run_and_bounds_cross_loop_wait(monkeypatch):
    assert async_mod._run(asyncio.sleep(0, result="ok")) == "ok"

    class RunningLoop:
        def is_running(self):
            return True

    class ResultFuture:
        def __init__(self, value=None, failure=None) -> None:
            self.value = value
            self.failure = failure

        def result(self, *, timeout):
            assert timeout == 0.01
            if self.failure:
                raise self.failure
            return self.value

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: RunningLoop())
    monkeypatch.setattr(async_mod, "_ap_sync_timeout_s", lambda: 0.01)

    def success(coro, _loop):
        coro.close()
        return ResultFuture("cross-loop")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", success)
    assert async_mod._run(asyncio.sleep(0)) == "cross-loop"

    def timeout(coro, _loop):
        coro.close()
        return ResultFuture(failure=FutureTimeoutError())

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", timeout)
    with pytest.raises(McpConnectionError, match="cross-loop call"):
        async_mod._run(asyncio.sleep(0))


def test_workflow_source_streams_loads_searches_and_verifies(monkeypatch):
    bridge = SymbolBridge()
    source = WorkflowGraphASTSource(bridge=bridge)
    monkeypatch.setattr(source_mod, "is_enabled", lambda: True)
    monkeypatch.setattr(source_mod, "resolve_graph_paths", lambda: ["/graph"])
    monkeypatch.setattr(source_mod, "resolve_graph_path", lambda: "/graph")
    monkeypatch.setattr(source_mod, "_edge_batches_async", edge_mod._edge_batches_async)
    try:
        assert source.enabled()
        symbols = source.load_symbols(["pkg/mod.py"])
        assert any(row["qualified_name"] == "pkg/mod.py::run" for row in symbols)

        edge_bridge = EdgeBridge()
        source._bridge = edge_bridge
        edges = source.load_ast_edges(["pkg/mod.py"])
        assert any(row["kind"] == "calls" for row in edges)
        assert asyncio.run(source._load_edges_async("/graph", ["pkg/mod.py"]))

        source._bridge = bridge
        assert asyncio.run(source._load_symbols_async("/graph", ["pkg/mod.py"]))
        results = source.search_codebase("needle", limit=3)
        assert [row["qualified_name"] for row in results] == [
            "pkg.mod.run",
            "fallback",
        ]
        assert results[0]["id"] == "symbol:pkg/mod.py::pkg.mod.run"

        verified = source.verify_symbols(["pkg.mod.run", "Thing.go", "missing", ""])
        assert verified["pkg.mod.run"]
        assert verified["Thing.go"]
        assert not verified["missing"]
    finally:
        source.close()
    assert bridge.closed


def test_workflow_source_degrades_when_disabled_or_unconfigured(monkeypatch):
    source = WorkflowGraphASTSource(bridge=SymbolBridge())
    monkeypatch.setattr(source_mod, "is_enabled", lambda: False)
    assert list(source.iter_symbols(["x.py"])) == []
    assert list(source.iter_ast_edges(["x.py"])) == []
    assert source.search_codebase("query") == []
    assert source.search_codebase(" ") == []
    assert source.verify_symbols(["x"]) == {"x": False}

    monkeypatch.setattr(source_mod, "is_enabled", lambda: True)
    monkeypatch.setattr(source_mod, "resolve_graph_paths", lambda: [])
    monkeypatch.setattr(source_mod, "resolve_graph_path", lambda: None)
    assert list(source.iter_symbols(["x.py"])) == []
    assert list(source.iter_ast_edges(["x.py"])) == []
    assert source.search_codebase("query") == []
    assert source.verify_symbols(["x"]) == {"x": False}

    monkeypatch.setattr(source_mod, "resolve_graph_path", lambda: "/graph")
    assert source.verify_symbols(["", ""]) == {}
    source.close()


def test_workflow_source_skips_one_failed_graph(monkeypatch):
    source = WorkflowGraphASTSource(bridge=SymbolBridge())
    monkeypatch.setattr(source_mod, "is_enabled", lambda: True)
    monkeypatch.setattr(source_mod, "resolve_graph_paths", lambda: ["bad", "good"])

    async def symbol_batches(_bridge, graph_path, _paths):
        if graph_path == "bad":
            raise RuntimeError("corrupt")
        yield [{"qualified_name": "good::symbol"}]

    async def edge_batches(_bridge, graph_path, _paths):
        if graph_path == "bad":
            raise RuntimeError("corrupt")
        yield [{"kind": "calls"}]

    monkeypatch.setattr(source_mod, "_symbol_batches_async", symbol_batches)
    monkeypatch.setattr(source_mod, "_edge_batches_async", edge_batches)
    assert source.load_symbols([]) == [{"qualified_name": "good::symbol"}]
    assert source.load_ast_edges([]) == [{"kind": "calls"}]

    async def bad_close():
        raise RuntimeError("gone")

    source._bridge.close = bad_close
    source.close()


def test_native_ast_source_is_explicit_noop():
    native = WorkflowGraphNativeASTSource()
    assert native.load_symbols(["x.py"]) == []
    assert native.load_ast_edges(["x.py"]) == []
