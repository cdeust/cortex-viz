"""run_build end-to-end with the DB/AP/layout boundaries faked."""

from __future__ import annotations

import copy

import pytest

from cortex_viz.core import layout_engine
from cortex_viz.handlers import workflow_graph
from cortex_viz.infrastructure import ap_bridge
from cortex_viz.server import graph_build_run as gbr
from cortex_viz.server import graph_build_wiki_source
from cortex_viz.server import graph_cache_state as state


@pytest.fixture(autouse=True)
def _isolated_state():
    saved = {
        "cache": state._graph_cache,
        "phases": copy.deepcopy(state.PHASES),
        "payloads": copy.deepcopy(state._phase_payloads),
        "progress": copy.deepcopy(state._build_progress),
        "fp": state._graph_roster_fingerprint,
        "totals": dict(state._source_totals),
        "hubs": dict(state._cached_domain_hub_ids),
    }
    yield
    state._graph_cache = saved["cache"]
    state.PHASES.clear()
    state.PHASES.update(saved["phases"])
    state._phase_payloads.clear()
    state._phase_payloads.update(saved["payloads"])
    state._build_progress.clear()
    state._build_progress.update(saved["progress"])
    state._graph_roster_fingerprint = saved["fp"]
    state._source_totals.clear()
    state._source_totals.update(saved["totals"])
    state._cached_domain_hub_ids.clear()
    state._cached_domain_hub_ids.update(saved["hubs"])
    state._node_index.clear()
    state._adjacency.clear()
    if state._graph_build_lock.locked():
        state._graph_build_lock.release()


class HDict(dict):
    """Hashable endpoint dict (the merge keys edges on source/target)."""

    __hash__ = object.__hash__


class Obj:
    def __init__(self, **d):
        self.d = d

    def model_dump(self, exclude_none=True):
        return dict(self.d)


def _dn(i, kind, **kw):
    return {"id": i, "kind": kind, "type": kind, **kw}


def _install(monkeypatch, *, ap, l6_result=True, fail_skeleton=False, layout=None):
    calls = {"build": [], "persist": 0, "l6": 0, "wiki": 0}

    def fake_build(store, **kw):
        calls["build"].append(kw)
        if kw["stage"] == "skeleton":
            if fail_skeleton:
                raise RuntimeError("skel boom")
            return {"nodes": [_dn("domain:x", "domain", label="x")], "edges": []}
        if kw.get("on_source_loaded"):
            kw["on_source_loaded"]("memories", 3)
        if kw.get("on_batch"):
            kw["on_batch"]("x", [], [])
            kw["on_batch"](
                "memories", [Obj(id="m1", kind="memory"), Obj(kind="nid")], []
            )
            kw["on_batch"]("memories", [Obj(id="m1", kind="memory")], [])
        return {
            "nodes": [
                _dn("domain:x", "domain", label="x"),
                _dn("f1", "file", path="a/b/c.py"),
                _dn("f2", "file", path=""),
                _dn("lonely", "tool_hub"),
                {"kind": "noid"},
            ],
            "edges": [
                {"source": "f1", "target": "domain:x", "kind": "in_domain"},
                {"source": HDict(id="f2"), "target": HDict(id="f1"), "kind": "k"},
                {"source": "f1", "target": "f1", "kind": "self"},
                {"source": "f1", "target": "ghost", "kind": "dangling"},
            ],
        }

    monkeypatch.setattr(workflow_graph, "build_workflow_graph", fake_build)
    monkeypatch.setattr(gbr, "_roster_fingerprint", lambda: (("g", 1, 1),))
    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: ap)

    def fake_persist(store):
        calls["persist"] += 1
        return {}

    monkeypatch.setattr(gbr, "_persist_full_layout", fake_persist)

    def fake_l6(store, baseline, fmap, **kw):
        calls["l6"] += 1
        calls["fmap"] = fmap
        kw["merge"](
            [_dn("s1", "symbol"), _dn("e1", "entity")],
            [
                {"source": "s1", "target": "f1", "kind": "defined_in"},
                {"source": "s1", "target": "e1", "kind": "about_entity"},
            ],
            stage="l6",
            pct=0.5,
            message="m",
        )
        return l6_result

    monkeypatch.setattr(gbr, "run_l6", fake_l6)

    def fake_wiki(store, *, merge):
        calls["wiki"] += 1
        return 4

    monkeypatch.setattr(
        graph_build_wiki_source, "resolve_wiki_source_over_cache", fake_wiki
    )
    if layout is None:

        def layout(ids, edges):
            calls["layout"] = (list(ids), list(edges))
            # place everything except 'lonely'
            return [(i, 1.0, 2.0) for i in ids if i != "lonely"]

    monkeypatch.setattr(layout_engine, "layout", layout)
    return calls


def _run():
    assert state._graph_build_lock.acquire(blocking=False)
    gbr.run_build(store=object(), domain_filter="x")
    assert not state._graph_build_lock.locked()


