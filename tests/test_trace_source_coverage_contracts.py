"""Behavioral contracts for the ordered Claude session-trace reader."""

from __future__ import annotations

import json

from cortex_viz.infrastructure import trace_source


def _write_jsonl(path, *records):
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def test_project_iteration_domains_and_content_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_source, "CLAUDE_DIR", tmp_path)
    projects = tmp_path / "projects"
    first = projects / "-Users-dev-Developments-Cortex"
    duplicate = projects / "Cortex"
    empty = projects / "empty"
    first.mkdir(parents=True)
    duplicate.mkdir()
    empty.mkdir()
    (projects / "README").write_text("not a directory", encoding="utf-8")
    _write_jsonl(first / "one.jsonl", {})
    _write_jsonl(duplicate / "two.jsonl", {})
    (first / "ignore.txt").write_text("skip", encoding="utf-8")

    assert trace_source.project_dir_to_label(first.name) == "Cortex"
    assert trace_source.project_dir_to_domain(first.name) == "domain:cortex"
    assert trace_source._projects_dir() == projects
    assert {entry.name for entry in trace_source._iter_project_dirs()} == {
        first.name,
        duplicate.name,
        empty.name,
    }
    assert [path.name for path in trace_source._session_files(first)] == ["one.jsonl"]
    assert trace_source._first_user_text("plain") == "plain"
    assert (
        trace_source._first_user_text(
            ["skip", {"type": "image"}, {"type": "text", "text": "hello"}]
        )
        == "hello"
    )
    assert trace_source._first_user_text({"text": "no"}) == ""

    domains = trace_source.list_domains()
    assert domains == [
        {
            "id": "domain:cortex",
            "kind": "domain",
            "type": "domain",
            "label": "Cortex",
            "domain_id": "domain:cortex",
            "session_count": 2,
            "expandable": True,
        }
    ]


