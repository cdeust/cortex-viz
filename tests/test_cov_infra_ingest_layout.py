"""Tests for core.workflow_graph_builder_ingest and core.layout_engine."""

from __future__ import annotations

import pytest

from cortex_viz.core import layout_engine
from cortex_viz.core import workflow_graph_builder_ingest as ing
from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder
from cortex_viz.core.workflow_graph_schema import (
    EdgeKind,
    NodeIdFactory,
    NodeKind,
    ToolKind,
)


def kinds(b, kind):
    return [n for n in b._nodes.values() if n.kind == kind]


def edge_kinds(b):
    return [e.kind for e in b._edges]


def test_require_and_as_tool():
    assert ing._require({"a": 1}, "a", "ctx") == 1
    with pytest.raises(ValueError, match="missing key"):
        ing._require({"a": None}, "a", "ctx")
    with pytest.raises(ValueError):
        ing._require({}, "a", "ctx")
    assert ing._as_tool("Edit") is ToolKind.EDIT
    assert ing._as_tool("bash") is ToolKind.BASH
    with pytest.raises(ValueError, match="unknown ToolKind"):
        ing._as_tool("Telepathy")


def test_tool_event_without_file_adds_only_hub():
    b = WorkflowGraphBuilder()
    b._ingest_tool_event({"tool": "Bash", "domain": "alpha"})
    assert b._file_tool_counts == {}
    assert not any(e.kind == EdgeKind.TOOL_USED_FILE for e in b._edges)


def test_tool_event_with_file_tracks_counts_and_timestamps():
    b = WorkflowGraphBuilder()
    b._ingest_tool_event(
        {
            "tool": "Edit",
            "domain": "alpha",
            "file_path": "/r/a.py",
            "count": 3,
            "first_ts": "2026-01-05",
            "last_ts": "2026-01-06",
        }
    )
    b._ingest_tool_event(
        {
            "tool": "Read",
            "domain": "beta",
            "file_path": "/r/a.py",
            "first_ts": "2026-01-01",
            "last_ts": "2026-01-09",
        }
    )
    b._ingest_tool_event(
        {
            "tool": "Write",
            "domain": "beta",
            "file_path": "/r/a.py",
            "last_ts": "2026-01-02",
        }
    )
    b._ingest_tool_event({"tool": "Write", "domain": "alpha", "file_path": "/r/a.py"})
    assert b._file_tool_counts["/r/a.py"][ToolKind.EDIT] == 3
    ts = b._file_timestamps["/r/a.py"]
    assert ts == {
        "first_seen": "2026-01-01",
        "last_accessed": "2026-01-09",
        "last_modified": "2026-01-06",
    }
    used = [e for e in b._edges if e.kind == EdgeKind.TOOL_USED_FILE]
    assert len(used) == 4 and used[0].weight == 3.0
    assert used[0].target == NodeIdFactory.file_id("/r/a.py")


def test_finalize_files_builds_nodes_with_multiple_domains():
    b = WorkflowGraphBuilder()
    b._ingest_tool_event(
        {
            "tool": "Edit",
            "domain": "alpha",
            "file_path": "/r/dir/a.py",
            "first_ts": "t1",
            "last_ts": "t2",
        }
    )
    b._ingest_tool_event({"tool": "Read", "domain": "beta", "file_path": "/r/dir/a.py"})
    b._file_domains["/r/dir/a.py"].add("domain:zeta")
    b._finalize_files()
    (node,) = kinds(b, NodeKind.FILE)
    assert node.label == "a.py" and node.path == "/r/dir/a.py"
    assert node.first_seen == "t1" and node.last_modified == "t2"
    assert len(node.extra_domain_ids) == 1
    assert sum(1 for e in b._edges if e.target.startswith("domain:")) >= 2
    assert b._node_order[-1] is node


def test_finalize_files_rejects_domainless_file():
    b = WorkflowGraphBuilder()
    b._file_tool_counts["/x"][ToolKind.EDIT] += 1
    with pytest.raises(ValueError, match="no domain membership"):
        b._finalize_files()


def test_ingest_memory_projects_scientific_fields():
    b = WorkflowGraphBuilder()
    b._ingest_memory(
        {
            "id": 7,
            "domain": "alpha",
            "stage": "labile",
            "heat_base": 0.5,
            "content": "line one\nline two",
            "tags": ["a", 2],
            "arousal": 0.3,
            "importance": None,
            "created_at": "2026-01-01",
        }
    )
    (n,) = kinds(b, NodeKind.MEMORY)
    assert n.id == NodeIdFactory.memory_id(7)
    assert n.label == "line one line two"
    assert n.heat == 0.5 and n.stage == "labile" and n.tags == ["a", "2"]
    assert n.arousal == 0.3
    assert n.body == "line one\nline two"
    with pytest.raises(ValueError):
        b._ingest_memory({"content": "no id"})


def test_ingest_memory_defaults():
    b = WorkflowGraphBuilder()
    b._ingest_memory({"id": 1, "tags": "notalist", "consolidation_stage": "weird"})
    (n,) = kinds(b, NodeKind.MEMORY)
    assert n.label == "memory 1" and n.tags == [] and n.body is None


