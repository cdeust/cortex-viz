"""Dispatch and response contracts for standalone HTTP routes and endpoints."""

from __future__ import annotations

import io
import json
import time

from cortex_viz.handlers import quadtree_handler, recompute_layout, tile_handler
from cortex_viz.infrastructure import layout_pg_store, prd_bridge, snapshot_pg_store
from cortex_viz.server import (
    graph_appliers,
    graph_build,
    graph_coverage,
    http_dashboard_data,
    http_standalone_activity,
    http_standalone_fullstream,
    http_standalone_memories,
    http_standalone_skills,
    http_standalone_sse,
    http_standalone_static,
    http_standalone_trace,
    http_standalone_wiki,
)
from cortex_viz.server import (
    http_standalone_endpoints as endpoints,
)
from cortex_viz.server import (
    http_standalone_routes as routes,
)


class _Handler:
    def __init__(self, path="/"):
        self.path = path
        self.headers = {}
        self.wfile = io.BytesIO()
        self.responses = []
        self.response_headers = []
        self.ended = 0

    def send_response(self, status):
        self.responses.append(status)

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        self.ended += 1


def test_feature_moved_and_no_db_routes_are_explicit(tmp_path, monkeypatch):
    moved = _Handler()
    routes._feature_moved(moved, "wiki", "cortex:wiki")
    assert moved.responses == [410]
    assert json.loads(moved.wfile.getvalue())["feature"] == "wiki"

    calls = []
    monkeypatch.setattr(
        routes, "serve_capabilities", lambda *args: calls.append(("caps", args))
    )
    monkeypatch.setattr(routes, "requires_store", lambda path: path == "/api/graph")
    monkeypatch.setattr(
        routes,
        "serve_db_unavailable",
        lambda *args: calls.append(("unavailable", args)),
    )
    html = tmp_path / "index.html"
    html.write_text("page", encoding="utf-8")
    routes._route_unified_get(
        _Handler("/api/capabilities"), None, tmp_path, tmp_path, html
    )
    routes._route_unified_get(_Handler("/api/graph"), None, tmp_path, tmp_path, html)
    assert [call[0] for call in calls] == ["caps", "unavailable"]


def test_api_dispatch_table_routes_every_endpoint(tmp_path, monkeypatch):
    calls = []

    def recorder(name):
        def record(*_args):
            calls.append(name)

        return record

    direct = {
        "serve_capabilities": "capabilities",
        "serve_discussions": "discussions",
        "serve_discussion_detail": "discussion_detail",
        "serve_stats": "stats",
        "serve_sankey": "sankey",
        "serve_file_diff": "file_diff",
    }
    for attr, name in direct.items():
        monkeypatch.setattr(routes, attr, recorder(name))
    monkeypatch.setattr(routes, "requires_store", lambda _path: False)

    module_functions = [
        (endpoints, "serve_dashboard", "dashboard"),
        (http_standalone_trace, "serve_trace_domains", "trace_domains"),
        (http_standalone_trace, "serve_trace_sessions", "trace_sessions"),
        (http_standalone_trace, "serve_trace_chain", "trace_chain"),
        (http_standalone_trace, "serve_trace_file", "trace_file"),
        (http_standalone_trace, "serve_trace_impact", "trace_impact"),
        (endpoints, "serve_graph_node", "graph_node"),
        (endpoints, "serve_graph_progress", "graph_progress"),
        (graph_coverage, "serve_graph_coverage", "graph_coverage"),
        (http_standalone_sse, "serve_graph_events", "graph_events"),
        (endpoints, "serve_graph_phase", "graph_phase"),
        (endpoints, "serve_graph_slice", "graph_slice"),
        (endpoints, "serve_prd", "prd"),
        (http_standalone_activity, "serve_activity_stream", "activity"),
        (http_standalone_fullstream, "serve_graph_full_stream", "full_stream"),
        (endpoints, "serve_graph_full", "graph_full"),
        (endpoints, "serve_graph", "graph"),
        (http_standalone_memories, "serve_memory_facets", "memory_facets"),
        (http_standalone_memories, "serve_memories", "memories"),
        (http_standalone_skills, "serve_skills", "skills"),
        (http_standalone_wiki, "serve_wiki", "wiki"),
        (recompute_layout, "serve", "recompute"),
        (tile_handler, "serve", "tile"),
        (quadtree_handler, "serve", "quadtree"),
    ]
    for module, attr, name in module_functions:
        monkeypatch.setattr(module, attr, recorder(name))

    cases = [
        ("/api/capabilities", "capabilities"),
        ("/api/dashboard", "dashboard"),
        ("/api/trace/domains", "trace_domains"),
        ("/api/trace/sessions", "trace_sessions"),
        ("/api/trace/chain", "trace_chain"),
        ("/api/trace/file", "trace_file"),
        ("/api/trace/impact", "trace_impact"),
        ("/api/graph/node?id=file%3Aa", "graph_node"),
        ("/api/graph/progress", "graph_progress"),
        ("/api/graph/coverage", "graph_coverage"),
        ("/api/graph/events", "graph_events"),
        ("/api/graph/phase", "graph_phase"),
        ("/api/graph/slice", "graph_slice"),
        ("/api/prd", "prd"),
        ("/api/activity/stream", "activity"),
        ("/api/graph/full/stream", "full_stream"),
        ("/api/graph/full", "graph_full"),
        ("/api/graph", "graph"),
        ("/api/memories/facets", "memory_facets"),
        ("/api/memories", "memories"),
        ("/api/skills", "skills"),
        ("/api/discussions?page=2", "discussions"),
        ("/api/discussion/sid", "discussion_detail"),
        ("/api/wiki/page", "wiki"),
        ("/api/stats", "stats"),
        ("/api/sankey?domain=x", "sankey"),
        ("/api/file-diff?id=x", "file_diff"),
        ("/api/recompute_layout", "recompute"),
        ("/api/tile/1/2/3.png", "tile"),
        ("/api/quadtree", "quadtree"),
    ]
    html = tmp_path / "index.html"
    html.write_text("page", encoding="utf-8")
    for path, expected in cases:
        routes._route_unified_get(_Handler(path), object(), tmp_path, tmp_path, html)
        assert calls[-1] == expected

    moved = _Handler("/api/graph/chain")
    routes._route_unified_get(moved, object(), tmp_path, tmp_path, html)
    assert moved.responses == [410]


