"""Fullstream and wiki HTTP endpoint behaviour with a fake handler."""

from __future__ import annotations

import gzip
import io
import json

import pytest

from cortex_viz.infrastructure import snapshot_pg_store, wiki_pg, wiki_read
from cortex_viz.infrastructure.snapshot_pg_store import _encode_ndjson_gzip
from cortex_viz.server import graph_appliers
from cortex_viz.server import http_standalone_fullstream as fs
from cortex_viz.server import http_standalone_wiki as wk


class Handler:
    def __init__(self, path="/", body=b"", headers=None, fail_after=None):
        self.path = path
        self.wfile = io.BytesIO()
        self.rfile = io.BytesIO(body)
        self.headers = dict(headers or {})
        self.status = None
        self.sent = []
        self.fail_after = fail_after
        self._writes = 0
        outer = self

        class W:
            def write(self, data):
                if outer.fail_after is not None and outer._writes >= outer.fail_after:
                    raise BrokenPipeError("gone")
                outer._writes += 1
                outer.wfile.write(data)

        self.out = W()

    # fullstream writes through handler.wfile; swap in the failing writer
    def failing(self):
        self.real = self.wfile
        self.wfile = self.out
        return self

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass

    def body_json(self):
        return json.loads(
            self.wfile.getvalue() if not hasattr(self, "real") else self.real.getvalue()
        )


def _graph(n=5, e=6):
    return {
        "nodes": [
            {"id": f"n{i}", "kind": "memory", "label": f"l{i}"} for i in range(n)
        ],
        "edges": [
            {"source": f"n{i % n}", "target": f"n{(i + 1) % n}", "kind": "k"}
            for i in range(e)
        ],
        "meta": {"schema": "v1"},
    }


def _legacy(g):
    return {
        "payload_gzip": gzip.compress(json.dumps(g, separators=(",", ":")).encode()),
        "node_count": len(g["nodes"]),
        "edge_count": len(g["edges"]),
        "format": "json.v1",
    }


def _ndjson(g):
    return {
        "payload_gzip": _encode_ndjson_gzip(g["nodes"], g["edges"], g["meta"]),
        "node_count": len(g["nodes"]),
        "edge_count": len(g["edges"]),
        "format": "ndjson.v1",
    }


# ── fullstream ──


def test_write_line_reports_disconnect():
    h = Handler(fail_after=0).failing()
    assert fs._write_line(h, b"x") is False
    h2 = Handler()
    assert fs._write_line(h2, b"abc") is True
    assert h2.wfile.getvalue() == b"abc"


def test_ndjson_row_stops_when_client_drops():
    row = _ndjson(_graph())
    h = Handler(fail_after=0).failing()
    assert fs._stream_ndjson_row(h, row["payload_gzip"]) is False


@pytest.mark.parametrize("fail_after", [0, 1, 2, 3])
def test_legacy_row_stops_at_each_write_when_client_drops(fail_after):
    h = Handler(fail_after=fail_after).failing()
    assert fs._stream_legacy_row(h, _legacy(_graph())) is False


def test_legacy_row_flushes_on_size_floor(monkeypatch):
    monkeypatch.setattr(fs, "_MIN_LINE_BYTES", 1)
    g = _graph(4, 4)
    h = Handler()
    assert fs._stream_legacy_row(h, _legacy(g)) is True
    lines = [json.loads(x) for x in h.wfile.getvalue().decode().splitlines()]
    assert lines[0] == {"node_total": 4, "edge_total": 4}
    node_lines = [x for x in lines if "nodes" in x]
    assert [n for x in node_lines for n in x["nodes"]] == g["nodes"]
    assert lines[-1] == {"meta": g["meta"]}


def test_legacy_row_flush_failure_on_floor(monkeypatch):
    monkeypatch.setattr(fs, "_MIN_LINE_BYTES", 1)
    h = Handler(fail_after=1).failing()
    assert fs._stream_legacy_row(h, _legacy(_graph())) is False


def test_iter_ndjson_lines_handles_unterminated_tail():
    gz = gzip.compress(b'{"a":1}\n{"b":2}')
    assert list(fs._iter_ndjson_lines(gz)) == [b'{"a":1}', b'{"b":2}']
    gz = gzip.compress(b'{"a":1}\n')
    assert list(fs._iter_ndjson_lines(gz)) == [b'{"a":1}']


