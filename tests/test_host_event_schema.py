"""Host-neutral activity-v1 normalization and schema contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_viz.core.activity_graph import normalize_event
from cortex_viz.core.workflow_graph_schema import NodeIdFactory

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
        }
    )

    assert row is not None
    assert row["action"] == "edit"
    assert row["target_id"] == NodeIdFactory.file_id("/repo/src/auth.ts")
    assert "host" not in row["detail"]
