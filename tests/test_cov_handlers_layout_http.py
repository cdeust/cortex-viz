"""Behaviour tests for quadtree_handler, tile_handler and recompute_layout.

The PG-backed stores are the external boundary and are faked; Arrow, gzip,
datashader/PNG rendering and igraph layout run for real.
"""

from __future__ import annotations

import gzip
import io
import json

import pytest

pa = pytest.importorskip("pyarrow")
pytest.importorskip("datashader")
pytest.importorskip("igraph")

import pyarrow.ipc as ipc  # noqa: E402

from cortex_viz.handlers import (  # noqa: E402
    quadtree_handler,
    recompute_layout,
    tile_handler,
)
from cortex_viz.infrastructure import layout_pg_store, lod_pg_store  # noqa: E402
from cortex_viz.server import graph_cache_state  # noqa: E402


class _Handler:
    def __init__(self, path="/"):
        self.path = path
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass


def _dechunk(raw: bytes) -> bytes:
    out, i = b"", 0
    while True:
        j = raw.index(b"\r\n", i)
        n = int(raw[i:j], 16)
        if n == 0:
            return out
        out += raw[j + 2 : j + 2 + n]
        i = j + 2 + n + 2


# ── quadtree ───────────────────────────────────────────────────────────


def test_chunk_writer_drops_empty_and_frames_nonempty():
    buf = io.BytesIO()
    w = quadtree_handler._ChunkWriter(buf)
    assert w.writable() is True
    assert w.write(b"") == 0
    assert buf.getvalue() == b""
    assert w.write(b"hey") == 3
    assert buf.getvalue() == b"3\r\nhey\r\n"


def test_quadtree_streams_gzipped_arrow_across_chunks(monkeypatch):
    chunks = [
        [("a", 0.1, 0.2, "memory"), ("b", 0.3, 0.4, "file")],
        [("c", -0.5, 0.5, "memory")],
    ]
    monkeypatch.setattr(
        layout_pg_store, "iter_positions_chunked", lambda store: iter(chunks)
    )
    h = _Handler()
    quadtree_handler.serve(h, object())
    assert h.status == 200
    assert h.headers["Content-Encoding"] == "gzip"
    assert h.headers["Transfer-Encoding"] == "chunked"
    assert h.wfile.getvalue().endswith(b"0\r\n\r\n")
    table = ipc.open_stream(gzip.decompress(_dechunk(h.wfile.getvalue()))).read_all()
    assert table.num_rows == 3
    assert table.column("id").to_pylist() == ["a", "b", "c"]
    assert table.column("kind").to_pylist() == ["memory", "file", "memory"]
    assert table.column("x").to_pylist() == pytest.approx([0.1, 0.3, -0.5])


def test_quadtree_empty_layout_is_warming_202(monkeypatch):
    monkeypatch.setattr(layout_pg_store, "iter_positions_chunked", lambda s: iter([]))
    h = _Handler()
    quadtree_handler.serve(h, object())
    assert h.status == 202
    assert json.loads(h.wfile.getvalue())["reason"] == "no_layout"


# ── tile ───────────────────────────────────────────────────────────────


def test_tile_parse():
    assert tile_handler._parse("/api/tile/3/4/5.png") == (3, 4, 5)
    assert tile_handler._parse("/api/tile/x/4/5.png") is None


def test_tile_bad_path_404():
    h = _Handler("/nope")
    tile_handler.serve(h, object())
    assert h.status == 404


def test_tile_renders_png_from_raw_rows(monkeypatch):
    calls = {}

    def _read(store, **kw):
        calls.update(kw)
        return [("a", 0.0, 0.0, "memory"), ("b", 0.5, 0.5, "file")]

    monkeypatch.setattr(layout_pg_store, "read_positions_in_bbox", _read)
    h = _Handler("/api/tile/0/0/0.png?x=1")
    tile_handler.serve(h, object())
    assert h.status == 200
    assert h.headers["Content-Type"] == "image/png"
    png = h.wfile.getvalue()
    assert png.startswith(b"\x89PNG")
    assert h.headers["Content-Length"] == str(len(png))
    assert calls["limit"] == tile_handler._RAW_RENDER_MAX + 1
    assert calls["min_x"] == -1.0 and calls["max_x"] == 1.0