def test_full_document_reassembly_matches_graph():
    g = _graph(6, 9)
    h = Handler()
    fs.serve_full_document_from_ndjson(h, _ndjson(g))
    doc = json.loads(h.wfile.getvalue())
    assert doc["nodes"] == g["nodes"] and doc["edges"] == g["edges"]
    assert doc["meta"] == g["meta"]
    assert h.status == 200
    assert ("X-Graph-Node-Count", "6") in h.sent
    assert ("X-Graph-Edge-Count", "9") in h.sent
    assert ("Connection", "close") in h.sent


def test_full_document_edge_cases():
    # nodes only, no edges
    g = {"nodes": [{"id": "a"}], "edges": [], "meta": {"m": 1}}
    h = Handler()
    fs.serve_full_document_from_ndjson(h, _ndjson(g))
    doc = json.loads(h.wfile.getvalue())
    assert (
        doc["nodes"] == [{"id": "a"}] and doc["edges"] == [] and doc["meta"] == {"m": 1}
    )
    # empty graph
    h = Handler()
    fs.serve_full_document_from_ndjson(
        h, _ndjson({"nodes": [], "edges": [], "meta": {}})
    )
    assert json.loads(h.wfile.getvalue()) == {"nodes": [], "edges": [], "meta": {}}
    # unparsable header frame: meta falls back to {}
    payload = gzip.compress(b'not json\n{"nodes":[{"id":"z"}]}\n{"edges":[]}\n')
    h = Handler()
    fs.serve_full_document_from_ndjson(
        h, {"payload_gzip": payload, "node_count": 1, "edge_count": 0}
    )
    doc = json.loads(h.wfile.getvalue())
    assert doc == {"nodes": [{"id": "z"}], "edges": [], "meta": {}}
    # edges section appearing first (no nodes frame)
    payload = gzip.compress(b'{"edges":[{"source":"a","target":"b"}]}\n')
    h = Handler()
    fs.serve_full_document_from_ndjson(
        h, {"payload_gzip": payload, "node_count": 0, "edge_count": 1}
    )
    assert json.loads(h.wfile.getvalue())["edges"] == [{"source": "a", "target": "b"}]


def test_full_document_stops_when_client_drops():
    h = Handler(fail_after=0).failing()
    fs.serve_full_document_from_ndjson(h, _ndjson(_graph()))
    assert h.real.getvalue() == b""


def _patch_snapshot(monkeypatch, value=None, exc=None):
    def read(store, scope):
        if exc:
            raise exc
        return value

    monkeypatch.setattr(snapshot_pg_store, "read_latest_snapshot", read)


def test_serve_stream_ndjson_snapshot(monkeypatch, capsys):
    g = _graph()
    _patch_snapshot(monkeypatch, _ndjson(g))
    h = Handler()
    fs.serve_graph_full_stream(h, store=object())
    assert h.status == 200
    assert ("Content-Type", "application/x-ndjson") in h.sent
    lines = h.wfile.getvalue().decode().splitlines()
    assert json.loads(lines[-1]) == {"done": True}
    assert "completed" in capsys.readouterr().err


def test_serve_stream_legacy_snapshot_and_dropped_client(monkeypatch, capsys):
    _patch_snapshot(monkeypatch, _legacy(_graph()))
    h = Handler()
    fs.serve_graph_full_stream(h, store=object())
    assert json.loads(h.wfile.getvalue().decode().splitlines()[-1]) == {"done": True}
    h = Handler(fail_after=0).failing()
    fs.serve_graph_full_stream(h, store=object())
    assert "client dropped" in capsys.readouterr().err
    assert b"done" not in h.real.getvalue()


def test_serve_stream_no_snapshot_replies_202_with_progress(monkeypatch):
    _patch_snapshot(monkeypatch, None)
    monkeypatch.setattr(graph_appliers, "get_build_progress", lambda: {"phase": "x"})
    h = Handler()
    fs.serve_graph_full_stream(h, store=object())
    assert h.status == 202
    body = json.loads(h.wfile.getvalue())
    assert body == {
        "status": "warming",
        "reason": "no_snapshot",
        "progress": {"phase": "x"},
    }


def test_serve_stream_no_snapshot_survives_progress_error(monkeypatch):
    _patch_snapshot(monkeypatch, None)

    def boom():
        raise RuntimeError("no progress")

    monkeypatch.setattr(graph_appliers, "get_build_progress", boom)
    h = Handler()
    fs.serve_graph_full_stream(h, store=object())
    assert h.status == 202
    assert "progress" not in json.loads(h.wfile.getvalue())


