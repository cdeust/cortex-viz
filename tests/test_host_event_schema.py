"""Host-neutral activity-v1 normalization and schema contracts."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from cortex_viz.core.activity_graph import normalize_event
from cortex_viz.core.workflow_graph_schema import NodeIdFactory
from cortex_viz.server.activity_stream import stream as activity_stream
from cortex_viz.server.http_standalone_activity import serve_activity_ingest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "docs" / "host-event-v1.schema.json"


def _event(**overrides):
    event = {
        "schema_version": "1",
        "host": "codex",
        "session_id": "session-1",
        "timestamp": "2026-08-02T12:34:56Z",
        "event": "tool_call",
        "tool": "read_file",
        "input_summary": "Read the authentication module",
        "artifact": "src/auth.ts",
        "result": "success",
        "cwd": "/repo",
    }
    event.update(overrides)
    return event


class _Cursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return {"id": 41}


class _Connection:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return _Cursor(self.calls)

    def commit(self):
        pass


class _Pool:
    def __init__(self):
        self.calls = []

    def connection(self):
        return _Connection(self.calls)


class _Store:
    def __init__(self):
        self.batch_pool = _Pool()


class _Handler:
    def __init__(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = BytesIO(raw)
        self.wfile = BytesIO()
        self.status = None
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


def test_published_schema_declares_the_runtime_contract():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == "1"
    assert set(schema["required"]) == {
        "schema_version",
        "host",
        "session_id",
        "timestamp",
        "event",
    }
    assert set(schema["properties"]["event"]["enum"]) == {
        "prompt",
        "tool_call",
        "mcp_call",
        "api_call",
        "db_read",
        "db_write",
        "file_read",
        "file_edit",
        "file_write",
        "terminal_run",
        "skill",
        "subagent",
        "web",
    }


def test_codex_tool_event_with_artifact_joins_the_existing_file_id_space():
    row = normalize_event(_event())

    assert row is not None
    assert row["session_id"] == "session-1"
    assert row["ts"] == pytest.approx(1785674096.0)
    assert row["tool"] == "read_file"
    assert row["action"] == "tool"
    assert row["target_kind"] == "file"
    assert row["target_id"] == NodeIdFactory.file_id("/repo/src/auth.ts")
    assert row["detail"] == {
        "schema_version": "1",
        "host": "codex",
        "input_summary": "Read the authentication module",
        "result": "success",
        "path": "/repo/src/auth.ts",
    }


def test_codex_event_traverses_http_store_and_live_sse_stream():
    """Exercise the production seam, not just the neutral normalizer."""
    store = _Store()
    handler = _Handler(_event(event="file_read", tool="fs"))
    live = activity_stream()
    live.reset()

    try:
        serve_activity_ingest(handler, store)

        assert handler.status == 200
        assert json.loads(handler.wfile.getvalue()) == {
            "ok": True,
            "id": 41,
            "action": "read",
        }

        inserts = [
            params
            for sql, params in store.batch_pool.calls
            if params is not None and "INSERT INTO session_activity" in sql
        ]
        assert len(inserts) == 1
        persisted = inserts[0]
        assert persisted[0] == "session-1"
        assert persisted[2:5] == ("file_read", "fs", "read")
        assert persisted[5] == NodeIdFactory.file_id("/repo/src/auth.ts")
        assert json.loads(persisted[10])["host"] == "codex"

        emitted = list(live.subscribe(since=0, timeout=0.01))
        assert len(emitted) == 1
        _, event = emitted[0]
        assert event["label"] == "activity"
        assert any(
            node["id"] == NodeIdFactory.file_id("/repo/src/auth.ts")
            for node in event["nodes"]
        )
        assert event["edges"], "the SSE delta must include the action path"
    finally:
        live.reset()


@pytest.mark.parametrize(
    ("event_name", "expected_action", "expected_edge"),
    [
        ("file_read", "read", "read"),
        ("file_edit", "edit", "edit"),
        ("file_write", "write", "write"),
    ],
)
def test_file_events_use_explicit_host_neutral_semantics(
    event_name, expected_action, expected_edge
):
    row = normalize_event(_event(event=event_name, tool="fs", artifact="src/auth.ts"))

    assert row is not None
    assert row["action"] == expected_action
    assert row["edge_kind"] == expected_edge
    assert row["target_id"] == NodeIdFactory.file_id("/repo/src/auth.ts")


def test_prompt_event_preserves_host_provenance():
    row = normalize_event(
        _event(event="prompt", tool="", artifact="", input_summary="Fix auth")
    )

    assert row is not None
    assert row["action"] == "prompt"
    assert row["target_label"] == "Fix auth"
    assert row["detail"]["host"] == "codex"


@pytest.mark.parametrize(
    ("event_name", "artifact", "tool", "action", "target_kind", "edge_kind"),
    [
        (
            "mcp_call",
            "postgres:query",
            "mcp__postgres__query",
            "mcp_call",
            "mcp",
            "call",
        ),
        (
            "api_call",
            "GET https://api.example.test/users",
            "http",
            "api_call",
            "api",
            "call",
        ),
        (
            "db_read",
            "postgres:cortex.session_activity",
            "postgres",
            "db_read",
            "database",
            "read",
        ),
        (
            "db_write",
            "postgres:cortex.session_activity",
            "postgres",
            "db_write",
            "database",
            "write",
        ),
    ],
)
def test_explicit_remote_and_database_events_are_not_inferred(
    event_name, artifact, tool, action, target_kind, edge_kind
):
    row = normalize_event(_event(event=event_name, artifact=artifact, tool=tool))

    assert row is not None
    assert row["action"] == action
    assert row["target_kind"] == target_kind
    assert row["target_label"] == artifact
    assert row["edge_kind"] == edge_kind


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "2"},
        {"host": ""},
        {"host": "Codex"},
        {"session_id": ""},
        {"timestamp": "not-a-timestamp"},
        {"timestamp": float("nan")},
        {"event": "unknown"},
        {"event": "tool_call", "tool": ""},
        {"event": "mcp_call", "tool": ""},
        {"event": "api_call", "artifact": ""},
        {"event": "db_read", "artifact": ""},
        {"event": "db_write", "artifact": ""},
        {"event": "file_edit", "artifact": ""},
        {"event": "prompt", "input_summary": ""},
        {"event": "skill", "tool": "", "input_summary": ""},
        {"event": "web", "artifact": "", "input_summary": ""},
    ],
)
def test_invalid_host_events_are_dropped_without_affecting_legacy_ingest(overrides):
    assert normalize_event(_event(**overrides)) is None


def test_legacy_claude_hook_shape_remains_supported():
    row = normalize_event(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/src/auth.ts"},
            "cwd": "/repo",
            "session_id": "claude-session",
            "ts": 1.0,
            "event_type": "PostToolUse",
            "tool_response": {
                "rows": 3,
                "authorization": "Bearer private",
            },
        }
    )

    assert row is not None
    assert row["action"] == "edit"
    assert row["target_id"] == NodeIdFactory.file_id("/repo/src/auth.ts")
    assert "host" not in row["detail"]
    assert row["detail"]["input_summary"] == '{"file_path":"/repo/src/auth.ts"}'
    assert row["detail"]["result"] == '{"rows":3,"authorization":"[redacted]"}'