def test_tile_render_import_error_reports_unavailable(monkeypatch):
    from cortex_viz.core import tile_renderer

    monkeypatch.setattr(
        layout_pg_store, "read_positions_in_bbox", lambda store, **kw: []
    )

    def _boom(*a, **kw):
        raise ImportError("no datashader")

    monkeypatch.setattr(tile_renderer, "render_tile_png", _boom)
    h = _Handler("/api/tile/1/0/0.png")
    tile_handler.serve(h, object())
    body = json.loads(h.wfile.getvalue())
    assert body["status"] == "unavailable" and body["capability"] == "viz-tile"


def _overdense(monkeypatch, n=3):
    monkeypatch.setattr(tile_handler, "_RAW_RENDER_MAX", n)
    raw = [(f"n{i}", 0.0, 0.0, "memory") for i in range(n + 1)]
    monkeypatch.setattr(layout_pg_store, "read_positions_in_bbox", lambda s, **kw: raw)
    return raw


def test_read_rows_under_budget_returns_raw(monkeypatch):
    rows = [("a", 0.0, 0.0, "k")]
    monkeypatch.setattr(layout_pg_store, "read_positions_in_bbox", lambda s, **kw: rows)
    out = tile_handler._read_rows(
        None, layout_pg_store, lod_pg_store, z=0, bbox=(-1, -1, 1, 1)
    )
    assert out is rows


def test_read_rows_overdense_uses_lod_band(monkeypatch):
    _overdense(monkeypatch)
    monkeypatch.setattr(
        layout_pg_store, "read_layout_version", lambda s: {"fingerprint": "fp"}
    )
    seen = {}

    def _lod(store, **kw):
        seen.update(kw)
        return [("rep", 0.0, 0.0, "memory")]

    monkeypatch.setattr(lod_pg_store, "read_lod_in_bbox", _lod)
    out = tile_handler._read_rows(
        None, layout_pg_store, lod_pg_store, z=2, bbox=(-1, -1, 1, 1)
    )
    assert out == [("rep", 0.0, 0.0, "memory")]
    assert seen["fingerprint"] == "fp" and seen["level"] == 5


@pytest.mark.parametrize(
    "version,lod_rows,z",
    [
        (None, [], 0),  # no layout version
        ({"fingerprint": "fp"}, [], 0),  # pyramid empty
        ({"fingerprint": "fp"}, [("x", 0, 0, "k")], 9),  # level out of range
    ],
)
def test_read_rows_overdense_falls_back_to_raw(monkeypatch, version, lod_rows, z):
    raw = _overdense(monkeypatch)
    monkeypatch.setattr(layout_pg_store, "read_layout_version", lambda s: version)
    monkeypatch.setattr(lod_pg_store, "read_lod_in_bbox", lambda s, **kw: lod_rows)
    out = tile_handler._read_rows(
        None, layout_pg_store, lod_pg_store, z=z, bbox=(-1, -1, 1, 1)
    )
    assert out is raw


# ── recompute_layout ───────────────────────────────────────────────────


@pytest.fixture
def cache(monkeypatch):
    def _set(value):
        monkeypatch.setattr(graph_cache_state, "_graph_cache", value)

    return _set


def test_extract_topology_handles_dict_endpoints_and_self_loops():
    data = {
        "nodes": [{"id": "a", "kind": "x"}, {"id": "b"}, {"nokey": 1}],
        "edges": [
            {"source": "a", "target": "b"},
            {"source": {"id": "b"}, "target": {"id": "a"}},
            {"source": "a", "target": "a"},
            {"source": "a"},
        ],
    }
    ids, edges, kinds = recompute_layout._extract_topology(data)
    assert ids == ["a", "b"]
    assert edges == [("a", "b"), ("b", "a")]
    assert kinds == {"a": "x", "b": "unknown"}
    assert recompute_layout._extract_topology({}) == ([], [], {})