def test_serve_stream_read_error_sends_json_error(monkeypatch):
    _patch_snapshot(monkeypatch, exc=ValueError("db"))
    h = Handler()
    fs.serve_graph_full_stream(h, store=object())
    assert h.status == 500
    assert json.loads(h.wfile.getvalue()) == {"error": "ValueError"}


# ── wiki ──


def test_params_and_safe_int():
    h = Handler(path="/api/wiki/page?path=a%2Fb.md&empty=&x=1&x=2")
    assert wk._params(h) == {"path": "a/b.md", "x": "1"}
    assert wk._safe_int("5", 1) == 5
    assert wk._safe_int("zz", 1) == 1
    assert wk._safe_int(None, 7) == 7


def test_dispatch_routes_each_endpoint(monkeypatch):
    calls = []

    def rec(name, ret):
        def f(*a):
            calls.append((name, a))
            return {"called": name}

        return f

    monkeypatch.setattr(wiki_read, "list_pages", rec("list", None))
    monkeypatch.setattr(wiki_read, "list_projects", rec("projects", None))
    monkeypatch.setattr(wiki_read, "read_page", rec("page", None))
    monkeypatch.setattr(wiki_read, "list_bibliography", rec("bib", None))
    monkeypatch.setattr(wiki_read, "read_bibliography", rec("bibread", None))
    monkeypatch.setattr(wiki_pg, "page_meta", rec("meta", None))
    monkeypatch.setattr(wiki_pg, "list_memos", rec("memos", None))
    monkeypatch.setattr(wk, "_page_actions", lambda s, p: {"called": "actions", "p": p})
    monkeypatch.setattr(wk, "_doc_graph", lambda s, p: {"called": "graph", "p": p})
    d = wk._dispatch_get
    assert d("S", "/api/wiki/list", {})["called"] == "list"
    assert d("S", "/api/wiki/projects", {})["called"] == "projects"
    assert d("S", "/api/wiki/page", {"path": "a.md"})["called"] == "page"
    assert d("S", "/api/wiki/page_meta", {"path": "a.md"})["called"] == "meta"
    assert (
        d("S", "/api/wiki/memos", {"subject_id": "9", "limit": "x"})["called"]
        == "memos"
    )
    assert d("S", "/api/wiki/actions", {"page_id": "1"})["called"] == "actions"
    assert d("S", "/api/wiki/bibliography", {})["called"] == "bib"
    assert (
        d("S", "/api/wiki/bibliography/read", {"path": "r.bib"})["called"] == "bibread"
    )
    assert d("S", "/api/wiki/graph", {"domain": "x"})["called"] == "graph"
    by = dict(calls)
    assert by["page"] == ("a.md",)
    assert by["memos"] == ("S", "page", "9", 50)
    assert by["meta"] == ("S", "a.md")
    assert by["bibread"] == ("r.bib",)
    unk = d("S", "/api/wiki/concepts", {})
    assert unk == {
        "ok": True,
        "items": [],
        "note": "not_served_by_viz",
        "unavailable": True,
    }


def test_flag_semantics():
    assert wk._flag({"a": "1"}, "a", default=False) is True
    assert wk._flag({"a": "0"}, "a", default=True) is False
    assert wk._flag({"a": "yes"}, "a", default=True) is True
    assert wk._flag({}, "a", default=False) is False


def test_serve_wiki_sends_dispatch_result(monkeypatch):
    monkeypatch.setattr(wiki_read, "list_pages", lambda: {"pages": [1]})
    h = Handler(path="/api/wiki/list")
    wk.serve_wiki(h, store=None, path_no_qs="/api/wiki/list")
    assert h.status == 200
    assert json.loads(h.wfile.getvalue()) == {"pages": [1]}


