"""Behaviour tests for workflow_graph_schema and workflow_graph_builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder
from cortex_viz.core.workflow_graph_inputs import WorkflowBuildInputs
from cortex_viz.core.workflow_graph_schema import (
    GLOBAL_DOMAIN_ID,
    EdgeKind,
    GraphValidationError,
    NodeIdFactory,
    NodeKind,
    ToolKind,
    WorkflowEdge,
    WorkflowNode,
    _split_tool_hub_id,
    edge_provenance_defaults,
    validate_graph,
)

D = "domain:proj"


def _dom(i=D):
    return WorkflowNode(id=i, kind=NodeKind.DOMAIN, domain_id=i)


def _in(src, dom=D):
    return WorkflowEdge(source=src, target=dom, kind=EdgeKind.IN_DOMAIN)


# ── NodeIdFactory ──────────────────────────────────────────────────────


def test_node_id_factory_deterministic_ids():
    f = NodeIdFactory
    assert f.domain_id(None) == GLOBAL_DOMAIN_ID
    assert f.domain_id("x") == "domain:x"
    assert f.tool_hub_id("domain:x", ToolKind.READ) == "tool_hub:domain:x:Read"
    assert f.file_id("/a") == f.file_id("/a")
    assert f.file_id("/a") != f.file_id("/b")
    assert f.file_id("/a").startswith("file:") and len(f.file_id("/a")) == 15
    assert f.skill_id("/commit ") == "skill:commit"
    assert f.skill_id("") == "skill:unknown"
    assert f.hook_id("Pre", "s.sh").startswith("hook:Pre:")
    assert f.agent_id("domain:x", "a") == "agent:domain:x:a"
    assert f.command_id("h") == "command:h"
    assert f.memory_id(5) == "memory:5"
    assert f.mcp_id("srv") == "mcp:srv"
    sid = f.symbol_id("/f.py", "m.C.f")
    assert sid.startswith("symbol:") and len(sid) == len("symbol:") + 12
    assert f.entity_id(3) == "entity:3"
    assert f.wiki_id(4) == "wiki:4"


# ── models ─────────────────────────────────────────────────────────────


def test_edge_confidence_bounds_enforced():
    WorkflowEdge(source="a", target="b", kind=EdgeKind.CALLS, confidence=0.5)
    with pytest.raises(ValidationError):
        WorkflowEdge(source="a", target="b", kind=EdgeKind.CALLS, confidence=1.5)


def test_node_allows_extra_fields():
    n = WorkflowNode(id="memory:1", kind=NodeKind.MEMORY, plasticity=0.3)
    assert n.model_dump()["plasticity"] == 0.3


# ── edge_provenance_defaults ───────────────────────────────────────────


def test_edge_provenance_structural_defaults():
    assert edge_provenance_defaults("defined_in") == (1.0, "direct-ast")
    assert edge_provenance_defaults("about_entity") == (1.0, "memory-entities-link")


def test_edge_provenance_heuristic_and_overrides():
    assert edge_provenance_defaults("calls") == (None, None)
    assert edge_provenance_defaults("calls", 0.4, "r") == (0.4, "r")
    assert edge_provenance_defaults("member_of", 0.0, "x") == (0.0, "x")
    assert edge_provenance_defaults("calls", None, "") == (None, None)


# ── validate_graph ─────────────────────────────────────────────────────


def test_validate_graph_accepts_wellformed():
    hub = NodeIdFactory.tool_hub_id(D, ToolKind.READ)
    nodes = [
        _dom(),
        WorkflowNode(id=hub, kind=NodeKind.TOOL_HUB, domain_id=D),
        WorkflowNode(id="file:abc", kind=NodeKind.FILE, domain_id=D),
        WorkflowNode(id="symbol:s1", kind=NodeKind.SYMBOL),
    ]
    edges = [
        _in(hub),
        _in("file:abc"),
        WorkflowEdge(source="symbol:s1", target="file:abc", kind=EdgeKind.DEFINED_IN),
    ]
    validate_graph(nodes, edges)


def test_validate_duplicate_id():
    with pytest.raises(GraphValidationError, match="duplicate node id"):
        validate_graph([_dom(), _dom()], [])


def test_validate_prefix_mismatch():
    with pytest.raises(GraphValidationError, match="does not match kind"):
        validate_graph([WorkflowNode(id="x:1", kind=NodeKind.MEMORY)], [])


def test_validate_missing_edge_endpoints():
    with pytest.raises(GraphValidationError, match="edge source missing"):
        validate_graph([_dom()], [_in("memory:1")])
    with pytest.raises(GraphValidationError, match="edge target missing"):
        validate_graph(
            [_dom(), WorkflowNode(id="memory:1", kind=NodeKind.MEMORY)],
            [_in("memory:1", "domain:nope")],
        )


def test_validate_in_domain_counts():
    mem = WorkflowNode(id="memory:1", kind=NodeKind.MEMORY)
    with pytest.raises(GraphValidationError, match="exactly one"):
        validate_graph([_dom(), mem], [])
    with pytest.raises(GraphValidationError, match="exactly one"):
        validate_graph(
            [_dom(), _dom("domain:b"), mem],
            [_in("memory:1"), _in("memory:1", "domain:b")],
        )
    f = WorkflowNode(id="file:x", kind=NodeKind.FILE)
    with pytest.raises(GraphValidationError, match="no in_domain edge"):
        validate_graph([_dom(), f], [])
    # multi-domain kinds accept >1
    validate_graph(
        [_dom(), _dom("domain:b"), f],
        [_in("file:x"), _in("file:x", "domain:b")],
    )


def test_validate_symbol_needs_file_anchor():
    sym = WorkflowNode(id="symbol:s", kind=NodeKind.SYMBOL)
    with pytest.raises(GraphValidationError, match="no defined_in edge"):
        validate_graph([_dom(), sym], [])


def test_validate_tool_hub_rules():
    ok = NodeIdFactory.tool_hub_id(D, ToolKind.READ)
    with pytest.raises(GraphValidationError, match="unknown domain"):
        validate_graph(
            [
                _dom(),
                WorkflowNode(id="tool_hub:domain:zzz:Read", kind=NodeKind.TOOL_HUB),
            ],
            [_in("tool_hub:domain:zzz:Read")],
        )
    with pytest.raises(GraphValidationError, match="unknown tool"):
        validate_graph(
            [
                _dom(),
                WorkflowNode(id=f"tool_hub:{D}:Nope", kind=NodeKind.TOOL_HUB),
            ],
            [_in(f"tool_hub:{D}:Nope")],
        )
    assert ok  # sanity


def test_split_tool_hub_id():
    assert _split_tool_hub_id("tool_hub:domain:x:Read") == ("domain:x", "Read")
    with pytest.raises(GraphValidationError, match="malformed"):
        _split_tool_hub_id("tool_hub:x")
    with pytest.raises(GraphValidationError, match="malformed"):
        _split_tool_hub_id("other:a:b")


# ── builder ────────────────────────────────────────────────────────────


def _inputs():
    return WorkflowBuildInputs(
        tool_events=[
            {
                "tool": "Edit",
                "file_path": "/r/a.py",
                "domain": D,
                "count": 2,
                "first_ts": "2026-01-01",
                "last_ts": "2026-01-03",
            },
            {
                "tool": "read",
                "file_path": "/r/a.py",
                "domain": D,
                "count": 1,
                "first_ts": "2025-12-31",
                "last_ts": "2026-01-02",
            },
            {"tool": "Grep", "file_path": "/r/b.py", "domain": None},
            {"tool": "Bash", "file_path": None, "domain": D},
        ],
        skill_paths=[{"name": "/commit", "path": "/s/commit.md", "domains": [D]}],
        hook_defs=[
            {"event": "Pre", "matcher": "Bash", "command": "x.sh", "domain": D},
            {"event": "Pre", "matcher": "Bash", "command": "x.sh", "domain": D},
        ],
        agent_events=[{"subagent_type": "Explore", "domain": D, "count": 3}],
        command_events=[{"cmd": "ls", "cmd_hash": "h1", "domain": D, "count": 2}],
        memories=[
            {
                "id": 1,
                "domain": D,
                "consolidation_stage": "late_ltp",
                "heat_base": 0.5,
                "content": "hello\nworld",
                "tags": ["t"],
                "importance": 0.9,
            }
        ],
        discussions=[{"session_id": "sess1234", "domain": D, "message_count": 4}],
        entities=[{"id": 9, "name": "Thing", "type": "concept", "domain": D}],
        discussion_file_events=[
            {"session_id": "sess1234", "file_path": "/r/a.py", "count": 2}
        ],
        command_file_events=[{"cmd_hash": "h1", "file_path": "/r/a.py"}],
        discussion_tool_events=[
            {"session_id": "sess1234", "tool": "Read", "domain": D}
        ],
        discussion_agent_events=[
            {"session_id": "sess1234", "subagent_type": "Plan", "domain": D}
        ],
        memory_entity_edges=[{"memory_id": 1, "entity_id": 9}],
    )


def test_build_full_graph_is_valid_and_correct():
    nodes, edges = WorkflowGraphBuilder().build(_inputs())
    validate_graph(nodes, edges)
    by_id = {n.id: n for n in nodes}
    assert GLOBAL_DOMAIN_ID in by_id and D in by_id
    fa = by_id[NodeIdFactory.file_id("/r/a.py")]
    assert fa.primary_cluster == "edit_write"
    assert fa.first_seen == "2025-12-31"
    assert fa.last_accessed == "2026-01-03"
    assert fa.last_modified == "2026-01-03"
    fb = by_id[NodeIdFactory.file_id("/r/b.py")]
    assert fb.domain_id == GLOBAL_DOMAIN_ID
    mem = by_id["memory:1"]
    assert mem.stage == "late_ltp" and mem.label == "hello world"
    assert mem.importance == 0.9
    kinds = {(e.kind, e.source, e.target) for e in edges}
    assert (
        EdgeKind.TOOL_USED_FILE,
        NodeIdFactory.tool_hub_id(D, ToolKind.EDIT),
        fa.id,
    ) in kinds
    assert any(k[0] == EdgeKind.DISCUSSION_TOUCHED_FILE for k in kinds)
    assert any(k[0] == EdgeKind.ABOUT_ENTITY for k in kinds)
    # duplicated hook collapses to a single node
    assert sum(1 for n in nodes if n.kind == "hook") == 1


def test_streaming_build_batches_and_on_batch():
    b = WorkflowGraphBuilder()
    pushed = []
    batches = list(
        b.streaming_build(_inputs(), on_batch=lambda *a: pushed.append(a[0]))
    )
    labels = [x[0] for x in batches]
    assert labels[0] == "tool_events"
    assert labels.index("files") > labels.index("entities")
    assert labels[-2:] == ["ast_symbols", "ast_edges"]
    assert pushed == labels
    first_nodes = batches[0][1]
    assert GLOBAL_DOMAIN_ID in {n.id for n in first_nodes}
    files_batch = {x[0]: x for x in batches}["files"]
    assert {n.kind for n in files_batch[1]} == {"file"}


def test_dedupe_sums_weights():
    b = WorkflowGraphBuilder()
    e = WorkflowEdge(source="a", target="b", kind=EdgeKind.CALLS, weight=2.0)
    _, out = b._dedupe_and_link([], [e, e, e])
    assert len(out) == 1 and out[0].weight == 6.0


def test_ensure_domain_and_hubs_idempotent():
    b = WorkflowGraphBuilder()
    b._ensure_domain(D)
    b._ensure_domain(D)
    first = b._build_tool_hubs(D, [ToolKind.READ, ToolKind.GREP])
    again = b._build_tool_hubs(D, [ToolKind.READ])
    assert len(first) == 2 and again == []
    assert len(b._nodes) == 3
    assert b._nodes[D].label == "proj"


def test_assign_domain_variants():
    b = WorkflowGraphBuilder()
    assert b._assign_domain(None) == GLOBAL_DOMAIN_ID
    assert b._assign_domain("domain:q") == "domain:q"
    # a leading-dash slug is noise and is bucketed to global
    assert b._assign_domain("-noise") == GLOBAL_DOMAIN_ID
    # single-word, unknown to the registry -> global
    assert b._assign_domain("zzqqxx") == GLOBAL_DOMAIN_ID
    assert b._assign_domain("zz-qq-xx-unknown") == "domain:zz-qq-xx-unknown"


def test_malformed_inputs_raise():
    with pytest.raises(ValueError, match="missing key"):
        WorkflowGraphBuilder().build(
            WorkflowBuildInputs(tool_events=[{"file_path": "x"}])
        )
    with pytest.raises(ValueError, match="unknown ToolKind"):
        WorkflowGraphBuilder().build(
            WorkflowBuildInputs(tool_events=[{"tool": "Nope"}])
        )