def test_static_routes_and_html_cache_busting(tmp_path, monkeypatch):
    static_calls = []
    monkeypatch.setattr(routes, "requires_store", lambda _path: False)
    monkeypatch.setattr(
        routes,
        "serve_static",
        lambda _handler, base, name, kind: static_calls.append((base, name, kind)),
    )
    monkeypatch.setattr(
        http_standalone_static,
        "serve_shared_asset",
        lambda _handler, base, name: static_calls.append((base, name, "shared")),
    )
    ui = tmp_path / "ui"
    js = ui / "js"
    css = ui / "css"
    vendor = ui / "vendor"
    html = ui / "index.html"
    ui.mkdir()
    html.write_text(
        '<script src="/js/app.js?v=old"></script>'
        '<link href="/css/app.css" rel="stylesheet">',
        encoding="utf-8",
    )
    cases = [
        ("/atom", ui, "atom-viz.html", "text/html"),
        ("/brain", ui, "brain-viz.html", "text/html"),
        ("/brain/js/app.js", ui / "brain/js", "app.js", "application/javascript"),
        (
            "/brain/models/brain.glb",
            ui / "brain/models",
            "brain.glb",
            "model/gltf-binary",
        ),
        (
            "/dashboard/js/app.js",
            ui / "dashboard/js",
            "app.js",
            "application/javascript",
        ),
        ("/dashboard/app.css", ui / "dashboard", "app.css", "text/css"),
        ("/shared/tokens/base.css", ui / "shared", "tokens/base.css", "shared"),
        ("/js/app.js", js, "app.js", "application/javascript"),
        ("/css/app.css", css, "app.css", "text/css"),
        ("/vendor/deck.js", vendor, "deck.js", "application/javascript"),
    ]
    for path, *_expected in cases:
        routes._route_unified_get(_Handler(path), object(), js, css, html, vendor)
    assert static_calls == [case[1:] for case in cases]

    monkeypatch.setattr(time, "time", lambda: 1234)
    handler = _Handler("/")
    routes._route_unified_get(handler, object(), js, css, html, vendor)
    body = handler.wfile.getvalue().decode()
    assert '/js/app.js?v=1234"' in body
    assert '/css/app.css?v=1234"' in body
    assert handler.responses == [200]
    assert ("Cache-Control", "no-store, must-revalidate") in handler.response_headers


def _capture_endpoint_responses(monkeypatch):
    sent = []
    errors = []
    warming = []
    monkeypatch.setattr(
        endpoints, "send_json_ok", lambda _h, payload: sent.append(payload)
    )
    monkeypatch.setattr(
        endpoints, "send_json_error", lambda _h, exc: errors.append(str(exc))
    )
    monkeypatch.setattr(
        endpoints,
        "send_json_warming",
        lambda _h, reason, progress: warming.append((reason, progress)),
    )
    return sent, errors, warming


