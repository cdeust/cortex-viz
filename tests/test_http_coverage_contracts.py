"""Behavioral contracts for standalone HTTP security and response helpers."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cortex_viz.server import (
    graph_build,
    graph_event_stream,
    http_dashboard_data,
    http_security,
    http_standalone_state,
)
from cortex_viz.server import http_standalone_memories as memories
from cortex_viz.server import http_standalone_skills as skills
from cortex_viz.server import http_standalone_sse as sse


class FakeHandler:
    def __init__(self, *, headers=None, path="/"):
        self.headers = headers or {}
        self.path = path
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


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1:8080", True),
        ("localhost", True),
        ("[::1]:9000", True),
        ("::1", False),
        ("example.com", False),
        ("127.0.0.1.example.com", False),
        ("", False),
    ],
)
def test_validate_host_header(host, allowed):
    assert (
        http_security.validate_host_header(FakeHandler(headers={"Host": host}))
        is allowed
    )


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("https://localhost", "https://localhost"),
        ("http://[::1]:9000", "http://[::1]:9000"),
        ("ftp://localhost", None),
        ("https://example.com", None),
        ("http://localhost\r\nInjected: yes", None),
        ("", None),
    ],
)
def test_resolve_allowed_origin(origin, expected):
    handler = FakeHandler(headers={"Origin": origin})
    assert http_security.resolve_allowed_origin(handler) == expected


def test_same_origin_write_accepts_origin_or_loopback_referer():
    assert http_security.enforce_same_origin_write(
        FakeHandler(headers={"Origin": "https://localhost:444"})
    )
    assert http_security.enforce_same_origin_write(
        FakeHandler(headers={"Referer": "http://127.0.0.1:8000/page"})
    )
    assert not http_security.enforce_same_origin_write(FakeHandler())
    assert not http_security.enforce_same_origin_write(
        FakeHandler(headers={"Referer": "https://attacker.example/page"})
    )


def test_apply_cors_headers_reflects_only_a_safe_origin():
    allowed = FakeHandler(headers={"Origin": "http://localhost:9000"})
    http_security._apply_cors_headers(allowed)
    assert allowed.response_headers == [
        ("Access-Control-Allow-Origin", "http://localhost:9000"),
        ("Vary", "Origin"),
    ]

    denied = FakeHandler(headers={"Origin": "https://example.com"})
    http_security._apply_cors_headers(denied)
    assert denied.response_headers == [("Vary", "Origin")]


def test_memory_query_and_success_endpoints(monkeypatch):
    handler = FakeHandler(path="/api/memories?tag=one&tag=two&empty=")
    assert memories._query_params(handler) == {"tag": "one"}
    calls = []
    monkeypatch.setattr(
        memories.memory_browse,
        "list_memories_page",
        lambda store, params: {"store": store, "params": params},
    )
    monkeypatch.setattr(
        memories.memory_browse, "memory_facets", lambda store: {"store": store}
    )
    monkeypatch.setattr(
        memories, "send_json_ok", lambda h, payload: calls.append((h, payload))
    )
    memories.serve_memories(handler, "db")
    memories.serve_memory_facets(handler, "db")
    assert calls == [
        (handler, {"store": "db", "params": {"tag": "one"}}),
        (handler, {"store": "db"}),
    ]


def test_memory_endpoints_serialize_failures(monkeypatch):
    errors = []
    monkeypatch.setattr(
        memories.memory_browse,
        "list_memories_page",
        lambda *_: (_ for _ in ()).throw(RuntimeError("query failed")),
    )
    monkeypatch.setattr(
        memories.memory_browse,
        "memory_facets",
        lambda *_: (_ for _ in ()).throw(RuntimeError("facets failed")),
    )
    monkeypatch.setattr(
        memories, "send_json_error", lambda _h, exc: errors.append(str(exc))
    )
    memories.serve_memories(FakeHandler(), object())
    memories.serve_memory_facets(FakeHandler(), object())
    assert errors == ["query failed", "facets failed"]


def test_skill_format_and_endpoint(monkeypatch):
    row = {
        "skill_id": "s1",
        "action_sequence": " Read > > Bash:repo ",
        "context_signature": None,
        "occurrences": None,
        "success_count": 3,
        "failure_count": None,
        "proficiency": 0.87654,
        "is_habitual": 1,
        "last_seen": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    formatted = skills._format_skill(row)
    assert formatted["sequence"] == ["Read", "Bash:repo"]
    assert formatted["sequence_text"] == "Read → Bash:repo"
    assert formatted["length"] == 2
    assert formatted["proficiency"] == 0.8765
    assert formatted["last_seen"] == "2026-01-01T00:00:00+00:00"

    store = SimpleNamespace(
        list_procedural_skills=lambda **kwargs: [
            row,
            {"skill_id": "s2", "last_seen": "today"},
        ]
    )
    sent = []
    monkeypatch.setattr(
        skills, "send_json_ok", lambda h, payload: sent.append((h, payload))
    )
    handler = FakeHandler(path="/api/skills?min_proficiency=0.5&limit=7")
    skills.serve_skills(handler, store)
    assert sent[0][1]["count"] == 2
    assert sent[0][1]["habitual_count"] == 1


def test_skill_endpoint_reports_invalid_parameters(monkeypatch):
    errors = []
    monkeypatch.setattr(
        skills, "send_json_error", lambda _h, exc: errors.append(type(exc).__name__)
    )
    skills.serve_skills(FakeHandler(path="/api/skills?limit=bad"), object())
    assert errors == ["ValueError"]


@pytest.mark.parametrize(
    ("headers", "path", "expected"),
    [
        ({}, "/api/graph/events", 0),
        ({"Last-Event-ID": "4"}, "/api/graph/events", 5),
        ({"Last-Event-Id": "bad"}, "/api/graph/events?since=3", 3),
        ({"Last-Event-ID": "8"}, "/api/graph/events?since=3", 9),
        ({"Last-Event-ID": "1"}, "/api/graph/events?since=bad", 2),
    ],
)
def test_resolve_sse_since(headers, path, expected):
    assert sse._resolve_sse_since(FakeHandler(headers=headers, path=path)) == expected


class FakeStream:
    def __init__(self, events=(), stats=()):
        self.events = list(events)
        self.stats_values = list(stats)
        self.subscriptions = []

    def subscribe(self, *, since, timeout):
        self.subscriptions.append((since, timeout))
        yield from self.events
        self.events = []

    def stats(self):
        return self.stats_values.pop(0)


def test_write_sse_batch_events_and_disconnect():
    handler = FakeHandler()
    stream = FakeStream(events=[(2, {"label": "x", "nodes": [], "edges": []})])
    assert sse._write_sse_batch_events(handler, stream, 2) == (3, True)
    assert b"id: 2" in handler.wfile.getvalue()
    assert stream.subscriptions == [(2, 15.0)]

    class BrokenWriter:
        def write(self, _data):
            raise BrokenPipeError

        def flush(self):
            raise AssertionError("flush must not run")

    broken = FakeHandler()
    broken.wfile = BrokenWriter()
    assert sse._write_sse_batch_events(broken, FakeStream(events=[(4, {})]), 4) == (
        4,
        False,
    )


def test_stream_sse_batches_writes_heartbeat_then_done(monkeypatch):
    handler = FakeHandler()
    stream = FakeStream(
        stats=[{"closed": False, "count": 0}, {"closed": True, "count": 0}]
    )
    done = []
    monkeypatch.setattr(sse, "_write_sse_done", lambda h: done.append(h))
    sse._stream_sse_batches(handler, stream, 0)
    assert handler.wfile.getvalue() == b": heartbeat\n\n"
    assert done == [handler]


def test_sse_headers_error_and_done_paths(monkeypatch):
    handler = FakeHandler()
    sse._send_sse_headers(handler)
    assert handler.responses == [200]
    assert (
        "Content-Type",
        "text/event-stream; charset=utf-8",
    ) in handler.response_headers
    assert handler.ended == 1

    sse._write_sse_error(handler, ValueError("bad"))
    assert b"event: error\ndata: ValueError: bad" in handler.wfile.getvalue()

    monkeypatch.setattr(
        "cortex_viz.server.graph_appliers.get_build_progress",
        lambda: {"node_count": 2, "edge_count": 3},
    )
    done_handler = FakeHandler()
    sse._write_sse_done(done_handler)
    assert b'"total_nodes":2' in done_handler.wfile.getvalue()


def test_serve_graph_events_balances_stream_lifecycle(monkeypatch):
    events = []
    fake_stream = object()
    monkeypatch.setattr(
        graph_build,
        "ensure_build_started",
        lambda store: events.append(("build", store)),
    )
    monkeypatch.setattr(graph_event_stream, "get_stream", lambda: fake_stream)
    monkeypatch.setattr(
        http_standalone_state, "stream_opened", lambda: events.append("open")
    )
    monkeypatch.setattr(
        http_standalone_state, "stream_closed", lambda: events.append("closed")
    )
    monkeypatch.setattr(sse, "_send_sse_headers", lambda _h: events.append("headers"))
    monkeypatch.setattr(
        sse,
        "_stream_sse_batches",
        lambda _h, stream, since: events.append((stream, since)),
    )
    handler = FakeHandler(headers={"Last-Event-ID": "2"})
    sse.serve_graph_events(handler, "store")
    assert events == ["open", ("build", "store"), "headers", (fake_stream, 3), "closed"]


def test_serve_graph_events_reports_errors_and_still_closes(monkeypatch):
    events = []
    monkeypatch.setattr(
        graph_build,
        "ensure_build_started",
        lambda _store: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        http_standalone_state, "stream_opened", lambda: events.append("open")
    )
    monkeypatch.setattr(
        http_standalone_state, "stream_closed", lambda: events.append("closed")
    )
    monkeypatch.setattr(
        sse, "_write_sse_error", lambda _h, exc: events.append(str(exc))
    )
    sse.serve_graph_events(FakeHandler(), object())
    assert events == ["open", "boom", "closed"]


class DashboardStore:
    def count_memories(self):
        return {"total": 3, "episodic": 2, "active": 1}

    def get_hot_memories(self, **_kwargs):
        return [{"id": "m1", "content": "hot", "heat": 0.123456, "tags": '["one"]'}]

    def get_all_entities(self):
        return [
            {"id": "e1", "heat": 1},
            {"id": "e2", "heat": 2},
            {"id": "e3", "heat": 0},
        ]

    def get_all_relationships(self):
        return [
            {"source_entity_id": "e1", "target_entity_id": "e2", "weight": 0.77777},
            {"source_entity_id": "e1", "target_entity_id": "outside"},
        ]

    def get_recent_memories(self, **_kwargs):
        return [{"id": "m2", "content": "recent"}]

    def get_domain_counts(self):
        return {"code": 2}

    def get_all_engram_slots(self):
        return [{"slot_index": 1, "excitability": 0.55555}, {"slot_index": 2}]

    def get_slot_occupancy(self):
        return {1: 2, 2: 0}

    def get_stage_counts(self):
        return {"labile": 3}

    def count_schemas(self):
        return 1

    def get_all_schemas(self):
        return [{"schema_id": "s1", "consistency_threshold": 0.12345}]

    def get_avg_heat(self):
        return 0.98765

    def count_entities(self):
        return 3

    def count_relationships(self):
        return 2

    def count_active_triggers(self):
        return 1

    def get_last_consolidation(self):
        return "today"


def test_dashboard_build_and_formatters():
    data = http_dashboard_data.build_dashboard_data(DashboardStore())
    assert data["stats"]["total"] == 3
    assert data["stats"]["avg_heat"] == 0.9877
    assert data["stats"]["procedural_skills"] == 0
    assert data["hot_memories"][0]["tags"] == ["one"]
    assert [entity["id"] for entity in data["entities"]] == ["e2", "e1", "e3"]
    assert data["relationships"] == [
        {
            "source": "e1",
            "target": "e2",
            "type": "related",
            "weight": 0.7778,
            "is_causal": False,
            "release_probability": 0.5,
            "facilitation": 0,
            "depression": 0,
            "confidence": 1.0,
            "last_reinforced": "",
        }
    ]
    assert data["engram_slots"][0]["occupancy"] == 2
    assert data["schemas"][0]["id"] == "s1"
    assert data["schema_count"] == 1
    assert data["timestamp"].endswith("+00:00")


def test_dashboard_optional_methods_and_small_formatters():
    class Broken:
        def explode(self):
            raise RuntimeError

    broken = Broken()
    assert http_dashboard_data._safe_call(broken, "missing", 1) == 1
    assert http_dashboard_data._safe_call(broken, "explode", 2) == 2
    assert http_dashboard_data.parse_tags(["a"]) == ["a"]
    assert http_dashboard_data.parse_tags("not-json") == []
    assert http_dashboard_data.parse_tags(None) == []
    assert http_dashboard_data.format_entity({"id": "e"})["type"] == "unknown"
    assert http_dashboard_data.format_relationship(
        {"source_entity_id": "a", "target_entity_id": "b", "is_causal": 1}
    )["is_causal"]
    assert http_dashboard_data.format_schema({})["consistency_threshold"] == 0.5
    assert http_dashboard_data.build_engram_data([{"slot_index": 1}], {1: 0}) == []
