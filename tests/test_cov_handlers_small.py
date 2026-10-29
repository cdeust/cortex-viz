"""Behaviour tests for small handler / helper modules.

Covers graph_stream, _tool_meta, tool_error_handler, workflow_graph_palette,
workflow_graph_serialize and get_methodology_graph. Only external boundaries
(sockets, the layout authority singleton, the profile store) are faked.
"""

from __future__ import annotations

import asyncio
import io
import json
import queue

import pytest

from cortex_viz import tool_error_handler as teh
from cortex_viz.core import workflow_graph_palette as pal
from cortex_viz.core.workflow_graph_schema import (
    EdgeKind,
    NodeKind,
    PrimaryToolCluster,
    ToolKind,
    WorkflowEdge,
    WorkflowNode,
)
from cortex_viz.handlers import _tool_meta, get_methodology_graph, graph_stream
from cortex_viz.handlers import workflow_graph_serialize as ser
from cortex_viz.server import graph_cache_state

# ── fakes ──────────────────────────────────────────────────────────────


class _Handler:
    def __init__(self, wfile=None):
        self.wfile = wfile if wfile is not None else io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass


class _BrokenFile:
    def write(self, b):
        raise BrokenPipeError

    def flush(self):
        pass


class _Authority:
    def __init__(self, items=()):
        self.q = queue.Queue()
        for it in items:
            self.q.put(it)
        self.unsubscribed = []

    def subscribe(self):
        return self.q

    def unsubscribe(self, q):
        self.unsubscribed.append(q)

    def stats(self):
        return {"subs": 1}


# ── graph_stream ───────────────────────────────────────────────────────


def test_write_chunk_frames_payload_and_reports_failure():
    h = _Handler()
    assert graph_stream._write_chunk(h, b"abc") is True
    assert h.wfile.getvalue() == b"3\r\nabc\r\n"
    assert graph_stream._write_chunk(_Handler(_BrokenFile()), b"abc") is False


def test_write_terminator_swallows_disconnect():
    h = _Handler()
    graph_stream._write_terminator(h)
    assert h.wfile.getvalue() == b"0\r\n\r\n"
    graph_stream._write_terminator(_Handler(_BrokenFile()))


def test_serve_streams_until_done_and_unsubscribes(monkeypatch):
    auth = _Authority(
        [
            (1, "slot", b"id: 1\nevent: slot\ndata: x\n\n"),
            (2, "done", b"id: 2\nevent: done\ndata: {}\n\n"),
            (3, "slot", b"never-sent"),
        ]
    )
    monkeypatch.setattr(graph_cache_state, "get_layout_authority", lambda: auth)
    h = _Handler()
    graph_stream.serve(h, store=None)
    assert h.status == 200
    assert h.headers["Content-Type"].startswith("text/event-stream")
    assert h.headers["Transfer-Encoding"] == "chunked"
    out = h.wfile.getvalue()
    assert b"event: slot" in out and b"event: done" in out
    assert b"never-sent" not in out
    assert out.endswith(b"0\r\n\r\n")
    assert auth.unsubscribed == [auth.q]


def test_serve_unsubscribes_when_client_disconnects(monkeypatch):
    auth = _Authority([(1, "slot", b"payload")])
    monkeypatch.setattr(graph_cache_state, "get_layout_authority", lambda: auth)
    graph_stream.serve(_Handler(_BrokenFile()), store=None)
    assert auth.unsubscribed == [auth.q]


def test_pump_one_keepalive_on_idle(monkeypatch):
    monkeypatch.setattr(graph_stream, "_KEEPALIVE_TIMEOUT_S", 0.01)
    h = _Handler()
    assert graph_stream._pump_one(h, queue.Queue()) is True
    assert b": ping" in h.wfile.getvalue()
    assert graph_stream._pump_one(_Handler(_BrokenFile()), queue.Queue()) is False


def test_serve_stats_returns_log_and_authority(monkeypatch):
    monkeypatch.setattr(graph_cache_state, "get_layout_authority", lambda: _Authority())
    monkeypatch.setattr(graph_stream._log, "stats", lambda: {"events": 7})
    h = _Handler()
    graph_stream.serve_stats(h, None)
    body = json.loads(h.wfile.getvalue())
    assert body == {"log": {"events": 7}, "authority": {"subs": 1}}
    assert h.status == 200
    assert h.headers["Content-Length"] == str(len(h.wfile.getvalue()))
    graph_stream.serve_stats(_Handler(_BrokenFile()), None)


# ── _tool_meta ─────────────────────────────────────────────────────────


def test_tool_kwargs_extracts_known_keys_only():
    schema = {
        "description": "d",
        "title": "t",
        "output_schema": {"type": "object"},
        "annotations": _tool_meta.READ_ONLY,
        "tags": {"a"},
        "inputSchema": {"ignored": True},
    }
    out = _tool_meta.tool_kwargs(schema)
    assert out == {
        "description": "d",
        "title": "t",
        "output_schema": {"type": "object"},
        "annotations": _tool_meta.READ_ONLY,
        "tags": {"a"},
    }


def test_tool_kwargs_camel_output_schema_and_empty():
    assert _tool_meta.tool_kwargs({"outputSchema": {"x": 1}}) == {
        "output_schema": {"x": 1}
    }
    assert _tool_meta.tool_kwargs({}) == {}


# ── tool_error_handler ─────────────────────────────────────────────────


def test_safe_handler_returns_result_and_empty_for_none():
    async def ok(args):
        return {"echo": args["x"]}

    async def none(args):
        return None

    assert asyncio.run(teh.safe_handler(ok, {"x": 5})) == {"echo": 5}
    assert asyncio.run(teh.safe_handler(none, {})) == {}


