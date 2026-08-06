"""Live Trace, activity-stream, Sankey, and HUD endpoint contracts."""

from __future__ import annotations

import io
import json
import subprocess
from types import SimpleNamespace

from cortex_viz.core import activity_graph, impact_graph, session_trace
from cortex_viz.infrastructure import activity_store, ap_bridge, trace_source
from cortex_viz.server import (
    activity_stream,
    git_diff_engine,
    graph_discussions,
    graph_event_stream,
    http_standalone_state,
)
from cortex_viz.server import (
    http_standalone_activity as activity,
)
from cortex_viz.server import (
    http_standalone_endpoints_sankey as sankey,
)
from cortex_viz.server import (
    http_standalone_trace as trace,
)


class _Handler:
    def __init__(self, path="/", body=b""):
        self.path = path
        self.headers = {"Content-Length": str(len(body))} if body else {}
        self.rfile = io.BytesIO(body)
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


def _capture_trace(monkeypatch):
    sent = []
    errors = []
    monkeypatch.setattr(
        trace, "send_json_ok", lambda _handler, payload: sent.append(payload)
    )
    monkeypatch.setattr(
        trace, "send_json_error", lambda _handler, exc: errors.append(str(exc))
    )
    return sent, errors


def test_trace_domain_session_and_chain_endpoints(monkeypatch):
    sent, errors = _capture_trace(monkeypatch)
    monkeypatch.setattr(trace_source, "list_domains", lambda: [{"id": "domain:x"}])
    trace.serve_trace_domains(_Handler())
    response = sent.pop()
    assert response["meta"]["level"] == 0

    trace.serve_trace_sessions(_Handler("/api/trace/sessions"))
    response = sent.pop()
    assert response["error"] == "missing domain"
    monkeypatch.setattr(
        trace_source,
        "list_sessions",
        lambda domain: {"nodes": [{"domain": domain}], "edges": []},
    )
    trace.serve_trace_sessions(_Handler("/api/trace/sessions?domain=domain%3Ax"))
    response = sent.pop()
    assert response["meta"] == {
        "schema": "trace.v1",
        "level": 1,
        "domain": "domain:x",
    }

    trace.serve_trace_chain(_Handler("/api/trace/chain"))
    response = sent.pop()
    assert response["error"] == "missing session"
    monkeypatch.setattr(trace_source, "iter_session_events", lambda sid: [{"sid": sid}])
    monkeypatch.setattr(
        session_trace,
        "build_chain",
        lambda events, sid, *, since: {"nodes": [sid, since], "edges": events},
    )
    trace.serve_trace_chain(_Handler("/api/trace/chain?session=s1&since=bad"))
    response = sent.pop()
    assert response["meta"]["since"] == 0
    trace.serve_trace_chain(_Handler("/api/trace/chain?session=s1&since=3"))
    response = sent.pop()
    assert response["nodes"] == ["s1", 3]

    monkeypatch.setattr(
        trace_source,
        "list_domains",
        lambda: (_ for _ in ()).throw(RuntimeError("domains")),
    )
    trace.serve_trace_domains(_Handler())
    monkeypatch.setattr(
        trace_source,
        "list_sessions",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("sessions")),
    )
    trace.serve_trace_sessions(_Handler("/?domain=x"))
    monkeypatch.setattr(
        trace_source,
        "iter_session_events",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("chain")),
    )
    trace.serve_trace_chain(_Handler("/?session=x"))
    assert errors == ["domains", "sessions", "chain"]


