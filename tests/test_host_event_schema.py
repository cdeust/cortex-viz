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


def test_an_mcp_call_without_an_artifact_is_labelled_by_its_tool():
    """The only branch of the new taxonomy with a fallback label."""
    row = normalize_event(
        _event(event="mcp_call", tool="mcp__postgres__query", artifact="")
    )

    assert row is not None
    assert row["target_label"] == "mcp__postgres__query"
    assert row["target_id"] == "mcp:mcp__postgres__query"


@pytest.mark.parametrize(
    ("event_name", "prefix"),
    [("api_call", "api:"), ("db_read", "db:"), ("db_write", "db:")],
)
def test_remote_and_database_labels_are_bounded(event_name, prefix):
    row = normalize_event(_event(event=event_name, artifact="u" * 500))

    assert row is not None
    assert row["target_label"] == "u" * 200
    assert row["target_id"] == prefix + "u" * 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "mcp_call", "tool": 7},
        {"event": "api_call", "artifact": {"url": "x"}},
        {"event": "db_read", "artifact": ["table"]},
        {"event": "db_write", "artifact": 42},
    ],
)
def test_a_non_string_identifier_is_rejected_like_an_empty_one(overrides):
    """The `isinstance` arm of each guard; only the falsy-string arm was covered."""
    assert normalize_event(_event(**overrides)) is None


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


def _observed(**overrides):
    """A legacy Claude hook event, the shape that carries observed payloads."""
    event = {
        "tool_name": "Read",
        "tool_input": {"file_path": "/repo/a.py"},
        "cwd": "/repo",
        "session_id": "s1",
        "ts": 1.0,
        "event_type": "PostToolUse",
    }
    event.update(overrides)
    return event


@pytest.mark.parametrize(
    "field",
    [
        "authorization",
        "Authorization",
        "cookie",
        "Set-Cookie",
        "credential",
        "aws_credentials",
        "password",
        "db_password",
        "secret",
        "client_secret",
        "token",
        "refresh_token",
        "api_key",
        "api-key",
        "apikey",
        "X-API-Key",
    ],
)
def test_every_credential_shaped_field_is_redacted_before_storage(field):
    """One alternative of `_SENSITIVE_FIELD` per case: a regex with seven
    branches that only ever sees `authorization` is six branches unverified."""
    row = normalize_event(_observed(tool_response={field: "sensitive-value"}))

    assert row is not None
    assert "sensitive-value" not in row["detail"]["result"]
    assert '"[redacted]"' in row["detail"]["result"]


@pytest.mark.parametrize("field", ["rows", "status", "duration_ms", "cookbook"])
def test_fields_that_only_look_adjacent_are_kept(field):
    """Negative control: over-redaction would silently gut the detail panel."""
    row = normalize_event(_observed(tool_response={field: "kept-value"}))

    assert row is not None
    assert "kept-value" in row["detail"]["result"]


@pytest.mark.parametrize("field", ["tokens_used", "input_tokens", "cookie_jar"])
def test_substring_matching_over_redacts_by_design(field):
    """`_SENSITIVE_FIELD` is an unanchored substring match, so it catches
    `db_password` and `refresh_token` — and also collides with innocent
    `*_tokens` telemetry. Pinned deliberately: a leaked credential is
    unrecoverable, a hidden token count is not, so the fail-safe direction
    stays. Narrowing this must break this test and argue the new pattern.
    """
    row = normalize_event(_observed(tool_response={field: "telemetry"}))

    assert row is not None
    assert row["detail"]["result"] == f'{{"{field}":"[redacted]"}}'


def test_redaction_reaches_credentials_nested_under_lists():
    row = normalize_event(
        _observed(
            tool_response={
                "results": [
                    {"headers": {"authorization": "Bearer nested"}},
                    {"ok": True},
                ]
            }
        )
    )

    assert row is not None
    assert "Bearer nested" not in row["detail"]["result"]
    assert row["detail"]["result"] == (
        '{"results":[{"headers":{"authorization":"[redacted]"}},{"ok":true}]}'
    )


def test_observed_summaries_are_bounded_for_the_detail_panel():
    row = normalize_event(
        _observed(
            tool_input={"query": "q" * 500},
            tool_response={"body": "r" * 500},
        )
    )

    assert row is not None
    assert len(row["detail"]["input_summary"]) == 200
    assert len(row["detail"]["result"]) == 200


def test_a_plain_string_payload_is_passed_through_and_bounded():
    """Hosts may send an already-summarized string rather than a JSON object;
    it must not be re-encoded with JSON quotes around it."""
    row = normalize_event(_observed(tool_response="rows=3"))

    assert row is not None
    assert row["detail"]["result"] == "rows=3"
    assert len(
        normalize_event(_observed(tool_response="r" * 500))["detail"]["result"]
    ) == (200)


def test_redaction_is_field_shaped_and_does_not_scan_free_text():
    """Known boundary, pinned rather than left to be discovered: redaction
    matches KEY names, so a credential a host embeds in a summary STRING is
    stored as-is. Widening it to value patterns needs a sourced pattern and a
    false-positive budget, so the limit is asserted here instead of implied.
    """
    row = normalize_event(_observed(tool_response="authorization: Bearer xyz"))

    assert row is not None
    assert row["detail"]["result"] == "authorization: Bearer xyz"


def test_an_unserializable_payload_degrades_to_text_instead_of_raising():
    class _Opaque:
        def __repr__(self):
            return "<opaque>"

    row = normalize_event(_observed(tool_response={"handle": _Opaque()}))

    assert row is not None
    assert "<opaque>" in row["detail"]["result"]


@pytest.mark.parametrize("empty", [None, "", {}, []])
def test_empty_observed_payloads_add_no_detail_keys(empty):
    """Absence is the behaviour: an empty payload must not create a blank
    `input_summary`/`result` the panel would render as a filled-in field."""
    row = normalize_event(_observed(tool_input=empty, tool_response=empty))

    assert row is not None
    assert "input_summary" not in row["detail"]
    assert "result" not in row["detail"]


def test_tool_result_is_read_when_the_host_sends_no_tool_response():
    row = normalize_event(_observed(tool_result={"rows": 1}))

    assert row is not None
    assert row["detail"]["result"] == '{"rows":1}'


def test_an_explicit_null_tool_response_wins_over_tool_result():
    """`tool_response` present-but-null means "the tool returned nothing", and
    must not silently fall back to a stale `tool_result` from the same event."""
    row = normalize_event(_observed(tool_response=None, tool_result={"rows": 1}))

    assert row is not None
    assert "result" not in row["detail"]