def test_safe_handler_converts_exception_to_dict():
    async def boom(args):
        raise RuntimeError("bad")

    out = asyncio.run(teh.safe_handler(boom, {}, tool_name="t"))
    assert out == {"error": "RuntimeError", "message": "bad", "tool": "t"}


def test_run_coroutine_on_thread_survives_loop_close_failure(monkeypatch):
    async def h(args):
        return {"ok": True}

    real = asyncio.new_event_loop

    def _new():
        loop = real()
        orig = loop.close

        def _close():
            orig()
            raise OSError("no close")

        loop.close = _close
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", _new)
    assert teh._run_coroutine_on_thread(h, {}) == {"ok": True}


# ── palette ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "counts,expected",
    [
        ({ToolKind.EDIT: 1, ToolKind.READ: 5}, PrimaryToolCluster.EDIT_WRITE),
        ({ToolKind.WRITE: 1}, PrimaryToolCluster.EDIT_WRITE),
        ({ToolKind.READ: 1, ToolKind.GREP: 3}, PrimaryToolCluster.READ),
        ({ToolKind.GREP: 1, ToolKind.BASH: 3}, PrimaryToolCluster.GREP_GLOB),
        ({ToolKind.GLOB: 1}, PrimaryToolCluster.GREP_GLOB),
        ({ToolKind.BASH: 1}, PrimaryToolCluster.BASH),
        ({}, PrimaryToolCluster.BASH),
    ],
)
def test_classify_primary_tool_precedence(counts, expected):
    assert pal.classify_primary_tool(counts) is expected
    assert pal.primary_tool_color(expected) == pal.PRIMARY_TOOL_COLORS[expected]


def test_every_tool_and_cluster_has_a_hex_color():
    for tool in (
        ToolKind.EDIT,
        ToolKind.WRITE,
        ToolKind.READ,
        ToolKind.GREP,
        ToolKind.GLOB,
        ToolKind.BASH,
        ToolKind.TASK,
    ):
        assert pal.TOOL_HUB_COLORS[tool].startswith("#")
    assert set(pal.PRIMARY_TOOL_COLORS) == set(PrimaryToolCluster)


# ── serialize ──────────────────────────────────────────────────────────


def test_plain_domain():
    assert ser._plain_domain(None) == ""
    assert ser._plain_domain("domain:cortex") == "cortex"
    assert ser._plain_domain("raw") == "raw"


def test_node_to_dict_real_domain_node():
    n = WorkflowNode(
        id="domain:cortex", kind=NodeKind.DOMAIN, domain_id="domain:cortex"
    )
    d = ser._node_to_dict(n)
    assert d["type"] == "domain"
    assert d["domain"] == "cortex"
    assert d["isGlobal"] is False
    assert d["selectableDomain"] is True


def test_node_to_dict_path_like_domain_not_selectable():
    n = WorkflowNode(id="domain:/a/b", kind=NodeKind.DOMAIN, domain_id="domain:/a/b")
    assert ser._node_to_dict(n)["selectableDomain"] is False
    n2 = WorkflowNode(id="domain:x(1)", kind=NodeKind.DOMAIN, domain_id="domain:x(1)")
    assert ser._node_to_dict(n2)["selectableDomain"] is False


def test_node_to_dict_global_and_camel_aliases():
    n = WorkflowNode(
        id="domain:__global__",
        kind=NodeKind.DOMAIN,
        domain_id="domain:__global__",
    )
    d = ser._node_to_dict(n)
    assert d["domain"] == "global" and d["isGlobal"] is True
    assert d["selectableDomain"] is False

    m = WorkflowNode(
        id="memory:1",
        kind=NodeKind.MEMORY,
        domain_id="",
        session_id="s1",
        subagent_type="x",
        created_at="now",
        heat_base=0.5,
    )
    md = ser._node_to_dict(m)
    assert md["domain"] == "global"
    assert "selectableDomain" not in md
    assert md["sessionId"] == "s1"
    assert md["subagentType"] == "x"
    assert md["createdAt"] == "now"
    assert md["heatBase"] == 0.5


def test_edge_to_dict_adds_type():
    e = WorkflowEdge(source="a", target="b", kind=EdgeKind.IN_DOMAIN)
    d = ser._edge_to_dict(e)
    assert d["type"] == "in_domain"
    assert d["source"] == "a" and d["weight"] == 1.0


# ── get_methodology_graph ──────────────────────────────────────────────


def _patch_graph(monkeypatch, graph):
    seen = {}
    monkeypatch.setattr(get_methodology_graph, "load_profiles", lambda: {"p": 1})

    def _build(profiles, domain):
        seen["args"] = (profiles, domain)
        return graph

    monkeypatch.setattr(get_methodology_graph, "build_graph", _build)
    return seen


def test_methodology_graph_passthrough(monkeypatch):
    seen = _patch_graph(monkeypatch, {"nodes": [{"id": 1}], "edges": []})
    out = asyncio.run(get_methodology_graph.handler({"domain": "cortex"}))
    assert out == {"nodes": [{"id": 1}], "edges": []}
    assert seen["args"] == ({"p": 1}, "cortex")


def test_methodology_graph_caps_nodes_and_edges(monkeypatch):
    nodes = [{"id": i, "quality": i} for i in range(250)]
    edges = [{"s": i, "weight": i} for i in range(600)]
    _patch_graph(monkeypatch, {"nodes": nodes, "edges": edges})
    out = asyncio.run(get_methodology_graph.handler(None))
    assert len(out["nodes"]) == 200
    assert out["truncated_nodes"] == 50
    assert out["nodes"][0]["quality"] == 249
    assert len(out["edges"]) == 500
    assert out["truncated_edges"] == 100
    assert out["edges"][0]["weight"] == 599
