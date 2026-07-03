"""Wire tests for /api/graph/full/stream and the ndjson→document transform.

No HTTP, no PG: a fake handler captures ``wfile`` bytes, and snapshot rows
are built with the real writer's encoders so the tests exercise the exact
bytes the two formats store.
"""

from __future__ import annotations

import gzip
import io
import json

from cortex_viz.infrastructure.snapshot_pg_store import _encode_ndjson_gzip
from cortex_viz.server.http_standalone_fullstream import (
    _stream_legacy_row,
    _stream_ndjson_row,
    serve_full_document_from_ndjson,
)


class _FakeHandler:
    def __init__(self) -> None:
        self.wfile = io.BytesIO()
        self.headers_sent: list[tuple[str, str]] = []
        self.status = None

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, k: str, v: str) -> None:
        self.headers_sent.append((k, v))

    def end_headers(self) -> None:
        pass


def _graph(n_nodes: int = 7, n_edges: int = 11) -> dict:
    return {
        "nodes": [
            {"id": f"n{i}", "kind": "memory", "label": f'l"{i}}}' , "heat": i / 10}
            for i in range(n_nodes)
        ],
        "edges": [
            {"source": f"n{i % n_nodes}", "target": f"n{(i + 1) % n_nodes}", "kind": "k"}
            for i in range(n_edges)
        ],
        "meta": {"schema": "workflow_graph.v1"},
    }


def _legacy_row(graph: dict) -> dict:
    payload = gzip.compress(
        json.dumps(graph, separators=(",", ":"), default=str).encode()
    )
    return {
        "payload_gzip": payload,
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "format": "json.v1",
    }


def _ndjson_row(graph: dict) -> dict:
    return {
        "payload_gzip": _encode_ndjson_gzip(
            graph["nodes"], graph["edges"], graph["meta"]
        ),
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "format": "ndjson.v1",
    }


def _parse_frames(raw: bytes) -> dict:
    nodes, edges, meta, header, done = [], [], None, None, False
    for line in raw.decode().splitlines():
        f = json.loads(line)
        if "nodes" in f:
            nodes.extend(f["nodes"])
        elif "edges" in f:
            edges.extend(f["edges"])
        elif "meta" in f and "node_total" not in f:
            meta = f["meta"]
        elif "done" in f:
            done = True
        if "node_total" in f:
            header = f
            meta = f.get("meta", meta)
    return {"nodes": nodes, "edges": edges, "meta": meta,
            "header": header, "done": done}


def test_legacy_row_streams_equivalent_frames():
    graph = _graph()
    h = _FakeHandler()
    assert _stream_legacy_row(h, _legacy_row(graph))
    out = _parse_frames(h.wfile.getvalue())
    assert out["nodes"] == graph["nodes"]
    assert out["edges"] == graph["edges"]
    assert out["meta"] == graph["meta"]
    assert out["header"]["node_total"] == len(graph["nodes"])
    assert out["header"]["edge_total"] == len(graph["edges"])


def test_ndjson_row_forwards_stored_frames():
    graph = _graph()
    h = _FakeHandler()
    assert _stream_ndjson_row(h, _ndjson_row(graph)["payload_gzip"])
    out = _parse_frames(h.wfile.getvalue())
    assert out["nodes"] == graph["nodes"]
    assert out["edges"] == graph["edges"]
    assert out["meta"] == graph["meta"]


def test_ndjson_row_reassembles_legacy_document():
    graph = _graph()
    h = _FakeHandler()
    serve_full_document_from_ndjson(h, _ndjson_row(graph))
    doc = json.loads(h.wfile.getvalue())
    assert doc == graph
    assert h.status == 200


def test_empty_graph_reassembles():
    graph = {"nodes": [], "edges": [], "meta": {"schema": "x"}}
    h = _FakeHandler()
    serve_full_document_from_ndjson(h, _ndjson_row(graph))
    assert json.loads(h.wfile.getvalue()) == graph
