"""Behavioral contracts for workflow graph relational and AST ingestion."""

from __future__ import annotations

from cortex_viz.core import workflow_graph_builder_relational as relational
from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder
from cortex_viz.core.workflow_graph_schema import (
    GLOBAL_DOMAIN_ID,
    EdgeKind,
    NodeIdFactory,
    NodeKind,
)


def add_child(builder, node_id, kind, domain="domain:alpha"):
    builder._ensure_domain(domain)
    builder._add_child(node_id, kind, node_id, "#000", domain, 1.0)


def use_literal_domains(builder):
    def assign(value):
        return value if str(value).startswith("domain:") else f"domain:{value}"

    builder._assign_domain = assign


def test_discussion_file_and_command_file_links_require_existing_nodes():
    builder = WorkflowGraphBuilder()
    builder._ensure_domain(GLOBAL_DOMAIN_ID)
    relational.ingest_discussion_file(
        builder, {"session_id": "s", "file_path": "/a.py"}
    )
    add_child(builder, "discussion:s", NodeKind.DISCUSSION)
    relational.ingest_discussion_file(
        builder, {"session_id": "s", "file_path": "/a.py"}
    )
    fid = NodeIdFactory.file_id("/a.py")
    add_child(builder, fid, NodeKind.FILE)
    relational.ingest_discussion_file(
        builder, {"session_id": "s", "file_path": "/a.py", "count": 3}
    )
    assert builder._edges[-1].kind == EdgeKind.DISCUSSION_TOUCHED_FILE
    assert builder._edges[-1].weight == 3.0

    relational.ingest_command_file(builder, {"cmd_hash": "h", "file_path": "/a.py"})
    add_child(builder, NodeIdFactory.command_id("h"), NodeKind.COMMAND)
    relational.ingest_command_file(
        builder, {"cmd_hash": "h", "file_path": "/a.py", "count": 2}
    )
    assert builder._edges[-1].kind == EdgeKind.COMMAND_TOUCHED_FILE
    assert builder._edges[-1].weight == 2.0


def test_discussion_tool_agent_and_command_materialization():
    builder = WorkflowGraphBuilder()
    use_literal_domains(builder)
    add_child(builder, "discussion:s", NodeKind.DISCUSSION)
    before = len(builder._edges)
    relational.ingest_discussion_tool(
        builder, {"session_id": "missing", "tool": "Read", "domain": "alpha"}
    )
    relational.ingest_discussion_tool(
        builder, {"session_id": "s", "tool": "Unknown", "domain": "alpha"}
    )
    assert len(builder._edges) == before
    relational.ingest_discussion_tool(
        builder, {"session_id": "s", "tool": "Read", "domain": "alpha", "count": 4}
    )
    assert builder._edges[-1].kind == EdgeKind.DISCUSSION_USED_TOOL
    assert builder._edges[-1].weight == 4.0

    relational.ingest_discussion_agent(
        builder,
        {"session_id": "missing", "subagent_type": "Explore", "domain": "alpha"},
    )
    relational.ingest_discussion_agent(
        builder,
        {
            "session_id": "s",
            "subagent_type": "Explore",
            "domain": "alpha",
            "count": 2,
        },
    )
    agent_id = NodeIdFactory.agent_id("domain:alpha", "Explore")
    assert agent_id in builder._nodes
    assert builder._edges[-1].kind == EdgeKind.DISCUSSION_SPAWNED_AGENT
    relational.ingest_discussion_agent(
        builder,
        {"session_id": "s", "subagent_type": "Explore", "domain": "alpha"},
    )

    relational.ingest_discussion_command(
        builder, {"session_id": "missing", "cmd_hash": "h"}
    )
    relational.ingest_discussion_command(
        builder,
        {"session_id": "s", "cmd_hash": "h", "cmd": "echo hi", "count": 3},
    )
    cmd_id = NodeIdFactory.command_id("h")
    assert cmd_id in builder._nodes
    assert builder._nodes[cmd_id].body == "echo hi"
    assert builder._edges[-1].kind == EdgeKind.DISCUSSION_RAN_COMMAND
    relational.ingest_discussion_command(
        builder, {"session_id": "s", "cmd_hash": "h", "count": 1}
    )


