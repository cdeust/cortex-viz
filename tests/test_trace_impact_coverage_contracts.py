"""Behavioral contracts for AP-backed trace impact and direction shaping."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from cortex_viz.infrastructure import ap_bridge, workflow_graph_source_ast
from cortex_viz.server import trace_impact as impact
from cortex_viz.server import trace_impact_directions as directions


def test_direction_scalar_helpers():
    assert directions._basename(r"C:\repo\file.py/") == "file.py"
    assert directions._file_of("pkg/file.py::run") == "pkg/file.py"
    assert directions._short_name("pkg/file.py::run") == "run"
    assert directions._conf({"conf": "0.75"}) == 0.75
    assert directions._conf({"conf": "bad"}) is None
    assert directions._conf({}) is None
    assert directions._rel_item({}, "calls") is None
    assert directions._rel_item({"name": "pkg/a.py::run"}, "calls") == {
        "file": "pkg/a.py",
        "name": "pkg/a.py::run",
        "label": "run",
        "kind": "calls",
        "confidence": None,
    }


class ContextBridge:
    async def get_context(self, _graph, qn):
        if qn == "bad":
            raise RuntimeError("unavailable")
        if qn == "none":
            return None
        return {
            "relationships": {
                "calls": [
                    {"qualified_name": "dep.py::call"},
                    {"qualified_name": "dep.py::call"},
                    {},
                ],
                "imports": [{"name": "import.py::symbol"}],
                "uses": [],
                "called_by": [{"qualified_name": "caller.py::entry"}],
                "imported_by": [],
                "used_by": [{"qualified_name": "user.py::use"}],
                "implements": [{"qualified_name": "iface.py::Interface"}],
                "implemented_by": [{"qualified_name": "iface.py::Interface"}],
            },
            "community": {"id": "community"},
        }

    async def get_impact(self, _graph, qn):
        if qn == "bad":
            raise RuntimeError("unavailable")
        if qn == "none":
            return None
        return {"communities_affected": 2, "processes_affected": 3}


def test_typed_directions_dedupe_and_degrade():
    downstream, upstream, implements, community = asyncio.run(
        directions._typed_directions(
            ContextBridge(), "/graph", ["one", "bad", "none", "two"]
        )
    )
    assert [item["name"] for item in downstream] == [
        "dep.py::call",
        "import.py::symbol",
    ]
    assert {item["name"] for item in upstream} == {
        "caller.py::entry",
        "user.py::use",
    }
    assert [item["name"] for item in implements] == ["iface.py::Interface"]
    assert community == {"id": "community"}


def test_fallback_directions_and_confidence():
    async def exercise():
        async def query(cypher):
            if "Imports_File_Function" in cypher:
                return [{"name": "import.py::symbol", "conf": "bad"}]
            if "WHERE d.qualified_name" in cypher:
                return [{"name": "caller.py::entry", "conf": None}, {}]
            return [{"name": "dep.py::call", "conf": "0.9"}]

        return await directions._fallback_directions(query, "pkg/file.py")

    downstream, upstream = asyncio.run(exercise())
    assert downstream[0]["confidence"] == 0.9
    assert downstream[1]["confidence"] is None
    assert upstream == [
        {
            "file": "caller.py",
            "name": "caller.py::entry",
            "label": "entry",
            "kind": "calls",
            "confidence": None,
        }
    ]


def test_blast_radius_process_rollup_and_file_edges():
    assert asyncio.run(directions._blast_radius(ContextBridge(), "/g", [])) == (
        None,
        None,
    )
    assert asyncio.run(directions._blast_radius(ContextBridge(), "/g", ["good"])) == (
        2,
        3,
    )
    assert asyncio.run(directions._blast_radius(ContextBridge(), "/g", ["bad"])) == (
        None,
        None,
    )
    assert asyncio.run(directions._blast_radius(ContextBridge(), "/g", ["none"])) == (
        None,
        None,
    )

    assert directions._processes_from(
        [{"entry": "a.py::main", "kind": "main", "depth": 2, "n": 4}, {}]
    ) == [
        {
            "entry": "a.py::main",
            "label": "main",
            "kind": "main",
            "depth": 2,
            "symbol_count": 4,
        }
    ]
    items = [
        {"file": "dep.py", "kind": "calls"},
        {"file": "dep.py", "kind": "imports"},
        {"file": "other.py", "kind": "calls"},
        {"file": "self.py", "kind": "calls"},
        {"file": ""},
    ]
    rolled = directions._rollup(items, "self.py")
    assert rolled[0] == {
        "file": "dep.py",
        "label": "dep.py",
        "edges": 2,
        "kinds": ["calls", "imports"],
    }
    assert directions._file_edges(
        [{"name": "dep.py", "conf": "0.5"}, {"name": "self.py"}, {}],
        "references",
        "self.py",
    ) == [
        {
            "file": "dep.py",
            "label": "dep.py",
            "kind": "references",
            "confidence": 0.5,
        }
    ]


class LoopOwner:
    def run(self, coro):
        return asyncio.run(coro)


class ImpactBridge(ContextBridge):
    def __init__(self, member_count=2, *, present=True):
        self.member_count = member_count
        self.present = present

    async def query_graph(self, _graph, cypher):
        if "RETURN f.id AS id" in cypher:
            return [{"id": "pkg/file.py"}] if self.present else []
        if "MATCH (s:Function) WHERE s.qualified_name" in cypher:
            return [
                {"name": f"pkg/file.py::symbol_{i}"} for i in range(self.member_count)
            ]
        if "Imports_File_File" in cypher and "MATCH (f:File)" in cypher:
            return [{"name": "dep.py", "conf": 0.8}]
        if "Imports_File_File" in cypher:
            return [{"name": "caller.py", "conf": 0.7}]
        if "References_File_File" in cypher and "MATCH (f:File)" in cypher:
            return [{"name": "doc.md", "conf": 1}]
        if "References_File_File" in cypher:
            return [{"name": "readme.md", "conf": 1}]
        if "MATCH (p:Process)" in cypher:
            return [{"entry": "pkg/file.py::main", "kind": "main", "depth": 2, "n": 4}]
        if "Imports_File_Function" in cypher:
            return [{"name": "fallback-import.py::symbol", "conf": 0.6}]
        if "WHERE d.qualified_name" in cypher:
            return [{"name": "fallback-caller.py::symbol", "conf": 0.5}]
        if "Calls_Function_Function" in cypher:
            return [{"name": "fallback-dep.py::symbol", "conf": 0.9}]
        return []


class WarmSource:
    def __init__(self, bridge, symbols=None):
        self._bridge = bridge
        self._loop_owner = LoopOwner()
        self.symbols = symbols or []

    def load_symbols(self, _paths):
        return [dict(symbol) for symbol in self.symbols]


def test_get_ast_source_is_singleton(monkeypatch):
    created = []

    class Source:
        def __init__(self):
            created.append(self)

    monkeypatch.setattr(workflow_graph_source_ast, "WorkflowGraphASTSource", Source)
    impact._AST_SOURCE = None
    impact._AST_SOURCE_LOCK = None
    assert impact._get_ast_source() is impact._get_ast_source()
    assert len(created) == 1


def test_ast_and_impact_degradation_and_enrichment(monkeypatch):
    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: False)
    assert impact._ast_and_impact("file.py") == {
        "available": False,
        "reason": "ap_disabled",
    }

    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: True)
    monkeypatch.setattr(ap_bridge, "resolve_graph_paths", lambda: ["/graph"])
    source = WarmSource(
        ContextBridge(),
        [
            {"qualified_name": "pkg/file.py::run"},
            {"qualified_name": "bad"},
            {"qualified_name": ""},
        ],
    )
    monkeypatch.setattr(impact, "_get_ast_source", lambda: source)
    result = impact._ast_and_impact("file.py")
    assert result["available"]
    assert result["symbols"][0]["context"]["community"] == {"id": "community"}
    assert result["impact"]["communities_affected"] == 2

    source.symbols = []
    assert impact._ast_and_impact("file.py") == {
        "available": True,
        "symbols": [],
        "impact": [],
    }
    source.symbols = [{"qualified_name": "pkg/file.py::run"}]
    monkeypatch.setattr(ap_bridge, "resolve_graph_paths", lambda: [])
    assert impact._ast_and_impact("file.py")["impact"] == []


def test_attach_and_first_impact_failure_shapes():
    symbols = [{"qualified_name": ""}]
    impact._attach_symbol_context(
        lambda coro: asyncio.run(coro), ContextBridge(), "/g", symbols
    )
    assert "context" not in symbols[0]
    assert impact._first_symbol_impact(None, None, "/g", symbols) == {}
    assert (
        impact._first_symbol_impact(
            lambda coro: asyncio.run(coro),
            ContextBridge(),
            "/g",
            [{"qualified_name": "bad"}],
        )
        == {}
    )

    def non_dict(coro):
        coro.close()
        return "not-a-dict"

    assert (
        impact._first_symbol_impact(
            non_dict,
            ContextBridge(),
            "/g",
            [{"qualified_name": "good"}],
        )
        == {}
    )


def test_impact_for_graph_typed_and_fallback_paths(monkeypatch):
    typed = WarmSource(ImpactBridge(member_count=2))
    monkeypatch.setattr(impact, "_get_ast_source", lambda: typed)
    result = impact._impact_for_graph("/graph", "pkg/file.py")
    assert result["members"][0]["label"] == "symbol_0"
    assert result["community"] == {"id": "community"}
    assert result["communities_affected"] == 2
    assert result["processes"][0]["entry"] == "pkg/file.py::main"
    assert result["references"][0]["file"] == "doc.md"
    assert result["depends_on"]

    fallback = WarmSource(ImpactBridge(member_count=impact._AST_CONTEXT_CAP + 1))
    monkeypatch.setattr(impact, "_get_ast_source", lambda: fallback)
    result = impact._impact_for_graph("/graph", "pkg/file.py")
    assert any(item["file"] == "fallback-dep.py" for item in result["downstream"])
    assert any(item["file"] == "fallback-caller.py" for item in result["upstream"])

    missing = WarmSource(ImpactBridge(present=False))
    monkeypatch.setattr(impact, "_get_ast_source", lambda: missing)
    assert impact._impact_for_graph("/graph", "pkg/file.py") is None


def test_path_normalization_containment_and_git_root(tmp_path, monkeypatch):
    assert impact._to_repo_relative("./pkg/file.py") == "pkg/file.py"
    assert impact._to_repo_relative("../secret") == ""

    original_realpath = os.path.realpath
    original_is_contained = impact._is_contained
    original_relative_to_git_root = impact._relative_to_git_root
    monkeypatch.setattr(os.path, "realpath", lambda _path: "/allowed/repo/file.py")
    monkeypatch.setattr(impact, "_is_contained", lambda _path: False)
    assert impact._to_repo_relative("/outside/file.py") == "outside/file.py"
    monkeypatch.setattr(impact, "_is_contained", lambda _path: True)
    monkeypatch.setattr(impact, "_relative_to_git_root", lambda _path: "pkg/file.py")
    assert impact._to_repo_relative("/allowed/repo/file.py") == "pkg/file.py"
    monkeypatch.setattr(impact, "_relative_to_git_root", lambda _path: "")
    assert impact._to_repo_relative("/allowed/repo/file.py") == "allowed/repo/file.py"

    monkeypatch.setattr(os.path, "realpath", original_realpath)
    monkeypatch.setattr(impact, "_is_contained", original_is_contained)
    monkeypatch.setattr(impact, "_relative_to_git_root", original_relative_to_git_root)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path / "cwd")
    assert impact._is_contained(str(tmp_path / "project"))
    assert not impact._is_contained("/definitely-outside")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=str(tmp_path)),
    )
    assert (
        impact._relative_to_git_root(str(tmp_path / "pkg" / "file.py")) == "pkg/file.py"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert impact._relative_to_git_root(str(tmp_path / "file.py")) == ""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("git")),
    )
    assert impact._relative_to_git_root(str(tmp_path / "file.py")) == ""


def test_impact_for_path_selects_richest_graph_and_degrades(monkeypatch):
    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: False)
    assert impact.impact_for_path("file.py") is None
    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: True)
    monkeypatch.setattr(impact, "_to_repo_relative", lambda _path: "")
    assert impact.impact_for_path("file.py") is None

    monkeypatch.setattr(impact, "_to_repo_relative", lambda _path: "pkg/file.py")
    monkeypatch.setattr(
        ap_bridge, "resolve_graph_paths", lambda: ["bad", "small", "large"]
    )

    def graph_result(graph, _path):
        if graph == "bad":
            raise RuntimeError("bad graph")
        if graph == "small":
            return {"downstream": [{"x": 1}]}
        return {"downstream": [{"x": 1}, {"x": 2}], "upstream": [{"x": 3}]}

    monkeypatch.setattr(impact, "_impact_for_graph", graph_result)
    assert len(impact.impact_for_path("file.py")["downstream"]) == 2
