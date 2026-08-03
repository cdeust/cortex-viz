"""Behavioral contracts for Claude-project memory and transcript scanning."""

from __future__ import annotations

import json
from types import SimpleNamespace

from cortex_viz.infrastructure import scanner


def _jsonl(*records: object) -> str:
    return "\n".join(json.dumps(record) for record in records) + "\n"


def test_parse_jsonl_and_head_tail_tolerate_live_or_missing_files(tmp_path):
    assert scanner._parse_jsonl_lines(["", "not-json", '{"ok": true}']) == [
        {"ok": True}
    ]
    assert scanner.read_head_tail(tmp_path / "missing.jsonl") == []
    assert scanner.read_head_tail(tmp_path) == []

    small = tmp_path / "small.jsonl"
    small.write_text('{"part": "head"}\ninvalid\n', encoding="utf-8")
    assert scanner.read_head_tail(small) == [{"part": "head"}]

    large = tmp_path / "large.jsonl"
    filler = {"padding": "x" * 900}
    large.write_text(
        _jsonl({"part": "head"}, *(filler for _ in range(50)), {"part": "tail"}),
        encoding="utf-8",
    )
    records = scanner.read_head_tail(large)
    assert records[0] == {"part": "head"}
    assert records[-1] == {"part": "tail"}


def test_iter_tool_uses_streams_only_assistant_tool_blocks(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\ninvalid\n"
        + _jsonl(
            {"type": "user", "message": {"content": "question"}},
            {"type": "assistant", "message": {"content": "plain"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        "not-a-block",
                        {"type": "text", "text": "answer"},
                        {"type": "tool_use", "name": "Read", "input": None},
                        {"type": "tool_use", "name": None, "input": {"x": 1}},
                    ]
                },
            },
        ),
        encoding="utf-8",
    )

    assert list(scanner.iter_tool_uses(transcript)) == [
        {"name": "Read", "input": {}, "line": 5},
        {"name": "", "input": {"x": 1}, "line": 5},
    ]
    assert list(scanner.iter_tool_uses(tmp_path / "missing")) == []
    assert list(scanner.iter_tool_uses(tmp_path)) == []


def test_format_timestamp_and_parse_memory_file(tmp_path):
    assert scanner._format_timestamp(None, "st_mtime") is None
    assert scanner._format_timestamp(SimpleNamespace(), "st_mtime") is None
    assert scanner._format_timestamp(SimpleNamespace(st_mtime=0), "st_mtime") == (
        "1970-01-01T00:00:00Z"
    )

    note = tmp_path / "decision.md"
    note.write_text(
        "---\n"
        "name: Stable API\n"
        "description: Keep compatibility\n"
        "type: decision\n"
        "---\n"
        "Body\n",
        encoding="utf-8",
    )
    record = scanner._parse_memory_file(note, "project", note.name)
    assert record is not None
    assert record["name"] == "Stable API"
    assert record["description"] == "Keep compatibility"
    assert record["type"] == "decision"
    assert record["body"].strip() == "Body"
    assert record["modifiedAt"].endswith("Z")

    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert scanner._parse_memory_file(empty, "project", empty.name) is None

    plain = tmp_path / "fallback.md"
    plain.write_text("Plain body", encoding="utf-8")
    fallback = scanner._parse_memory_file(plain, "project", plain.name)
    assert fallback is not None
    assert fallback["name"] == "fallback"
    assert fallback["type"] == "unknown"


def test_discover_all_memories_filters_and_reports_bad_files(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(scanner, "CLAUDE_DIR", tmp_path)
    projects = tmp_path / "projects"
    memory_dir = projects / "project-one" / "memory"
    memory_dir.mkdir(parents=True)
    (projects / "not-a-directory").write_text("skip", encoding="utf-8")
    (memory_dir / "good.md").write_text("Remember this", encoding="utf-8")
    (memory_dir / "MEMORY.md").write_text("index", encoding="utf-8")
    (memory_dir / "ignore.txt").write_text("skip", encoding="utf-8")
    (memory_dir / "broken.md").write_text("broken", encoding="utf-8")
    (projects / "empty-project").mkdir()

    original = scanner._parse_memory_file

    def parse_or_fail(path, project_name, file_name):
        if file_name == "broken.md":
            raise ValueError("broken memory")
        return original(path, project_name, file_name)

    monkeypatch.setattr(scanner, "_parse_memory_file", parse_or_fail)
    memories = scanner.discover_all_memories()
    assert [memory["file"] for memory in memories] == ["good.md"]
    assert "broken memory" in capsys.readouterr().err

    monkeypatch.setattr(scanner, "CLAUDE_DIR", tmp_path / "absent")
    assert scanner.discover_all_memories() == []


def test_parse_and_discover_conversations(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scanner, "CLAUDE_DIR", tmp_path)
    projects = tmp_path / "projects"
    project = projects / "project-one"
    project.mkdir(parents=True)
    (projects / "not-a-directory").write_text("skip", encoding="utf-8")

    valid = project / "session.jsonl"
    valid.write_text(
        _jsonl(
            {
                "type": "user",
                "sessionId": "sid",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": "Question"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {"content": [{"type": "text", "text": "Answer"}]},
            },
        ),
        encoding="utf-8",
    )
    (project / "session-subagent-a.jsonl").write_text("{}\n", encoding="utf-8")
    (project / "ignore.txt").write_text("skip", encoding="utf-8")
    (project / "empty.jsonl").write_text("{}\n", encoding="utf-8")
    (project / "broken.jsonl").write_text("{}\n", encoding="utf-8")

    assert (
        scanner._parse_conversation_file(
            project / "missing.jsonl", "project-one", "missing"
        )
        is None
    )
    assert (
        scanner._parse_conversation_file(
            project / "empty.jsonl", "project-one", "empty"
        )
        is None
    )
    parsed = scanner._parse_conversation_file(valid, "project-one", "fallback")
    assert parsed is not None
    assert parsed["sessionId"] == "sid"
    assert parsed["messageCount"] == 2

    original = scanner._parse_conversation_file

    def parse_or_fail(path, project_name, fallback_id):
        if path.name == "broken.jsonl":
            raise ValueError("broken conversation")
        return original(path, project_name, fallback_id)

    monkeypatch.setattr(scanner, "_parse_conversation_file", parse_or_fail)
    conversations = scanner.discover_conversations()
    assert [conversation["sessionId"] for conversation in conversations] == ["sid"]
    assert "broken conversation" in capsys.readouterr().err

    groups = scanner.group_by_project(
        [*conversations, {"project": "other"}, {"without": "project"}]
    )
    assert set(groups) == {"project-one", "other", ""}

    monkeypatch.setattr(scanner, "CLAUDE_DIR", tmp_path / "absent")
    assert scanner.discover_conversations() == []