def test_basic_endpoints_report_success_and_errors(monkeypatch):
    sent, errors, _warming = _capture_endpoint_responses(monkeypatch)
    monkeypatch.setattr(
        endpoints, "get_graph_response", lambda store, path: [store, path]
    )
    endpoints.serve_graph(_Handler("/api/graph"), "db")
    response = sent.pop()
    assert response == ["db", "/api/graph"]
    monkeypatch.setattr(
        endpoints,
        "get_graph_response",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("graph")),
    )
    endpoints.serve_graph(_Handler(), "db")

    monkeypatch.setattr(
        http_dashboard_data, "build_dashboard_data", lambda store: {"db": store}
    )
    endpoints.serve_dashboard(_Handler(), "db")
    response = sent.pop()
    assert response == {"db": "db"}
    monkeypatch.setattr(
        http_dashboard_data,
        "build_dashboard_data",
        lambda _store: (_ for _ in ()).throw(RuntimeError("dashboard")),
    )
    endpoints.serve_dashboard(_Handler(), "db")

    monkeypatch.setattr(
        prd_bridge, "read_prd_graph", lambda: {"nodes": [1], "edges": [2]}
    )
    endpoints.serve_prd(_Handler())
    response = sent.pop()
    assert response["available"] is True
    monkeypatch.setattr(
        prd_bridge,
        "read_prd_graph",
        lambda: (_ for _ in ()).throw(RuntimeError("prd")),
    )
    endpoints.serve_prd(_Handler())
    assert errors == ["graph", "dashboard", "prd"]


def test_graph_full_warming_gzip_and_ndjson_paths(monkeypatch):
    sent, _errors, warming = _capture_endpoint_responses(monkeypatch)
    monkeypatch.setattr(
        graph_appliers, "get_build_progress", lambda: {"phase": "build"}
    )
    endpoints._send_no_snapshot_warming(_Handler())
    assert warming == [("no_snapshot", {"phase": "build"})]

    gzip_handler = _Handler()
    endpoints._send_gzip_snapshot(
        gzip_handler,
        {"payload_gzip": b"gzip", "node_count": 3, "edge_count": 2},
    )
    assert gzip_handler.wfile.getvalue() == b"gzip"
    assert ("Content-Encoding", "gzip") in gzip_handler.response_headers

    snapshots = iter(
        [
            None,
            {"format": snapshot_pg_store.FORMAT_NDJSON_V1, "payload_gzip": b"nd"},
            {
                "format": "json.v1",
                "payload_gzip": b"gz",
                "node_count": 1,
                "edge_count": 0,
            },
        ]
    )
    monkeypatch.setattr(
        snapshot_pg_store,
        "read_latest_snapshot",
        lambda *_args, **_kwargs: next(snapshots),
    )
    streamed = []
    monkeypatch.setattr(
        http_standalone_fullstream,
        "serve_full_document_from_ndjson",
        lambda _handler, snap: streamed.append(snap),
    )
    endpoints.serve_graph_full(_Handler(), "db")
    endpoints.serve_graph_full(_Handler(), "db")
    final = _Handler()
    endpoints.serve_graph_full(final, "db")
    assert warming[-1][0] == "no_snapshot"
    assert streamed[0]["format"] == snapshot_pg_store.FORMAT_NDJSON_V1
    assert final.wfile.getvalue() == b"gz"
    assert sent == []


def test_slice_progress_and_phase_endpoints_parse_defaults_and_errors(monkeypatch):
    sent, errors, _warming = _capture_endpoint_responses(monkeypatch)
    monkeypatch.setattr(
        graph_appliers,
        "get_graph_slice",
        lambda offset, limit: {"offset": offset, "limit": limit},
    )
    endpoints.serve_graph_slice(_Handler("/api/graph/slice?offset=bad&limit=7"))
    response = sent.pop()
    assert response == {"offset": 0, "limit": 7}

    monkeypatch.setattr(
        graph_build, "ensure_build_started", lambda store: sent.append(store)
    )
    monkeypatch.setattr(graph_appliers, "get_build_progress", lambda: {"ready": True})
    endpoints.serve_graph_progress(_Handler(), "db")
    assert sent == ["db", {"ready": True}]
    sent.clear()

    assert endpoints._apply_phase_param("name=L6%3ACortex", "", 0, None) == (
        "L6:Cortex",
        0,
        None,
    )
    assert endpoints._apply_phase_param("offset=bad", "x", 2, 3) == ("x", 2, 3)
    assert endpoints._apply_phase_param("limit=bad", "x", 2, 3) == ("x", 2, 3)
    assert endpoints._parse_phase_query_params(_Handler("/api/graph/phase")) == (
        "",
        0,
        None,
    )
    monkeypatch.setattr(
        graph_appliers,
        "get_phase_payload",
        lambda name, **kwargs: {"name": name, **kwargs},
    )
    endpoints.serve_graph_phase(
        _Handler("/api/graph/phase?name=L6%3ACortex&offset=5&limit=10")
    )
    response = sent.pop()
    assert response == {"name": "L6:Cortex", "offset": 5, "limit": 10}

    monkeypatch.setattr(
        graph_appliers,
        "get_graph_slice",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("slice")),
    )
    endpoints.serve_graph_slice(_Handler())
    monkeypatch.setattr(
        graph_build,
        "ensure_build_started",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("progress")),
    )
    endpoints.serve_graph_progress(_Handler(), "db")
    monkeypatch.setattr(
        graph_appliers,
        "get_phase_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("phase")),
    )
    endpoints.serve_graph_phase(_Handler())
    assert errors == ["slice", "progress", "phase"]