def test_run_recompute_no_graph_and_empty(cache):
    cache(None)
    assert recompute_layout.run_recompute(None) == {
        "status": "error",
        "reason": "no_graph_cached",
    }
    cache({"data": {"nodes": [], "edges": []}})
    assert recompute_layout.run_recompute(None)["reason"] == "empty_graph"


_GRAPH = {
    "nodes": [{"id": f"n{i}", "kind": "memory"} for i in range(5)],
    "edges": [{"source": f"n{i}", "target": f"n{i + 1}"} for i in range(4)],
}


def test_run_recompute_computes_and_persists(cache, monkeypatch):
    cache({"data": _GRAPH})
    written = {}
    lod = []
    monkeypatch.setattr(layout_pg_store, "read_layout_version", lambda s: None)

    def _write(store, coords, kinds, topology_fingerprint):
        written.update(coords=coords, kinds=kinds, fp=topology_fingerprint)
        return 1234

    monkeypatch.setattr(layout_pg_store, "write_layout", _write)
    monkeypatch.setattr(lod_pg_store, "build_lod", lambda store, **kw: lod.append(kw))
    out = recompute_layout.run_recompute(object())
    assert out["status"] == "ok" and out["cached"] is False
    assert out["node_count"] == 5 and out["edge_count"] == 4
    assert out["layout_version"] == 1234
    assert len(written["coords"]) == 5
    assert written["fp"] == out["topology_fingerprint"]
    assert lod == [{"fingerprint": written["fp"], "max_level": 7}]


def test_run_recompute_skips_when_fingerprint_fresh(cache, monkeypatch):
    cache({"data": _GRAPH})
    from cortex_viz.core import layout_engine

    fp = layout_engine.topology_fingerprint(
        [n["id"] for n in _GRAPH["nodes"]],
        [(e["source"], e["target"]) for e in _GRAPH["edges"]],
    )
    monkeypatch.setattr(
        layout_pg_store,
        "read_layout_version",
        lambda s: {"fingerprint": fp, "count": 5, "version": 77},
    )

    def _no_write(*a, **kw):
        raise AssertionError("layout must not be recomputed")

    monkeypatch.setattr(layout_pg_store, "write_layout", _no_write)
    lod = []
    monkeypatch.setattr(lod_pg_store, "build_lod", lambda s, **kw: lod.append(kw))
    out = recompute_layout.run_recompute(object())
    assert out["cached"] is True and out["layout_version"] == 77
    assert out["elapsed_ms"] == 0
    assert len(lod) == 1


def test_run_recompute_tolerates_version_read_failure_and_igraph_missing(
    cache, monkeypatch
):
    cache({"data": _GRAPH})

    def _raise(store):
        raise RuntimeError("db down")

    monkeypatch.setattr(layout_pg_store, "read_layout_version", _raise)
    from cortex_viz.core import layout_engine

    def _no_igraph(ids, edges):
        raise ImportError("igraph gone")

    monkeypatch.setattr(layout_engine, "layout", _no_igraph)
    out = recompute_layout.run_recompute(object())
    assert out["reason"] == "igraph_missing" and "igraph gone" in out["detail"]


def test_build_lod_safe_swallows_errors(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(lod_pg_store, "build_lod", _boom)
    recompute_layout._build_lod_safe(object(), "fp")


def test_serve_status_codes(cache):
    cache(None)
    h = _Handler()
    recompute_layout.serve(h, object())
    assert h.status == 503
    assert json.loads(h.wfile.getvalue())["reason"] == "no_graph_cached"
    assert h.headers["Content-Length"] == str(len(h.wfile.getvalue()))


def test_serve_ok_returns_200(monkeypatch):
    monkeypatch.setattr(
        recompute_layout, "run_recompute", lambda store: {"status": "ok"}
    )
    h = _Handler()
    recompute_layout.serve(h, object())
    assert h.status == 200