def test_scan_session_meta_handles_live_jsonl_and_read_errors(tmp_path):
    transcript = tmp_path / "fallback.jsonl"
    transcript.write_text(
        "invalid\n\n"
        + "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-01-02T00:00:00Z",
                        "message": {"content": [{"type": "tool_result"}, "skip"]},
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "session-id",
                        "gitBranch": "feature",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {
                            "content": [
                                {"type": "text", "text": " First prompt ".strip()}
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-01-03T00:00:00Z",
                        "message": {
                            "content": [
                                "skip",
                                {"type": "tool_use", "name": "TodoWrite"},
                                {"type": "tool_use", "name": "Read"},
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": "not-a-list"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert trace_source._scan_session_meta(transcript) == {
        "session_id": "session-id",
        "first_ts": "2026-01-01T00:00:00Z",
        "last_ts": "2026-01-03T00:00:00Z",
        "first_prompt": "First prompt",
        "git_branch": "feature",
        "action_count": 2,
        "path": str(transcript),
    }

    no_id = tmp_path / "stem-id.jsonl"
    _write_jsonl(no_id, {"type": "user", "message": {"content": "hello"}})
    assert trace_source._scan_session_meta(no_id)["session_id"] == "stem-id"
    assert trace_source._scan_session_meta(tmp_path) is None
    assert trace_source._short("  many\n spaces  ", 60) == "many spaces"
    assert trace_source._short("abcdefgh", 5) == "abcd…"


def test_list_sessions_filters_noise_and_orders_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_source, "CLAUDE_DIR", tmp_path)
    project = tmp_path / "projects" / "Cortex"
    other = tmp_path / "projects" / "Other"
    project.mkdir(parents=True)
    other.mkdir()

    def session(name, sid, timestamp, prompt, tool="Read"):
        _write_jsonl(
            project / name,
            {
                "type": "user",
                "sessionId": sid,
                "timestamp": timestamp,
                "message": {"content": prompt},
            },
            {
                "type": "assistant",
                "timestamp": timestamp,
                "message": {
                    "content": [{"type": "tool_use", "name": tool, "input": {}}]
                },
            },
        )

    session("old.jsonl", "old", "2026-01-01T00:00:00Z", "Old prompt")
    session("new.jsonl", "new", "2026-01-02T00:00:00Z", "N" * 80)
    session("quiet.jsonl", "quiet", "2026-01-03T00:00:00Z", "Quiet", "TodoWrite")
    session("new-subagent-a.jsonl", "sub", "2026-01-04T00:00:00Z", "Subagent")
    session("new-explore-a.jsonl", "exp", "2026-01-05T00:00:00Z", "Explore")
    (project / "unreadable.jsonl").mkdir()
    _write_jsonl(other / "other.jsonl", {"type": "user"})

    payload = trace_source.list_sessions("domain:cortex")
    assert [node["session_id"] for node in payload["nodes"]] == [
        "quiet",
        "new",
        "old",
    ]
    assert payload["nodes"][1]["label"].endswith("…")
    assert all(node["action_count"] == 1 for node in payload["nodes"])
    assert {edge["target"] for edge in payload["edges"]} == {
        "session:old",
        "session:new",
        "session:quiet",
    }
    assert trace_source.list_sessions("domain:missing") == {"nodes": [], "edges": []}


def test_find_and_iter_session_events_fold_subagents_and_classify(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(trace_source, "CLAUDE_DIR", tmp_path)
    project = tmp_path / "projects" / "Cortex"
    other = tmp_path / "projects" / "Other"
    project.mkdir(parents=True)
    other.mkdir()

    parent = project / "sid.jsonl"
    subagent = project / "sid-subagent-a.jsonl"
    explore = other / "sid-explore-a.jsonl"
    ignored = project / "different.jsonl"
    unreadable = project / "sid-subagent-b.jsonl"
    _write_jsonl(
        parent,
        {
            "type": "assistant",
            "timestamp": "2026-01-02T00:00:00Z",
            "cwd": "/repo",
            "message": {
                "content": [
                    "not-a-block",
                    {"type": "text", "text": "short"},
                    {"type": "text", "text": "D" * 80},
                    {"type": "image"},
                    {
                        "type": "tool_use",
                        "name": "mcp__plugin_hypermnesia-mcp_cortex__remember",
                        "input": {"content": "decision"},
                    },
                    {
                        "type": "tool_use",
                        "name": "mcp__plugin_hypermnesia-mcp_cortex__recall",
                        "input": {"query": "history"},
                    },
                    {"type": "tool_use", "name": "TodoWrite"},
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "review"}},
                    {
                        "type": "tool_use",
                        "name": "mcp__postgres__query",
                        "input": {"query": "SELECT 1"},
                    },
                    {"type": "tool_use", "name": "Read", "input": None},
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"content": [{"type": "text", "text": "Prompt"}]},
        },
        {"type": "assistant", "message": {"content": "plain"}},
    )
    subagent.write_text(
        "invalid\n"
        + json.dumps(
            {
                "type": "user",
                "timestamp": "2026-01-03T00:00:00Z",
                "message": {"content": "Sub prompt"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_jsonl(
        explore,
        {
            "type": "assistant",
            "timestamp": "2026-01-04T00:00:00Z",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
        },
    )
    _write_jsonl(ignored, {"type": "user", "message": {"content": "ignore"}})
    unreadable.mkdir()

    matches = trace_source._find_session_files("sid")
    assert {path.name for path in matches} == {
        parent.name,
        subagent.name,
        explore.name,
    }
    assert trace_source.iter_session_events("missing") == []
    monkeypatch.setattr(
        trace_source, "_find_session_files", lambda _sid: [*matches, unreadable]
    )
    events = trace_source.iter_session_events("sid")
    assert [event["kind"] for event in events] == [
        "prompt",
        "discussion",
        "memory",
        "memory",
        "action",
        "action",
        "action",
        "action",
        "prompt",
        "action",
    ]
    assert events[2]["op"] == "remember" and events[2]["text"] == "decision"
    assert events[3]["op"] == "recall" and events[3]["text"] == "history"
    assert [event["tool"] for event in events[4:8]] == [
        "TodoWrite",
        "Skill",
        "mcp__postgres__query",
        "Read",
    ]
    assert events[7]["cwd"] == "/repo"
    assert events[-1]["tool"] == "Bash" and events[-1]["cwd"] == ""
