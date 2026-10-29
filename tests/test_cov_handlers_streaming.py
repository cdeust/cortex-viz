"""Behaviour tests for the interleaved workflow-graph streaming build.

The data source (PG / JSONL loaders) and the AST loaders are the external
boundary and are replaced with in-memory fakes; every ingest, emit, discard
and serialization step runs for real.
"""

from __future__ import annotations

import pytest

from cortex_viz.core.workflow_graph_schema import NodeIdFactory
from cortex_viz.handlers import workflow_graph_streaming as wgs
from cortex_viz.infrastructure import (
    workflow_graph_source_ast,
    workflow_graph_source_native_ast,
)

D = "domain:proj"


class _Source:
    def __init__(self, discussions=None):
        self._discussions = discussions

    def load_skills(self):
        return [{"name": "/commit", "path": "/s/c.md", "domains": [D]}]

    def load_hooks(self):
        return [{"event": "Pre", "command": "x.sh", "domain": D}]

    def load_agent_events(self):
        return [{"subagent_type": "Explore", "domain": D, "count": 2}]

    def load_command_events(self, store):
        return [{"cmd": "ls", "cmd_hash": "h1", "domain": D, "count": 1}]

    def load_discussions(self):
        if self._discussions is not None:
            return self._discussions
        return [{"session_id": "sess1", "domain": D, "message_count": 3}]

    def load_tool_events(self, store):
        return [
            {"tool": "Read", "domain": D, "file_path": "/r/one.py", "count": 1},
            {"tool": "Edit", "domain": "domain:other", "file_path": "/r/two.py"},
        ]

    def load_entities(self, store):
        return [{"id": 9, "name": "Thing", "type": "concept", "domain": D}]

    def load_wiki_pages(self, store):
        return [{"id": 1, "title": "Page", "domain": D, "heat": 0.1}]

    def load_wiki_links(self, store):
        return [{"src_page_id": 1, "dst_page_id": 1, "link_kind": "x"}]

    def load_wiki_session_links(self, store):
        return [{"page_id": 1, "session_id": "sess1"}]

    def load_wiki_memory_links(self, store):
        return [{"page_id": 1, "memory_id": 1}]

    def load_discussion_files(self):
        return [{"session_id": "sess1", "file_path": "/r/one.py", "domain": D}]

    def load_command_files(self, store, known_paths):
        self.known_paths = known_paths
        return [{"cmd_hash": "h1", "file_path": "/r/one.py"}]

    def load_skill_usage(self):
        return []

    def load_mcp_usage(self):
        return []

    def load_discussion_tool_uses(self):
        return [{"session_id": "sess1", "tool": "Read", "domain": D}]

    def load_discussion_agents(self):
        return [{"session_id": "sess1", "subagent_type": "Plan", "domain": D}]

    def load_discussion_commands(self):
        return []

    def load_memory_entity_edges(self, store):
        return [
            {"memory_id": 1, "entity_id": 9},
            {"memory_id": None, "entity_id": 9},
        ]

    def iter_memories_chunked(self, store, min_heat=0.0, chunk_size=1000, limit=0):
        self.mem_args = (min_heat, chunk_size, limit)
        yield [
            {"id": 1, "domain": D, "content": "first", "heat_base": 0.4},
            {"id": 2, "domain": "domain:other", "content": "second"},
        ]
        yield [{"id": 3, "domain": D, "content": "third"}]

    def load_memory_associations(self, store):
        return [{"source_memory_id": 1, "target_memory_id": 3, "weight": 0.5}]

    def load_supersede_edges(self, store):
        return [{"source_memory_id": 3, "target_memory_id": 1}]


def _run(source=None, **over):
    batches, progress, loaded = [], [], []
    kw = {
        "store": None,
        "source": source or _Source(),
        "domain_filter": None,
        "min_memory_heat": 0.25,
        "memory_limit": 0,
        "stage": "baseline",
        "defer_native_ast": True,
        "on_source_loaded": lambda label, n: progress.append((label, n)),
        "on_batch": lambda label, nodes, edges: batches.append((label, nodes, edges)),
        "notify_loaded": lambda label, items: loaded.append((label, len(items))),
    }
    kw.update(over)
    return wgs._build_interleaved(**kw), batches, progress, loaded


def _kinds(result):
    return {n["id"]: n["kind"] for n in result["nodes"]}


def _edge_kinds(result):
    return {e["kind"] for e in result["edges"]}


def test_uncapped_build_retains_memories_as_slim_dicts():
    src = _Source()
    res, batches, progress, loaded = _run(src)
    ids = _kinds(res)
    # every memory is retained even though builder discards them per chunk
    assert {"memory:1", "memory:2", "memory:3"} <= set(ids)
    assert ids["entity:9"] == "entity" and ids["wiki:1"] == "wiki"
    assert res["meta"]["memory_count"] == 3
    assert res["meta"]["counts"]["memories"] == 3
    assert res["meta"]["streaming"] == "interleaved"
    assert res["meta"]["ast_enabled"] is False
    assert res["nodes"] is not None and res["links"] is res["edges"]
    ek = _edge_kinds(res)
    assert {"associates_with", "supersedes", "documents", "about_entity"} <= ek
    assert {"wiki_links", "cited_in", "in_domain"} <= ek
    assert src.mem_args == (0.25, 1000, 0)
    assert src.known_paths == {"/r/one.py", "/r/two.py"}
    assert ("memories", 3) in progress
    assert ("memory_entity_edges", 1) in progress
    assert ("supersede", 1) in progress and ("associations", 1) in progress
    assert ("wiki_memory", 1) in progress
    assert ("skills", 1) in loaded and ("wiki_pages", 1) in loaded
    # memory batches are emitted while the data streams
    labels = [b[0] for b in batches]
    assert labels.count("memories") == 2
    # uncapped: associations/supersede are not batch-emitted
    assert "associations" not in labels and "supersede" not in labels
    mem_node = next(n for n in res["nodes"] if n["id"] == "memory:1")
    assert mem_node["type"] == "memory" and mem_node["domain"] == "proj"