def test_ingest_discussion():
    b = WorkflowGraphBuilder()
    b._ingest_discussion(
        {
            "session_id": "abcdef123456",
            "domain": "alpha",
            "message_count": 10,
            "started_at": "s",
            "last_activity": "l",
            "duration_ms": 5,
        }
    )
    b._ingest_discussion({"session_id": "zz", "title": "Titled"})
    labels = {n.label for n in kinds(b, NodeKind.DISCUSSION)}
    assert labels == {"abcdef12", "Titled"}
    with pytest.raises(ValueError):
        b._ingest_discussion({})


def test_ingest_skill_multi_and_global_domain():
    b = WorkflowGraphBuilder()
    b._ingest_skill(
        {"name": "s1", "path": "/s1", "domains": ["alpha", "beta"], "body": "b"}
    )
    b._ingest_skill({"name": "s2", "path": "/s2"})
    invoked = [e for e in b._edges if e.kind == EdgeKind.INVOKED_SKILL]
    assert len(invoked) == 3
    s1 = b._nodes[NodeIdFactory.skill_id("s1")]
    assert s1.body == "b" and len(s1.extra_domain_ids) == 1
    with pytest.raises(ValueError):
        b._ingest_skill({"name": "x"})


def test_ingest_hook_dedupes():
    b = WorkflowGraphBuilder()
    hk = {
        "event": "PreToolUse",
        "command": "run.sh",
        "matcher": "Bash",
        "domain": "alpha",
    }
    b._ingest_hook(hk)
    b._ingest_hook(hk)
    (n,) = kinds(b, NodeKind.HOOK)
    assert n.label == "PreToolUse:Bash"
    assert edge_kinds(b).count(EdgeKind.TRIGGERED_HOOK) == 1
    b._ingest_hook({"event": "Stop", "command": "c2"})
    assert "Stop" in {n.label for n in kinds(b, NodeKind.HOOK)}


def test_ingest_agent_and_command():
    b = WorkflowGraphBuilder()
    b._ingest_agent({"subagent_type": "Explore", "domain": "alpha", "count": 4})
    (a,) = kinds(b, NodeKind.AGENT)
    spawned = [e for e in b._edges if e.kind == EdgeKind.SPAWNED_AGENT]
    assert a.label == "Explore" and spawned[0].weight == 4.0

    cm = {
        "cmd": "git status",
        "cmd_hash": "abc",
        "domain": "alpha",
        "count": 2,
        "first_ts": "f",
        "last_ts": "l",
    }
    b._ingest_command(cm)
    b._ingest_command(cm)  # duplicate id: no second edge
    (c,) = kinds(b, NodeKind.COMMAND)
    assert c.body == "git status" and c.first_seen == "f" and c.last_accessed == "l"
    assert edge_kinds(b).count(EdgeKind.COMMAND_IN_HUB) == 1
    with pytest.raises(ValueError):
        b._ingest_command({"cmd": "x"})
    with pytest.raises(ValueError):
        b._ingest_agent({})


# ── layout_engine ────────────────────────────────────────────────────────────


def test_topology_fingerprint_is_order_independent():
    a = layout_engine.topology_fingerprint(["a", "b"], [("a", "b"), ("b", "a")])
    b = layout_engine.topology_fingerprint(["b", "a"], [("b", "a"), ("a", "b")])
    assert a == b and len(a) == 16
    assert a != layout_engine.topology_fingerprint(["a", "b", "c"], [])


def test_layout_validations():
    with pytest.raises(ValueError):
        layout_engine.layout([], [])


def test_layout_small_graph_is_normalised_and_ignores_bad_edges():
    ids = ["a", "b", "c", "d"]
    out = layout_engine.layout(
        ids,
        [("a", "b"), ("b", "c"), ("c", "d"), ("a", "a"), ("a", "missing")],
        seed=1,
    )
    assert [o[0] for o in out] == ids
    xs = [o[1] for o in out]
    ys = [o[2] for o in out]
    assert max(abs(v) for v in xs + ys) <= 1.0 + 1e-9
    assert len(set(zip(xs, ys, strict=True))) == 4


def test_layout_single_node_has_zero_span():
    assert layout_engine.layout(["only"], []) == [("only", 0.0, 0.0)]


def test_layout_drl_for_large_graph_and_algorithm_fallbacks():
    ids = [f"n{i}" for i in range(220)]
    edges = [(ids[i], ids[(i * 7 + 1) % 220]) for i in range(220)]
    out = layout_engine.layout(ids, edges)
    assert len(out) == 220
    assert max(abs(o[1]) for o in out) <= 1.0 + 1e-9
    fr = layout_engine.layout(ids[:30], edges[:10], algorithm="fr")
    assert len(fr) == 30
    unknown = layout_engine.layout(ids, edges, algorithm="mystery")
    assert len(unknown) == 220


def test_layout_without_igraph(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "igraph":
            raise ImportError("no igraph")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError, match="viz-tile"):
        layout_engine.layout(["a"], [])