def test_trace_git_helpers_file_and_impact_endpoints(tmp_path, monkeypatch):
    sent, errors = _capture_trace(monkeypatch)
    monkeypatch.setattr(
        git_diff_engine,
        "repo_root_and_relpath",
        lambda _path: (None, "", "outside"),
    )
    assert trace._file_git_root_rel("~/outside.py")[0] is None
    monkeypatch.setattr(
        git_diff_engine,
        "repo_root_and_relpath",
        lambda _path: ("/repo", "src/a.py", None),
    )
    assert trace._file_git_root_rel("/repo/src/a.py") == ("/repo", "src/a.py")
    monkeypatch.setattr(trace, "diff_for_path", lambda path: {"path": path})
    assert trace._git_history("a.py") == {"path": "a.py"}

    monkeypatch.setattr(trace, "_file_git_root_rel", lambda _path: (None, "a.py"))
    assert trace._git_versions("a.py") == {"available": False}
    monkeypatch.setattr(trace, "_file_git_root_rel", lambda _path: ("/repo", "a.py"))
    results = iter(
        [
            SimpleNamespace(returncode=1, stderr="failed", stdout=""),
            SimpleNamespace(
                returncode=0,
                stderr="",
                stdout="abc\x1f2026-01-01\x1fAda\x1fSubject\x1einvalid\x1e",
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: next(results))
    assert trace._git_versions("a.py")["error"] == "failed"
    versions = trace._git_versions("a.py")
    assert versions["versions"] == [
        {"sha": "abc", "date": "2026-01-01", "author": "Ada", "subject": "Subject"}
    ]

    trace.serve_trace_file(_Handler("/api/trace/file"))
    response = sent.pop()
    assert response["error"] == "missing path"
    monkeypatch.setattr(trace, "_git_history", lambda path: {"git": path})
    monkeypatch.setattr(trace, "_git_versions", lambda path: {"versions": path})
    monkeypatch.setattr(trace, "_ast_and_impact", lambda path: {"ast": path})
    trace.serve_trace_file(_Handler("/api/trace/file?path=a.py"))
    assert "ast" not in sent[-1]
    trace.serve_trace_file(_Handler("/api/trace/file?path=a.py&include=ast"))
    response = sent.pop()
    assert response["ast"] == {"ast": "a.py"}
    sent.pop()

    trace.serve_trace_impact(_Handler("/api/trace/impact"))
    response = sent.pop()
    assert response["reason"] == "missing path"
    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: False)
    trace.serve_trace_impact(_Handler("/?path=a.py"))
    response = sent.pop()
    assert response["reason"] == "ap_disabled"
    monkeypatch.setattr(ap_bridge, "is_enabled", lambda: True)
    monkeypatch.setattr(trace, "impact_for_path", lambda _path: None)
    trace.serve_trace_impact(_Handler("/?path=/repo/a.py"))
    response = sent.pop()
    assert response["reason"] == "not_indexed"
    monkeypatch.setattr(trace, "impact_for_path", lambda _path: {"upstream": [1]})
    trace.serve_trace_impact(_Handler("/?path=/repo/a.py"))
    response = sent.pop()
    assert response["available"] is True

    monkeypatch.setattr(
        trace,
        "_git_history",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("file")),
    )
    trace.serve_trace_file(_Handler("/?path=a"))
    monkeypatch.setattr(
        trace,
        "impact_for_path",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("impact")),
    )
    trace.serve_trace_impact(_Handler("/?path=a"))
    assert errors == ["file", "impact"]


class _Lock:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.released = 0

    def acquire(self, *, blocking):
        assert blocking is False
        return self.acquired

    def release(self):
        self.released += 1


class _Stream:
    def __init__(self, events=()):
        self.events = list(events)
        self.emitted = []

    def emit(self, *args, **kwargs):
        self.emitted.append(args + ((kwargs,) if kwargs else ()))

    def subscribe(self, *, since, timeout):
        assert timeout == 15.0
        yield from ((idx, event) for idx, event in self.events if idx >= since)

    def stats(self):
        return {"count": 4}