def test_skill_and_mcp_usage_expand_cross_domain_membership():
    builder = WorkflowGraphBuilder()
    use_literal_domains(builder)
    relational.ingest_skill_usage(
        builder, {"name": "review", "domain": "alpha", "count": 2}
    )
    relational.ingest_skill_usage(
        builder, {"name": "review", "domain": "beta", "count": 3}
    )
    relational.ingest_skill_usage(
        builder, {"name": "review", "domain": "beta", "count": 1}
    )
    skill = builder._nodes[NodeIdFactory.skill_id("review")]
    assert "domain:beta" in skill.extra_domain_ids
    assert builder._edges[-1].kind == EdgeKind.INVOKED_SKILL

    relational.ingest_mcp_usage(
        builder,
        {"server": "cortex", "tool": "recall", "domain": "alpha", "count": 2},
    )
    relational.ingest_mcp_usage(
        builder,
        {"server": "cortex", "tool": "remember", "domain": "beta", "count": 3},
    )
    relational.ingest_mcp_usage(
        builder,
        {"server": "cortex", "tool": "recall", "domain": "beta", "count": 1},
    )
    mcp = builder._nodes[NodeIdFactory.mcp_id("cortex")]
    assert "domain:beta" in mcp.extra_domain_ids
    assert mcp.count == 5
    assert builder._edges[-1].kind == EdgeKind.INVOKED_MCP
    assert builder._edges[-1].label == "recall"


def test_symbol_ingestion_synthesizes_file_and_is_idempotent():
    builder = WorkflowGraphBuilder()
    builder._ensure_domain(GLOBAL_DOMAIN_ID)
    symbol = {
        "file_path": "/repo/a.py",
        "qualified_name": "pkg.A.run",
        "symbol_type": "method",
        "signature": "run()",
        "language": "python",
        "line": 4,
    }
    relational.ingest_symbol(builder, symbol)
    fid = NodeIdFactory.file_id("/repo/a.py")
    sid = NodeIdFactory.symbol_id("/repo/a.py", "pkg.A.run")
    assert fid in builder._nodes
    assert sid in builder._nodes
    assert builder._nodes[sid].label == "run"
    assert builder._nodes[sid].signature == "run()"
    defined = [edge for edge in builder._edges if edge.kind == EdgeKind.DEFINED_IN]
    assert defined[0].confidence == 1.0
    before = len(builder._edges)
    relational.ingest_symbol(builder, symbol)
    assert len(builder._edges) == before

    relational.ingest_symbol(
        builder,
        {
            "file_path": "/repo/a.py",
            "qualified_name": "NoDot",
            "symbol_type": "unknown",
        },
    )


def test_ast_edge_endpoint_resolution_and_ingestion():
    builder = WorkflowGraphBuilder()
    builder._ensure_domain(GLOBAL_DOMAIN_ID)
    for qname in ("pkg.a", "pkg.b"):
        relational.ingest_symbol(
            builder,
            {
                "file_path": "/repo/a.py",
                "qualified_name": qname,
                "symbol_type": "function",
            },
        )

    assert relational._resolve_ast_edge_endpoints(builder, {}) is None
    assert (
        relational._resolve_ast_edge_endpoints(
            builder,
            {"dst_file": "/repo/a.py", "dst_name": "missing", "kind": "calls"},
        )
        is None
    )
    assert (
        relational._resolve_ast_edge_endpoints(
            builder,
            {
                "src_file": "",
                "dst_file": "/repo/a.py",
                "dst_name": "pkg.b",
                "kind": "imports",
            },
        )
        is None
    )
    assert (
        relational._resolve_ast_edge_endpoints(
            builder,
            {
                "src_file": "/missing.py",
                "dst_file": "/repo/a.py",
                "dst_name": "pkg.b",
                "kind": "imports",
            },
        )
        is None
    )
    assert (
        relational._resolve_ast_edge_endpoints(
            builder,
            {
                "src_file": "/repo/a.py",
                "src_name": "",
                "dst_file": "/repo/a.py",
                "dst_name": "pkg.b",
                "kind": "calls",
            },
        )
        is None
    )
    assert (
        relational._resolve_ast_edge_endpoints(
            builder,
            {
                "src_file": "/repo/a.py",
                "src_name": "missing",
                "dst_file": "/repo/a.py",
                "dst_name": "pkg.b",
                "kind": "calls",
            },
        )
        is None
    )

    edges = [
        {
            "src_file": "/repo/a.py",
            "src_name": "pkg.a",
            "dst_file": "/repo/a.py",
            "dst_name": "pkg.b",
            "kind": "calls",
            "confidence": 0.7,
            "reason": "resolver",
        },
        {
            "src_file": "/repo/a.py",
            "dst_file": "/repo/a.py",
            "dst_name": "pkg.b",
            "kind": "imports",
        },
        {
            "src_file": "/repo/a.py",
            "src_name": "pkg.a",
            "dst_file": "/repo/a.py",
            "dst_name": "pkg.b",
            "kind": "member_of",
        },
    ]
    for edge in edges:
        relational.ingest_ast_edge(builder, edge)
    assert [edge.kind for edge in builder._edges[-3:]] == [
        EdgeKind.CALLS,
        EdgeKind.IMPORTS,
        EdgeKind.MEMBER_OF,
    ]
    assert builder._edges[-3].confidence == 0.7
    assert builder._edges[-1].confidence == 1.0
    before = len(builder._edges)
    relational.ingest_ast_edge(builder, {"kind": "calls"})
    assert len(builder._edges) == before