def test_ap_disabled_build_reaches_full_ready(monkeypatch, capsys):
    calls = _install(monkeypatch, ap=False)
    _run()
    prog = state._build_progress
    assert prog["phase"] == "full_ready" and prog["full_ready"] is True
    assert "AP disabled" in prog["message"]
    assert calls["persist"] == 1 and calls["wiki"] == 1 and calls["l6"] == 0
    # layout fed only valid, distinct, known endpoints
    ids, edges = calls["layout"]
    assert set(ids) == {"domain:x", "f1", "f2", "lonely"}
    assert set(edges) == {("f1", "domain:x"), ("f2", "f1")}
    nodes = {n.get("id"): n for n in state._graph_cache["data"]["nodes"]}
    assert (nodes["f1"]["x"], nodes["f1"]["y"]) == (1.0, 2.0)
    assert "x" in nodes["lonely"] and "y" in nodes["lonely"]  # ray fallback
    assert state._graph_cache["domain_filter"] == "x"
    assert state._source_totals["memories"] == 3
    assert state.PHASES["L5"]["ready"] is True
    err = capsys.readouterr().err
    assert "2 ray fallbacks" in err
    assert "wiki_source edges resolved: 4" in err
    # build asked for skeleton then full stage
    assert [c["stage"] for c in calls["build"]] == ["skeleton", "full"]
    assert calls["build"][1]["memory_limit"] == 0


def test_ap_enabled_build_runs_l6_and_counts(monkeypatch):
    calls = _install(monkeypatch, ap=True)
    monkeypatch.setenv("CORTEX_VIZ_MEMORY_LIMIT", "25")
    _run()
    assert calls["l6"] == 1 and calls["persist"] == 1 and calls["wiki"] == 1
    assert calls["build"][1]["memory_limit"] == 25
    # file id map: full path plus every suffix, empty paths skipped
    assert calls["fmap"] == {"a/b/c.py": "f1", "b/c.py": "f1", "c.py": "f1"}
    prog = state._build_progress
    assert prog["phase"] == "full_ready" and prog["full_ready"] is True
    counts = state._graph_cache["data"]["meta"]["counts"]
    assert counts["symbols"] == 1
    assert counts["entities"] == 1
    assert counts["ast_edges"] == 1
    assert counts["memory_entity_edges"] == 1
    assert prog["node_count"] == len(state._graph_cache["data"]["nodes"])
    assert prog["edge_count"] == len(state._graph_cache["data"]["edges"])


def test_l6_incomplete_returns_early(monkeypatch):
    calls = _install(monkeypatch, ap=True, l6_result=False)
    _run()
    assert calls["l6"] == 1
    assert calls["persist"] == 0 and calls["wiki"] == 0
    assert state._build_progress["full_ready"] is False
    assert state._build_progress["phase"] == "l6"


def test_bad_memory_limit_and_failures_are_tolerated(monkeypatch, capsys):
    def bad_layout(ids, edges):
        raise ImportError("no igraph")

    calls = _install(monkeypatch, ap=False, fail_skeleton=True, layout=bad_layout)
    monkeypatch.setenv("CORTEX_VIZ_MEMORY_LIMIT", "lots")

    def wiki_boom(store, *, merge):
        raise RuntimeError("wiki down")

    monkeypatch.setattr(
        graph_build_wiki_source, "resolve_wiki_source_over_cache", wiki_boom
    )
    _run()
    err = capsys.readouterr().err
    assert "skeleton build failed: skel boom" in err
    assert "baseline layout bake skipped: no igraph" in err
    assert "wiki_source resolution skipped: wiki down" in err
    assert calls["build"][1]["memory_limit"] == 0
    assert state._build_progress["full_ready"] is True


def test_fatal_error_sets_error_phase(monkeypatch, capsys):
    _install(monkeypatch, ap=False)

    def boom(*a, **k):
        raise RuntimeError("no workflow")

    def fatal(store, **kw):
        if kw["stage"] == "full":
            raise RuntimeError("db gone")
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(workflow_graph, "build_workflow_graph", fatal)
    _run()
    assert state._build_progress["phase"] == "error"
    assert "RuntimeError: db gone" in state._build_progress["message"]
    assert "background build error" in capsys.readouterr().err


def test_rebuild_drops_previous_dynamic_phases(monkeypatch):
    _install(monkeypatch, ap=False)
    state.PHASES["L6:old"] = {"deps": ["L3"], "ready": True, "label": "old"}
    state._phase_payloads["L6:old"] = {"nodes": [1], "edges": []}
    state.PHASES["L6_CROSS"] = {"deps": [], "ready": True, "label": "x"}
    state._phase_payloads["L3"] = {"nodes": [{"id": "stale"}], "edges": [1]}
    _run()
    assert "L6:old" not in state.PHASES and "L6_CROSS" not in state.PHASES
    assert "L6:old" not in state._phase_payloads
    assert "stale" not in [n.get("id") for n in state._phase_payloads["L3"]["nodes"]]