def test_activity_impact_trigger_and_json_helpers(monkeypatch):
    lock = _Lock(False)
    monkeypatch.setattr(activity, "_impact_lock", lock)
    activity._run_impact_pass("a.py")
    assert lock.released == 0

    lock = _Lock()
    stream = _Stream()
    monkeypatch.setattr(activity, "_impact_lock", lock)
    monkeypatch.setattr(activity_stream, "stream", lambda: stream)
    monkeypatch.setattr(
        activity,
        "_trigger_impact",
        lambda store, path: stream.emitted.append((store, path)),
    )
    from cortex_viz.server import trace_impact

    monkeypatch.setattr(
        trace_impact, "impact_for_path", lambda _path: {"upstream": [1]}
    )
    monkeypatch.setattr(
        impact_graph,
        "impact_to_graph",
        lambda *_args: {"nodes": [1], "edges": [2]},
    )
    activity._run_impact_pass("a.py")
    assert stream.emitted[0][0] == "activity"
    assert lock.released == 1

    activity._trigger_impact(None, "")
    activity._maybe_trigger_impact(None, {"action": "read", "target_kind": "file"})
    activity._maybe_trigger_impact(
        "db",
        {"action": "edit", "target_kind": "file", "detail": {"path": "/a.py"}},
    )
    assert stream.emitted[-1] == ("db", "/a.py")
    handler = _Handler()
    activity._send_json(handler, 202, {"ok": True})
    assert handler.responses == [202]
    assert json.loads(handler.wfile.getvalue()) == {"ok": True}


def test_activity_ingest_paths(monkeypatch):
    no_db = _Handler()
    activity.serve_activity_ingest(no_db, None)
    assert no_db.responses == [204]
    bad = _Handler(body=b"{")
    activity.serve_activity_ingest(bad, "db")
    assert bad.responses == [400]

    monkeypatch.setattr(activity_graph, "normalize_event", lambda _event: None)
    ignored = _Handler(body=b"{}")
    activity.serve_activity_ingest(ignored, "db")
    assert ignored.responses == [204]

    row = {"action": "read", "target_kind": "file", "detail": {}}
    monkeypatch.setattr(activity_graph, "normalize_event", lambda _event: row)
    monkeypatch.setattr(activity_store, "record_activity", lambda _store, _row: 7)
    monkeypatch.setattr(
        activity_graph,
        "event_to_graph",
        lambda _row: {"nodes": [1], "edges": [2]},
    )
    stream = _Stream()
    monkeypatch.setattr(activity_stream, "stream", lambda: stream)
    handler = _Handler(body=b"{}")
    activity.serve_activity_ingest(handler, "db")
    assert json.loads(handler.wfile.getvalue()) == {
        "ok": True,
        "id": 7,
        "action": "read",
    }
    assert stream.emitted == [
        ("activity", [1], [2], {"event_meta": {"activity_id": 7}})
    ]

    monkeypatch.setattr(
        activity_store,
        "record_activity",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db")),
    )
    failed = _Handler(body=b"{}")
    activity.serve_activity_ingest(failed, "db")
    assert json.loads(failed.wfile.getvalue())["ok"] is False


class _BrokenWriter(io.BytesIO):
    def write(self, _payload):
        raise BrokenPipeError


def test_activity_replay_tail_and_stream_lifecycle(monkeypatch):
    monkeypatch.setattr(
        activity_store, "read_recent", lambda *_args, **_kwargs: [{"id": 2}]
    )
    monkeypatch.setattr(
        activity_graph,
        "event_to_graph",
        lambda _row: {"nodes": [1], "edges": []},
    )
    monkeypatch.setattr(
        graph_event_stream, "format_event", lambda idx, _event: f"id:{idx}\n".encode()
    )
    handler = _Handler()
    assert activity._replay_log(handler, "db", 1) == (True, 2)
    assert handler.wfile.getvalue() == b"id:2\n"
    broken = _Handler()
    broken.wfile = _BrokenWriter()
    assert activity._replay_log(broken, "db", 1) == (False, 1)
    assert activity._write_or_stop(handler, b"ok")
    assert not activity._write_or_stop(broken, b"no")

    # The event replayed from PostgreSQL (activity_id=2) is also present in
    # the pre-replay buffer. It must be skipped; the event committed during
    # replay (activity_id=3) must be delivered exactly once with its durable
    # PostgreSQL id, never the unrelated in-memory deque index.
    stream = _Stream(
        events=[
            (3, {"event": "replayed", "activity_id": 2}),
            (4, {"event": "during-replay", "activity_id": 3}),
        ]
    )
    monkeypatch.setattr(activity_stream, "stream", lambda: stream)
    monkeypatch.setattr(graph_event_stream, "format_heartbeat", lambda: b"heartbeat")
    writes = iter([True, False])
    monkeypatch.setattr(activity, "_write_or_stop", lambda *_args: next(writes))
    activity._tail_live(handler, 3, 2)

    opened = []
    closed = []
    monkeypatch.setattr(
        http_standalone_state, "stream_opened", lambda: opened.append(1)
    )
    monkeypatch.setattr(
        http_standalone_state, "stream_closed", lambda: closed.append(1)
    )
    monkeypatch.setattr(activity, "_replay_log", lambda *_args: (False, 0))
    activity.serve_activity_stream(_Handler("/?since=bad"), "db")
    assert opened == [1] and closed == [1]

    tails = []
    replay_since = []
    monkeypatch.setattr(
        activity,
        "_replay_log",
        lambda _handler, _store, since: (replay_since.append(since) or True, 9),
    )
    monkeypatch.setattr(
        activity,
        "_tail_live",
        lambda _handler, buffer_cursor, durable_since: tails.append(
            (buffer_cursor, durable_since)
        ),
    )
    resumed = _Handler("/?since=bad")
    resumed.headers["Last-Event-ID"] = "8"
    activity.serve_activity_stream(resumed, "db")
    assert replay_since == [8]
    assert tails == [(4, 9)]
    assert closed == [1, 1]


