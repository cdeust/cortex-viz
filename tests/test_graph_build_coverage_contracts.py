"""Behavioral contracts for graph-build helpers, merge, and the L6 sweep."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock

from cortex_viz.core.workflow_graph_schema import NodeIdFactory
from cortex_viz.infrastructure import ap_bridge, ap_graph_root, snapshot_pg_store
from cortex_viz.server import graph_build_helpers as helpers
from cortex_viz.server import graph_build_l6 as l6
from cortex_viz.server import graph_build_merge as merge_mod
from cortex_viz.server import graph_cache_state as state


def test_absolutize_rewrites_only_nonempty_values(monkeypatch):
    monkeypatch.setattr(
        ap_graph_root, "absolutize", lambda root, value: f"{root}/{value}"
    )
    rows = [{"file_path": "pkg/a.py"}, {"file_path": ""}, {}]
    l6._absolutize_in_place(rows, "/repo", ("file_path",))
    assert rows == [{"file_path": "/repo/pkg/a.py"}, {"file_path": ""}, {}]


def test_roster_fingerprint_skips_missing_graphs(monkeypatch):
    monkeypatch.setattr(
        ap_bridge, "resolve_graph_paths", lambda: ["/good/graph", "/gone/graph"]
    )

    def fake_stat(path):
        if path.startswith("/gone"):
            raise OSError("gone")
        return SimpleNamespace(st_mtime=10.9, st_size=42)

    monkeypatch.setattr(helpers.os, "stat", fake_stat)
    assert helpers._roster_fingerprint() == (("/good/graph", 10, 42),)


def test_progress_phase_registration_and_readiness(monkeypatch):
    forwarded = []
    monkeypatch.setattr(state, "_forward", forwarded.append)
    monkeypatch.setattr(state, "PHASES", {"base": {"deps": [], "ready": True}})
    monkeypatch.setattr(state, "_phase_payloads", {})
    monkeypatch.setattr(
        state,
        "_build_progress",
        {"phase": "old", "phase_seq": 2, "phases": {}},
    )

    helpers._set_progress(phase="new", pct=0.5)
    assert state._build_progress["phase"] == "new"
    assert state._build_progress["indeterminate"] is False
    assert forwarded[-1][0] == "progress"

    helpers._register_phase("child", ["base"], "Child")
    assert state._phase_payloads["child"] == {"nodes": [], "edges": []}
    assert helpers._phase_deps_satisfied("unknown")
    assert helpers._phase_deps_satisfied("child")
    state.PHASES["base"]["ready"] = False
    assert not helpers._phase_deps_satisfied("child")

    helpers._mark_phase_ready("unknown")
    helpers._mark_phase_ready("child")
    assert state.PHASES["child"]["ready"]
    assert state._build_progress["phase_seq"] == 3
    assert forwarded[-1] == ("phase_ready", "child", 3)


def test_persist_snapshot_handles_empty_success_and_failure(monkeypatch, capsys):
    monkeypatch.setattr(state, "_graph_cache", {"data": {"nodes": [], "edges": []}})
    write = MagicMock()
    monkeypatch.setattr(snapshot_pg_store, "write_snapshot", write)
    helpers._persist_snapshot(object(), "fingerprint")
    write.assert_not_called()

    monkeypatch.setattr(
        state,
        "_graph_cache",
        {"data": {"nodes": [{"id": "n"}], "edges": [{"source": "n"}]}},
    )
    write.return_value = {"node_count": 1, "edge_count": 1, "bytes": 10}
    helpers._persist_snapshot("store", None)
    assert write.call_args.kwargs["fingerprint"] == "unknown"
    assert "snapshot persisted" in capsys.readouterr().err

    write.side_effect = RuntimeError("db unavailable")
    helpers._persist_snapshot("store", "fp")
    assert "snapshot persist skipped" in capsys.readouterr().err


def test_persist_full_layout_records_snapshot_and_degrades(monkeypatch, capsys):
    from cortex_viz.handlers import recompute_layout

    result = {
        "status": "ok",
        "node_count": 3,
        "elapsed_ms": 5,
        "cached": False,
        "topology_fingerprint": "fp",
    }
    monkeypatch.setattr(recompute_layout, "run_recompute", lambda _store: result)
    persist = MagicMock()
    monkeypatch.setattr(helpers, "_persist_snapshot", persist)
    assert helpers._persist_full_layout("store") is result
    persist.assert_called_once_with("store", "fp")
    assert "full layout persisted" in capsys.readouterr().err

    monkeypatch.setattr(
        recompute_layout,
        "run_recompute",
        lambda _store: (_ for _ in ()).throw(RuntimeError("layout failed")),
    )
    degraded = helpers._persist_full_layout("store")
    assert degraded["status"] == "error"
    assert "layout persist skipped" in capsys.readouterr().err


def _reset_merge_state(monkeypatch, *, sink=None):
    monkeypatch.setattr(state, "_graph_cache", None)
    monkeypatch.setattr(state, "_node_index", {})
    monkeypatch.setattr(state, "_adjacency", {})
    monkeypatch.setattr(state, "_cached_domain_hub_ids", {})
    monkeypatch.setattr(state, "_source_totals", {"memories": 2})
    monkeypatch.setattr(state, "_phase_payloads", {"L1": {"nodes": [], "edges": []}})
    monkeypatch.setattr(state, "_SINK_Q", sink)


def test_merge_deduplicates_indexes_buffers_and_emits(monkeypatch):
    _reset_merge_state(monkeypatch)
    progress = MagicMock()
    monkeypatch.setattr(merge_mod, "_set_progress", progress)
    events = SimpleNamespace(emit=MagicMock())
    merge = merge_mod.make_merge("domain", events)
    nodes = [
        {"id": "d", "kind": "domain", "label": "alpha"},
        {"id": "m", "kind": "memory"},
        {"id": "f", "kind": "file"},
        {"id": "discussion", "kind": "discussion"},
        {"id": "", "kind": "ignored"},
    ]
    edges = [
        {"source": "f", "target": "d", "kind": "in_domain"},
        {"source": "f", "target": "d", "kind": "in_domain"},
    ]
    merge(nodes, edges, "L1", 0.2, "loaded", phase_key="L1", indeterminate=True)
    merge(nodes, edges, "L1", 0.3, "again", phase_key="missing")

    data = state._graph_cache["data"]
    assert [node["id"] for node in data["nodes"]] == ["d", "m", "f", "discussion"]
    assert len(data["edges"]) == 1
    assert data["links"] is data["edges"]
    assert data["meta"]["memory_count"] == 2
    assert data["meta"]["discussion_count"] == 1
    assert data["meta"]["entity_count"] == 2
    assert state._cached_domain_hub_ids == {"alpha": "d"}
    assert state._adjacency["f"] == [("d", "in_domain", "out")]
    assert len(state._phase_payloads["L1"]["nodes"]) == 4
    events.emit.assert_called_once()
    assert progress.call_count == 2


def test_merge_forwards_child_deltas_but_not_baseline(monkeypatch):
    sink = object()
    _reset_merge_state(monkeypatch, sink=sink)
    forwarded = []
    monkeypatch.setattr(state, "_forward", forwarded.append)
    monkeypatch.setattr(merge_mod, "_set_progress", MagicMock())
    events = SimpleNamespace(emit=MagicMock())
    merge = merge_mod.make_merge(None, events)
    merge([{"id": "base", "kind": "file"}], [], "baseline", 0.1, "base")
    merge([{"id": "next", "kind": "file"}], [], "L6", 0.2, "next", phase_key="L1")
    assert len(forwarded) == 1
    assert forwarded[0][:3] == ("delta", "L1", "L6")
    events.emit.assert_not_called()


class FakeLoopOwner:
    def run(self, coro):
        return asyncio.run(coro)


class FakeASTSource:
    datasets: ClassVar[dict[str, tuple[list[dict], list[dict]]]] = {}
    load_calls: ClassVar[list[str]] = []

    def __init__(self):
        self._loop_owner = FakeLoopOwner()

    async def _load_symbols_async(self, graph_path, _paths):
        self.load_calls.append(f"symbols:{graph_path}")
        return [dict(row) for row in self.datasets[graph_path][0]]

    async def _load_edges_async(self, graph_path, _paths):
        self.load_calls.append(f"edges:{graph_path}")
        return [dict(row) for row in self.datasets[graph_path][1]]


def _install_l6_fakes(monkeypatch, graph_paths, tmp_path):
    from cortex_viz.infrastructure import workflow_graph_source_ast

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ap_bridge, "resolve_graph_paths", lambda: graph_paths)
    monkeypatch.setattr(
        ap_graph_root,
        "graph_source_root",
        lambda _path, project: f"/repo/{project}",
    )
    monkeypatch.setattr(
        ap_graph_root,
        "absolutize",
        lambda root, value: value if value.startswith("/") else f"{root}/{value}",
    )
    monkeypatch.setattr(
        workflow_graph_source_ast, "WorkflowGraphASTSource", FakeASTSource
    )


def test_run_l6_builds_projects_routes_edges_and_uses_cache(tmp_path, monkeypatch):
    project_a = tmp_path / "graphs" / "alpha" / "graph"
    project_b = tmp_path / "graphs" / "beta" / "graph"
    project_a.parent.mkdir(parents=True)
    project_b.parent.mkdir(parents=True)
    project_a.write_text("a")
    project_a.with_name("graph.wal").write_text("wal")
    project_b.mkdir()
    (project_b / "part").write_text("b")
    paths = [str(project_a), str(project_b)]
    _install_l6_fakes(monkeypatch, paths, tmp_path)

    FakeASTSource.load_calls = []
    FakeASTSource.datasets = {
        str(project_a): (
            [
                {
                    "file_path": "pkg/a.py",
                    "qualified_name": "pkg/a.py::A",
                    "symbol_type": "function",
                },
                {
                    "file_path": "pkg/a.py",
                    "qualified_name": "pkg/a.py::B",
                    "symbol_type": "custom",
                },
                {"file_path": "", "qualified_name": "", "symbol_type": "function"},
            ],
            [
                {
                    "src_file": "pkg/a.py",
                    "src_name": "pkg/a.py::A",
                    "dst_file": "pkg/a.py",
                    "dst_name": "pkg/a.py::B",
                    "kind": "calls",
                    "confidence": 0.8,
                    "reason": "exact",
                },
                {
                    "src_file": "pkg/a.py",
                    "src_name": "pkg/a.py::A",
                    "dst_file": "pkg/b.py",
                    "dst_name": "pkg/b.py::C",
                    "kind": "calls",
                },
                {
                    "src_file": "pkg/a.py",
                    "src_name": "",
                    "dst_file": "pkg/a.py",
                    "dst_name": "pkg/a.py::B",
                    "kind": "imports",
                },
                {
                    "src_file": "",
                    "src_name": "",
                    "dst_file": "",
                    "dst_name": "",
                    "kind": "calls",
                },
            ],
        ),
        str(project_b): (
            [
                {
                    "file_path": "pkg/b.py",
                    "qualified_name": "pkg/b.py::C",
                    "symbol_type": "method",
                }
            ],
            [
                {
                    "src_file": "pkg/b.py",
                    "src_name": "pkg/b.py::C",
                    "dst_file": "pkg/a.py",
                    "dst_name": "pkg/a.py::A",
                    "kind": "calls",
                }
            ],
        ),
    }

    beta_file = "/repo/beta/pkg/b.py"
    beta_file_id = NodeIdFactory.file_id(beta_file)
    monkeypatch.setattr(
        state,
        "_node_index",
        {
            "domain:beta": {"id": "domain:beta", "x": 0.1, "y": 0.2},
            beta_file_id: {"id": beta_file_id, "x": 0.2, "y": 0.3},
        },
    )

    merges = []
    phases = {}
    ready = set()

    def merge(nodes, edges, stage, pct, message, phase_key=None, **flags):
        merges.append((nodes, edges, stage, phase_key))

    def register(key, deps, label):
        phases[key] = {"deps": deps, "label": label}

    def mark(key):
        ready.add(key)

    def deps_satisfied(key):
        return all(
            dep == "L3" or dep in ready for dep in phases.get(key, {}).get("deps", [])
        )

    kwargs = {
        "merge": merge,
        "set_progress": MagicMock(),
        "register_phase": register,
        "mark_phase_ready": mark,
        "phase_deps_satisfied": deps_satisfied,
        "persist_full_layout": MagicMock(),
    }
    file_ids = {beta_file: beta_file_id}
    assert l6.run_l6("store", {"nodes": []}, file_ids, **kwargs)
    assert ready == {"L6:alpha", "L6:beta", "L6_CROSS"}
    assert len(FakeASTSource.load_calls) == 4
    all_nodes = [node for nodes, _, _, _ in merges for node in nodes]
    all_edges = [edge for _, edges, _, _ in merges for edge in edges]
    assert any(
        node["kind"] == "domain" and node["id"] == "domain:alpha" for node in all_nodes
    )
    assert any(
        node["kind"] == "symbol" and node.get("x") is not None for node in all_nodes
    )
    assert any(edge["kind"] == "defined_in" for edge in all_edges)
    assert any(phase == "L6_CROSS" for _, _, _, phase in merges)

    FakeASTSource.load_calls = []
    merges.clear()
    ready.clear()
    assert l6.run_l6("store", {"nodes": []}, file_ids, **kwargs)
    assert FakeASTSource.load_calls == []
    assert any("L6" in stage for _, _, stage, _ in merges)


def test_run_l6_marks_failed_project_and_honors_unsatisfied_cross_deps(
    tmp_path, monkeypatch, capsys
):
    graph = tmp_path / "graphs" / "broken" / "graph"
    graph.parent.mkdir(parents=True)
    graph.write_text("x")
    _install_l6_fakes(monkeypatch, [str(graph)], tmp_path)
    FakeASTSource.datasets = {}
    marked = []
    registered = {}

    result = l6.run_l6(
        "store",
        {},
        {},
        merge=MagicMock(),
        set_progress=MagicMock(),
        register_phase=lambda key, deps, label: registered.setdefault(key, deps),
        mark_phase_ready=marked.append,
        phase_deps_satisfied=lambda key: key != "L6_CROSS",
        persist_full_layout=MagicMock(),
    )
    assert result is False
    assert "L6:broken" in marked
    assert "project broken skipped" in capsys.readouterr().err


def test_run_l6_skips_project_whose_phase_dependencies_are_unready(
    tmp_path, monkeypatch
):
    graph = tmp_path / "graphs" / "waiting" / "graph"
    graph.parent.mkdir(parents=True)
    graph.write_text("x")
    _install_l6_fakes(monkeypatch, [str(graph)], tmp_path)
    FakeASTSource.datasets = {str(graph): ([], [])}
    marked = []
    result = l6.run_l6(
        "store",
        {},
        {},
        merge=MagicMock(),
        set_progress=MagicMock(),
        register_phase=MagicMock(),
        mark_phase_ready=marked.append,
        phase_deps_satisfied=lambda _key: False,
        persist_full_layout=MagicMock(),
    )
    assert result is False
    assert marked == []
