"""Composition contracts for the workflow-graph handler."""

from __future__ import annotations

from types import SimpleNamespace

from cortex_viz.handlers import workflow_graph


class _Source:
    instance = None

    def __init__(self):
        type(self).instance = self
        self.calls = []

    def _rows(self, label):
        self.calls.append(label)
        return [
            {"id": f"{label}-keep", "domain": "keep", "file_path": "/repo/a.py"},
            {"id": f"{label}-drop", "domain": "drop", "file_path": None},
        ]

    def load_skills(self):
        return self._rows("skills")

    def load_hooks(self):
        return self._rows("hooks")

    def load_agent_events(self):
        return self._rows("agents")

    def load_command_events(self, store):
        assert store == "store"
        return self._rows("commands")

    def load_memories(self, store, *, min_heat, limit):
        assert (store, min_heat, limit) == ("store", 0.4, 7)
        return self._rows("memories")

    def load_discussions(self):
        return self._rows("discussions")

    def load_skill_usage(self):
        return self._rows("skill_usage")

    def load_mcp_usage(self):
        return self._rows("mcp_usage")

    def load_discussion_tool_uses(self):
        return self._rows("discussion_tools")

    def load_discussion_agents(self):
        return self._rows("discussion_agents")

    def load_discussion_commands(self):
        return self._rows("discussion_commands")

    def load_entities(self, store):
        assert store == "store"
        return self._rows("entities")

    def load_memory_entity_edges(self, store):
        assert store == "store"
        return self._rows("memory_entity_edges")

    def load_tool_events(self, store):
        assert store == "store"
        return self._rows("tool_events")

    def load_discussion_files(self):
        return self._rows("discussion_files")

    def load_command_files(self, store, known_paths):
        assert store == "store"
        assert known_paths == {"/repo/a.py"}
        return self._rows("command_files")


class _Builder:
    inputs = None

    def build(self, inputs):
        type(self).inputs = inputs
        nodes = [
            SimpleNamespace(kind=kind, id=f"{kind}:1")
            for kind in ("domain", "memory", "file", "discussion", "symbol", "entity")
        ]
        return nodes, [SimpleNamespace(id="edge:1")]


class _StreamingBuilder:
    inputs = None

    def streaming_build(self, inputs, *, on_batch):
        type(self).inputs = inputs
        first = [SimpleNamespace(kind="domain", id="domain:1")]
        second = [SimpleNamespace(kind="file", id="file:1")]
        on_batch("first", first, [])
        yield "first", first, []
        yield "second", second, [SimpleNamespace(id="edge:1")]

    def _dedupe_and_link(self, nodes, edges):
        return nodes, edges


class _ASTSource:
    def enabled(self):
        return True

    def load_symbols(self, paths):
        assert paths == []
        return [{"id": "ap-symbol"}]

    def load_ast_edges(self, paths):
        assert paths == []
        return [{"id": "ap-edge"}]


class _NativeSource:
    def load_symbols(self, paths):
        assert paths == ["/repo/a.py"]
        return [{"id": "native-symbol"}]

    def load_ast_edges(self, paths):
        assert paths == ["/repo/a.py"]
        return [{"id": "native-edge"}]


def _patch_composition(monkeypatch, builder):
    monkeypatch.setattr(workflow_graph, "WorkflowGraphSource", _Source)
    monkeypatch.setattr(workflow_graph, "WorkflowGraphBuilder", builder)
    monkeypatch.setattr(workflow_graph, "WorkflowGraphASTSource", _ASTSource)
    monkeypatch.setattr(workflow_graph, "WorkflowGraphNativeASTSource", _NativeSource)
    monkeypatch.setattr(
        workflow_graph, "_node_to_dict", lambda node: {"id": node.id, "kind": node.kind}
    )
    monkeypatch.setattr(workflow_graph, "_edge_to_dict", lambda edge: {"id": edge.id})


def test_full_build_loads_filters_validates_and_serializes(monkeypatch):
    _patch_composition(monkeypatch, _Builder)
    validated = []
    loaded = []
    monkeypatch.setattr(
        workflow_graph,
        "validate_graph",
        lambda nodes, edges: validated.append((nodes, edges)),
    )

    payload = workflow_graph.build_workflow_graph(
        "store",
        domain_filter="keep",
        min_memory_heat=0.4,
        memory_limit=7,
        stage="full",
        on_source_loaded=lambda label, count: loaded.append((label, count)),
    )

    inputs = _Builder.inputs
    assert inputs is not None
    assert len(inputs.tool_events) == 1
    assert len(inputs.agent_events) == 1
    assert len(inputs.command_events) == 1
    assert len(inputs.memories) == 1
    assert len(inputs.discussions) == 1
    assert len(inputs.skill_usage_events) == 1
    assert len(inputs.mcp_usage_events) == 1
    assert len(inputs.discussion_tool_events) == 1
    assert len(inputs.discussion_agent_events) == 1
    assert len(inputs.entities) == 1
    assert [item["id"] for item in inputs.ast_symbols] == [
        "ap-symbol",
        "native-symbol",
    ]
    assert [item["id"] for item in inputs.ast_edges] == ["ap-edge", "native-edge"]
    assert validated and validated[0][0][0].kind == "domain"
    assert len(loaded) == 13
    assert all(count == 2 for _, count in loaded)
    assert payload["meta"]["ast_enabled"] is True
    assert payload["meta"]["counts"] == {
        "nodes": 6,
        "edges": 1,
        "tool_events": 1,
        "skills": 2,
        "hooks": 2,
        "agents": 1,
        "commands": 1,
        "memories": 1,
        "discussions": 1,
        "files": 1,
        "symbols": 1,
        "ast_edges": 2,
        "entities": 1,
        "memory_entity_edges": 2,
    }
    assert payload["links"] == payload["edges"] == [{"id": "edge:1"}]


def test_skeleton_streaming_build_uses_only_lightweight_sources(monkeypatch):
    _patch_composition(monkeypatch, _StreamingBuilder)
    monkeypatch.setattr(workflow_graph, "validate_graph", lambda *_args: None)
    batches = []

    payload = workflow_graph.build_workflow_graph(
        "store",
        stage="skeleton",
        on_batch=lambda *args: batches.append(args),
    )

    assert _Source.instance.calls == ["skills", "hooks"]
    assert batches and batches[0][0] == "first"
    assert payload["meta"]["ast_enabled"] is False
    assert payload["meta"]["node_count"] == 2
    assert payload["meta"]["counts"]["tool_events"] == 0


def test_files_streaming_delegates_to_interleaved_loader(monkeypatch):
    source = _Source()
    monkeypatch.setattr(workflow_graph, "WorkflowGraphSource", lambda: source)
    expected = {"nodes": ["streamed"], "edges": [], "meta": {}}
    received = {}

    def interleaved(**kwargs):
        received.update(kwargs)
        return expected

    monkeypatch.setattr(workflow_graph, "_build_interleaved", interleaved)

    def callback(*_args):
        return None

    result = workflow_graph.build_workflow_graph(
        "store",
        stage="files",
        domain_filter="domain",
        min_memory_heat=0.2,
        memory_limit=3,
        on_source_loaded=callback,
        on_batch=callback,
        defer_native_ast=True,
    )

    assert result is expected
    assert received["source"] is source
    assert received["stage"] == "files"
    assert received["notify_loaded"]("none", None) is None
    assert received["defer_native_ast"] is True