class _Result:
    def __init__(self, *, one=None, many=()):
        self.one = one
        self.many = list(many)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _SankeyStore:
    def _execute(self, sql, params=None):
        if "stage_transitions" in sql and "AVG" not in sql:
            return _Result(many=[{"from_stage": "a", "to_stage": "b", "count": 2}])
        if "stage_transitions" in sql:
            return _Result(
                many=[
                    {
                        "from_stage": "a",
                        "to_stage": "b",
                        "avg_hours": 1.24,
                        "min_hours": 0.55,
                        "max_hours": 2.66,
                    }
                ]
            )
        if "consolidation_stage" in sql:
            assert params[0] in sankey._STAGES
            return _Result(one={"count": 1, "avg_heat": 1.23456, "empty": None})
        return _Result(one={"c": 10})

    def count_memories(self):
        return {"total": 7, "raw_total": 9, "discussions": 2}

    def get_domain_counts(self):
        return {"a": 1, "b": 2}

    def count_entities(self):
        return 3

    def count_relationships(self):
        return 4


def test_sankey_and_stats_build_cache_and_report_errors(monkeypatch):
    sent = []
    errors = []
    monkeypatch.setattr(
        sankey, "send_json_ok", lambda _handler, payload: sent.append(payload)
    )
    monkeypatch.setattr(
        sankey, "send_json_error", lambda _handler, exc: errors.append(str(exc))
    )
    store = _SankeyStore()
    assert sankey._sankey_transitions(store)[0]["count"] == 2
    assert sankey._sankey_timing(store)["a->b"]["avg_hours"] == 1.2
    assert sankey._sankey_stage_metrics(store)["labile"]["avg_heat"] == 1.235
    sankey.serve_sankey(_Handler(), store)
    response = sent.pop()
    assert response["total_memories"] == 10

    monkeypatch.setattr(
        graph_discussions, "_compute_memory_vitals", lambda _store: {"ok": 1}
    )
    payload = sankey._build_stats_payload(store)
    assert payload["node_count"] == 10
    assert payload["memory_count_raw"] == 9
    assert payload["system_vitals"] == {"ok": 1}
    sankey._stats_cache.update(ts=0.0, payload=None)
    times = iter([100.0, 110.0])
    monkeypatch.setattr(sankey.time, "time", lambda: next(times))
    sankey.serve_stats(_Handler(), store)
    first = sent.pop()
    sankey.serve_stats(_Handler(), store)
    response = sent.pop()
    assert response is first

    broken = SimpleNamespace(
        _execute=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sankey"))
    )
    sankey.serve_sankey(_Handler(), broken)
    monkeypatch.setattr(
        sankey,
        "_build_stats_payload",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("stats")),
    )
    monkeypatch.setattr(sankey.time, "time", lambda: 200.0)
    sankey._stats_cache.update(ts=0.0, payload=None)
    sankey.serve_stats(_Handler(), store)
    assert errors == ["sankey", "stats"]
