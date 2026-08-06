"""SSE resume-cursor semantics for ``/api/activity/stream``.

Two cursors coexist and must never be confused: the durable PostgreSQL
``session_activity.id`` (the replay authority and the only value that reaches
an SSE ``id`` field) and the in-process deque index (a wake-up position only).
The arms exercised here are the ones a reconnect depends on and that the
lifecycle test in ``test_http_live_coverage_contracts`` does not reach.
"""

from __future__ import annotations

import io

import pytest

from cortex_viz.server import http_standalone_activity as activity


class _Handler:
    def __init__(self, path="/", headers=None):
        self.path = path
        self.headers = dict(headers or {})
        self.wfile = io.BytesIO()
        self.responses = []
        self.response_headers = []

    def send_response(self, status):
        self.responses.append(status)

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass


class _CountingStream:
    def stats(self):
        return {"count": 0}


def _capture_since(monkeypatch):
    """Record the ``since`` the handler hands to the durable replay."""
    from cortex_viz.server import activity_stream

    seen = []
    monkeypatch.setattr(
        activity,
        "_replay_log",
        lambda _handler, _store, since: (seen.append(since) or True, 0),
    )
    monkeypatch.setattr(activity, "_tail_live", lambda *_args: None)
    monkeypatch.setattr(activity_stream, "stream", _CountingStream)
    return seen


@pytest.mark.parametrize("header", ["Last-Event-ID", "Last-Event-Id"])
def test_either_header_spelling_resumes_the_durable_cursor(monkeypatch, header):
    """RFC-defined SSE header, but a host may normalize its casing either way;
    the handler reads both spellings and this pins that it keeps doing so."""
    seen = _capture_since(monkeypatch)

    activity.serve_activity_stream(_Handler("/", {header: "12"}), "db")

    assert seen == [12]


def test_an_unparseable_resume_header_restarts_from_the_origin(monkeypatch):
    seen = _capture_since(monkeypatch)

    activity.serve_activity_stream(_Handler("/", {"Last-Event-ID": "abc"}), "db")

    assert seen == [0]


@pytest.mark.parametrize(
    ("query", "header", "expected"),
    [
        ("/?since=5", None, 5),
        ("/?since=5", "2", 5),
        ("/?since=2", "5", 5),
        ("/?since=bad", "5", 5),
        ("/", "5", 5),
        ("/", None, 0),
    ],
)
def test_the_furthest_cursor_wins_and_a_bad_query_never_rewinds_it(
    monkeypatch, query, header, expected
):
    """`max(header, query)`: whichever source is further ahead is authoritative,
    and a malformed query must not rewind a valid reconnect to the origin —
    that would replay the client's whole history as duplicates.
    """
    seen = _capture_since(monkeypatch)
    headers = {"Last-Event-ID": header} if header else None

    activity.serve_activity_stream(_Handler(query, headers), "db")

    assert seen == [expected]


def _row(row_id: int, action: str = "read"):
    return {
        "id": row_id,
        "session_id": "s1",
        "seq": row_id,
        "ts": float(row_id),
        "event_type": "tool_call",
        "action": action,
        "tool": "Read",
        "target_id": "file:a",
        "target_kind": "file",
        "target_label": "a.py",
        "edge_kind": action,
        "cwd": "/repo",
        "detail": {},
    }


def _stub_replay(monkeypatch, rows):
    from cortex_viz.infrastructure import activity_store
    from cortex_viz.server import graph_event_stream

    monkeypatch.setattr(activity_store, "read_recent", lambda *_a, **_k: rows)
    monkeypatch.setattr(
        graph_event_stream, "format_event", lambda idx, _event: f"id:{idx}\n".encode()
    )


def test_a_replay_with_nothing_new_keeps_the_clients_cursor(monkeypatch):
    """An idle reconnect: no rows past ``since``. The durable cursor must be
    preserved, not reset to 0, or the next live event would be numbered behind
    what the client already holds and be dropped as a duplicate.
    """
    _stub_replay(monkeypatch, [])
    handler = _Handler()

    assert activity._replay_log(handler, "db", 41) == (True, 41)
    assert handler.wfile.getvalue() == b""


def test_a_replay_advances_the_cursor_to_the_last_durable_row(monkeypatch):
    _stub_replay(monkeypatch, [_row(42), _row(43, "edit")])
    handler = _Handler()

    assert activity._replay_log(handler, "db", 41) == (True, 43)
    assert handler.wfile.getvalue() == b"id:42\nid:43\n"


class _ReplayStream:
    def __init__(self, events):
        self.events = list(events)

    def subscribe(self, *, since, timeout):
        assert timeout == 15.0
        yield from ((idx, event) for idx, event in self.events if idx >= since)


def _tail_once(monkeypatch, events, durable_since):
    """Run one ``_tail_live`` pass, stopping at the first heartbeat."""
    from cortex_viz.server import activity_stream, graph_event_stream

    monkeypatch.setattr(activity_stream, "stream", lambda: _ReplayStream(events))
    monkeypatch.setattr(
        graph_event_stream, "format_event", lambda idx, _event: f"id:{idx}\n".encode()
    )
    monkeypatch.setattr(graph_event_stream, "format_heartbeat", lambda: b"beat")
    written = []

    def _write(_handler, payload):
        written.append(payload)
        return payload != b"beat"

    monkeypatch.setattr(activity, "_write_or_stop", _write)
    activity._tail_live(_Handler(), 0, durable_since)
    return written


def test_an_event_without_a_durable_id_is_numbered_from_the_last_known_one(
    monkeypatch,
):
    """The build stream and heartbeats emit without an ``activity_id``. Those
    must still reach the client, tagged with the cursor it already holds, so a
    later reconnect does not resume behind the activity it has seen.
    """
    written = _tail_once(monkeypatch, [(0, {"label": "memories"})], durable_since=41)

    assert written == [b"id:41\n", b"beat"]


def test_an_event_already_replayed_from_postgresql_is_not_sent_twice(monkeypatch):
    written = _tail_once(
        monkeypatch,
        [(0, {"activity_id": 41}), (1, {"activity_id": 42})],
        durable_since=41,
    )

    assert written == [b"id:42\n", b"beat"]