def test_capped_build_keeps_memories_in_builder_and_emits_edge_batches():
    res, batches, progress, _ = _run(memory_limit=10)
    labels = [b[0] for b in batches]
    assert {"associations", "supersede", "wiki_memory"} <= set(labels)
    ids = _kinds(res)
    assert {"memory:1", "memory:2", "memory:3"} <= set(ids)
    assert {"associates_with", "supersedes", "documents"} <= _edge_kinds(res)
    assert ("associations", 1) in progress and ("supersede", 1) in progress
    assert ("wiki_memory", 1) in progress


def test_domain_filter_limits_every_stream():
    res, _batches, _, _loaded = _run(domain_filter=D)
    ids = _kinds(res)
    assert "memory:2" not in ids  # other-domain memory filtered
    assert NodeIdFactory.file_id("/r/two.py") not in ids
    assert NodeIdFactory.file_id("/r/one.py") in ids
    assert res["meta"]["domain_filter"] == D
    assert res["meta"]["counts"]["tool_events"] == 1
    assert res["meta"]["counts"]["memories"] == 2


def test_wiki_disabled_skips_wiki_nodes(monkeypatch):
    monkeypatch.setattr(wgs, "_WIKI_ON", False)
    res, _, progress, loaded = _run()
    assert "wiki" not in set(_kinds(res).values())
    assert not any(label.startswith("wiki") for label, _ in loaded)
    assert all(label != "wiki_memory" for label, _ in progress)


def test_no_callbacks_required():
    res, *_ = _run(on_batch=None, on_source_loaded=None)
    assert res["meta"]["node_count"] == len(res["nodes"])
    res_cap, *_ = _run(on_batch=None, on_source_loaded=None, memory_limit=5)
    assert res_cap["meta"]["counts"]["memories"] == 3


def test_large_source_is_emitted_in_chunks_of_500():
    many = [
        {"session_id": f"s{i}", "domain": D, "message_count": 1} for i in range(1100)
    ]
    res, batches, _, _ = _run(_Source(many))
    disc = [b for b in batches if b[0] == "discussions"]
    assert len(disc) == 3
    assert res["meta"]["discussion_count"] == 1100


class _FakeAST:
    def enabled(self):
        return True

    def load_symbols(self, paths):
        return [
            {
                "file_path": "/r/one.py",
                "qualified_name": "m.f",
                "symbol_type": "function",
            }
        ]

    def load_ast_edges(self, paths):
        return []


class _FakeNative:
    def load_symbols(self, paths):
        return [
            {"file_path": "/r/one.py", "qualified_name": "m.C", "symbol_type": "class"}
        ]

    def load_ast_edges(self, paths):
        return [
            {
                "kind": "calls",
                "src_file": "/r/one.py",
                "src_name": "m.f",
                "dst_file": "/r/one.py",
                "dst_name": "m.C",
            }
        ]


def test_full_stage_ingests_ast_symbols_and_edges(monkeypatch):
    monkeypatch.setattr(workflow_graph_source_ast, "WorkflowGraphASTSource", _FakeAST)
    monkeypatch.setattr(
        workflow_graph_source_native_ast, "WorkflowGraphNativeASTSource", _FakeNative
    )
    res, batches, _, loaded = _run(stage="full", defer_native_ast=False)
    ids = _kinds(res)
    sym_ids = [i for i, k in ids.items() if k == "symbol"]
    assert len(sym_ids) == 2
    assert res["meta"]["ast_enabled"] is True
    assert res["meta"]["counts"]["symbols"] == 2
    assert ("ast_symbols", 2) in loaded and ("ast_edges", 1) in loaded
    assert "calls" in _edge_kinds(res)
    assert {"ast_symbols", "ast_edges"} <= {b[0] for b in batches}


def test_full_stage_with_ast_disabled(monkeypatch):
    class _Off(_FakeAST):
        def enabled(self):
            return False

    monkeypatch.setattr(workflow_graph_source_ast, "WorkflowGraphASTSource", _Off)
    monkeypatch.setattr(
        workflow_graph_source_native_ast, "WorkflowGraphNativeASTSource", _FakeNative
    )
    res, *_ = _run(stage="full", defer_native_ast=False)
    assert res["meta"]["counts"]["symbols"] == 1


def test_invalid_graph_is_rejected(monkeypatch):
    from cortex_viz.core.workflow_graph_schema import GraphValidationError

    def _bad(nodes, edges):
        raise GraphValidationError("nope")

    monkeypatch.setattr(wgs, "validate_graph", _bad)
    with pytest.raises(GraphValidationError):
        _run()