def test_lod_and_node_resolution_helpers(monkeypatch):
    assert endpoints._lod_cell_bbox(1, 1, 0) == (0.0, -1.0, 1.0, 0.0)
    assert endpoints._resolve_lod_cell("db", "lod:bad")["error"] == "bad_lod_id"
    assert endpoints._resolve_lod_cell("db", "lod:x:1:2")["error"] == "bad_lod_id"
    monkeypatch.setattr(
        layout_pg_store,
        "read_positions_in_bbox",
        lambda store, **kwargs: [("node", 0.1, 0.2, "file")],
    )
    resolved = endpoints._resolve_lod_cell("db", "lod:2:1:2")
    assert resolved["found"] and resolved["member_count"] == 1

    assert endpoints._parse_node_id_param(_Handler("/api/graph/node")) == ""
    assert (
        endpoints._parse_node_id_param(
            _Handler("/api/graph/node?id=file%3Aone&id=file%3Atwo")
        )
        == "file:two"
    )

    class Store:
        def get_memory(self, memory_id):
            return {"memory": memory_id}

        def get_entity_by_id(self, entity_id):
            return {"entity": entity_id}

    monkeypatch.setattr(
        graph_appliers, "get_node_record", lambda node_id: {"cache": node_id}
    )
    assert endpoints._resolve_node_record(Store(), "memory", "2", "memory:2") == {
        "memory": 2
    }
    assert endpoints._resolve_node_record(Store(), "entity", "3", "entity:3") == {
        "entity": 3
    }
    assert endpoints._resolve_node_record(Store(), "file", "x", "file:x") == {
        "cache": "file:x"
    }
    monkeypatch.setattr(
        graph_appliers,
        "get_node_neighbors",
        lambda node_id, **kwargs: {"id": node_id, **kwargs},
    )
    assert endpoints._fetch_node_neighbors(
        _Handler("/api/graph/node?id=x&n_offset=bad&n_limit=9"), "x"
    ) == {"id": "x", "offset": 0, "limit": 9}


def test_graph_node_and_discussion_endpoints(monkeypatch):
    sent, errors, _warming = _capture_endpoint_responses(monkeypatch)
    endpoints.serve_graph_node(_Handler("/api/graph/node"), None)
    response = sent.pop()
    assert response == {"error": "missing id"}
    endpoints.serve_graph_node(_Handler("/api/graph/node?id=lod%3A1%3A0%3A0"), None)
    response = sent.pop()
    assert response["member_count"] == 0

    monkeypatch.setattr(endpoints, "_resolve_lod_cell", lambda *_args: {"found": True})
    endpoints.serve_graph_node(_Handler("/api/graph/node?id=lod%3A1%3A0%3A0"), "db")
    response = sent.pop()
    assert response == {"found": True}

    monkeypatch.setattr(
        endpoints, "_resolve_node_record", lambda *_args: {"name": "node"}
    )
    monkeypatch.setattr(
        endpoints,
        "_fetch_node_neighbors",
        lambda *_args: {"neighbors": [1], "total": 1, "next_offset": None},
    )
    endpoints.serve_graph_node(_Handler("/api/graph/node?id=file%3Ax"), "db")
    response = sent.pop()
    assert response["record"] == {"name": "node"}

    monkeypatch.setattr(
        endpoints,
        "_resolve_node_record",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("node")),
    )
    endpoints.serve_graph_node(_Handler("/api/graph/node?id=file%3Ax"), "db")

    monkeypatch.setattr(
        endpoints, "build_discussions_response", lambda path: {"path": path}
    )
    monkeypatch.setattr(
        endpoints, "build_discussion_detail", lambda sid: {"session": sid}
    )
    endpoints.serve_discussions(_Handler("/api/discussions?page=2"))
    endpoints.serve_discussion_detail(_Handler(), "/api/discussion/sid")
    assert sent == [
        {"path": "/api/discussions?page=2"},
        {"session": "sid"},
    ]
    monkeypatch.setattr(
        endpoints,
        "build_discussions_response",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("discussions")),
    )
    monkeypatch.setattr(
        endpoints,
        "build_discussion_detail",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("detail")),
    )
    endpoints.serve_discussions(_Handler())
    endpoints.serve_discussion_detail(_Handler(), "/api/discussion/sid")
    assert errors == ["node", "discussions", "detail"]