def test_doc_graph_wires_loaders_and_flags(monkeypatch):
    from cortex_viz.infrastructure import wiki_graph

    seen = {}
    monkeypatch.setattr(wiki_graph, "load_wiki_pages", lambda s: ["P"])
    monkeypatch.setattr(wiki_graph, "load_wiki_links", lambda s: ["L"])
    monkeypatch.setattr(
        wiki_graph,
        "load_wiki_memory_links",
        lambda s: [{"memory_id": 4}, {"memory_id": 5}],
    )

    def load_mem(s, ids):
        seen["ids"] = ids
        return ["M"]

    monkeypatch.setattr(wiki_graph, "load_memories_by_ids", load_mem)
    monkeypatch.setattr(
        wiki_read,
        "list_pages",
        lambda: {"pages": [{"path": "a.md", "tags": ["t"]}, {"path": None}]},
    )
    import cortex_viz.core.wiki_doc_graph as dg

    def fake_build(**kw):
        seen["kw"] = kw
        return {"built": True}

    monkeypatch.setattr(dg, "build_doc_graph", fake_build)
    out = wk._doc_graph(None, {"domain": "d", "cooccur": "1"})
    assert out == {"built": True}
    kw = seen["kw"]
    assert seen["ids"] == [4, 5]
    assert kw["pages"] == ["P"] and kw["links"] == ["L"] and kw["memories"] == ["M"]
    assert kw["tags_by_path"] == {"a.md": ["t"], "": []}
    assert (
        kw["domain"] == "d" and kw["cross_lens"] is True and kw["co_occurrence"] is True
    )
    wk._doc_graph(None, {"xlens": "0"})
    assert seen["kw"]["memories"] == [] and seen["kw"]["cross_lens"] is False


def test_page_actions_end_to_end_with_activity(monkeypatch):
    import cortex_viz.core.wiki_page_actions as wpa
    import cortex_viz.infrastructure.activity_store as act
    import cortex_viz.infrastructure.wiki_page_actions_pg as pg

    monkeypatch.setattr(
        pg, "load_page_by_id", lambda s, i: {"domain": "cortex", "rel_path": "p.md"}
    )
    monkeypatch.setattr(
        pg,
        "load_page_sources",
        lambda s, i: [
            {"source_path": "a.py", "link_kind": "doc"},
            {"source_path": "zz"},
        ],
    )
    monkeypatch.setattr(
        wpa, "resolve_source_target_ids", lambda d, srcs: {"a.py": "file:abc"}
    )
    monkeypatch.setattr(act, "find_by_target_ids", lambda s, ids, limit: [{"id": 1}])
    monkeypatch.setattr(act, "scan_legacy_file_rows", lambda s, limit: [])
    rows = [
        {
            "id": i,
            "session_id": "s",
            "ts": i,
            "tool": "Edit",
            "action": "edit",
            "target_label": "a.py",
            "edge_kind": "k",
            "source_path": "a.py",
            "extra": 1,
        }
        for i in range(3)
    ]
    monkeypatch.setattr(wpa, "match_activity_rows", lambda ids, c, lim: rows)
    got = wk._page_actions(None, {"page_id": "3", "limit": "2"})
    assert got["count"] == 2 and got["truncated"] is True
    assert got["rel_path"] == "p.md"
    assert got["sources"] == [
        {"source_path": "a.py", "link_kind": "doc", "resolved": True},
        {"source_path": "zz", "link_kind": None, "resolved": False},
    ]
    assert "extra" not in got["actions"][0]
    full = wk._page_actions(None, {"page_id": "3", "limit": "bad"})
    assert full["count"] == 3 and full["truncated"] is False


def test_save_page_success_and_failures(monkeypatch):
    saved = []

    def save(rel, body):
        saved.append((rel, body))
        if rel == "bad":
            return {"ok": False, "error": "nope"}
        return {"ok": True, "rel": rel}

    monkeypatch.setattr(wiki_read, "save_page", save)
    payload = json.dumps({"rel_path": "a.md", "content": "hello"}).encode()
    h = Handler(body=payload, headers={"Content-Length": str(len(payload))})
    wk.serve_wiki_save(h)
    assert h.status == 200 and saved[-1] == ("a.md", "hello")
    payload = json.dumps({"path": "b.md", "body": "B"}).encode()
    h = Handler(body=payload, headers={"Content-Length": str(len(payload))})
    wk.serve_wiki_save(h)
    assert saved[-1] == ("b.md", "B")
    payload = json.dumps({"rel_path": "bad", "body": ""}).encode()
    h = Handler(body=payload, headers={"Content-Length": str(len(payload))})
    wk.serve_wiki_save(h)
    assert h.status == 400
    assert json.loads(h.wfile.getvalue()) == {"error": "RuntimeError"}
    h = Handler(body=b"{not json", headers={"Content-Length": "9"})
    wk.serve_wiki_save(h)
    assert h.status == 400
    assert json.loads(h.wfile.getvalue())["error"] == "JSONDecodeError"
    h = Handler(body=b"", headers={})
    wk.serve_wiki_save(h)
    assert saved[-1] == ("", "")
    assert h.status == 200
