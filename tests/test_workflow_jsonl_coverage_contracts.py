"""Behavioral contracts for session-JSONL workflow graph loaders."""

from __future__ import annotations

from pathlib import Path

from cortex_viz.infrastructure import workflow_graph_source_jsonl as jsonl


class DirEntry:
    def __init__(self, name, *, directory=False, file=False):
        self.name = name
        self.directory = directory
        self.file = file

    def is_dir(self):
        return self.directory

    def is_file(self):
        return self.file


def test_normalize_tool_name_collapses_host_aliases():
    assert jsonl.normalize_tool_name("MultiEdit") == "Edit"
    assert jsonl.normalize_tool_name("Agent") == "Task"
    assert jsonl.normalize_tool_name("Unknown") == ""


def test_iter_session_paths_filters_entries_and_collapses_subagents(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(jsonl, "CLAUDE_DIR", tmp_path)
    projects = tmp_path / "projects"

    def list_entries(path, **_kwargs):
        if path == projects:
            return [DirEntry("not-dir", file=True), DirEntry("project", directory=True)]
        return [
            DirEntry("ignored.txt", file=True),
            DirEntry("nested", directory=True),
            DirEntry("session-subagent-Explore.jsonl", file=True),
            DirEntry("other-explore-plan.jsonl", file=True),
        ]

    monkeypatch.setattr(jsonl, "list_dir", list_entries)
    rows = list(jsonl.iter_session_paths(lambda name: f"domain:{name}"))
    assert rows == [
        (
            "session",
            "domain:project",
            projects / "project" / "session-subagent-Explore.jsonl",
        ),
        (
            "other",
            "domain:project",
            projects / "project" / "other-explore-plan.jsonl",
        ),
    ]


def install_session_fixtures(monkeypatch):
    sessions = [
        ("session", "alpha", Path("/sessions/a.jsonl")),
        ("session", "alpha", Path("/sessions/b.jsonl")),
    ]
    events = [
        {
            "name": "Task",
            "input": {"subagent_type": "Explore"},
            "timestamp": "2026-01-02",
        },
        {
            "name": "Agent",
            "input": {"subagent_type": "Explore"},
            "timestamp": "2026-01-01",
        },
        {"name": "Agent", "input": {}},
        {"name": "Unknown", "input": {}},
        {
            "name": "Read",
            "input": {"file_path": "/repo/a.py", "path": "./a.py"},
            "timestamp": "2026-01-03",
        },
        {
            "name": "MultiEdit",
            "input": {"file_path": "/repo/a.py"},
            "timestamp": "2026-01-01",
        },
        {
            "name": "NotebookRead",
            "input": {"notebook_path": "~/note.ipynb"},
            "ts": "2026-01-04",
        },
        {
            "name": "Bash",
            "input": {"command": "\n git status\nnext"},
            "timestamp": "2026-01-02",
        },
        {
            "name": "Bash",
            "input": {"command": "cat /repo/a.py ./rel.py /repo/a.py; word"},
            "timestamp": "2026-01-05",
        },
        {"name": "Bash", "input": {"command": ""}},
    ]
    records = [
        {"type": "user", "message": {"content": "/plugin:refine argument"}},
        {"type": "user", "message": {"content": "/help"}},
        {"type": "user", "message": {"content": "plain"}},
        {
            "type": "user",
            "message": {
                "content": [{"type": "image"}, {"type": "text", "text": "/review"}]
            },
        },
        {"type": "assistant", "message": {"content": "not-list"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    "not-dict",
                    {"type": "text", "text": "ignored"},
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "mcp__cortex__recall"},
                ]
            },
        },
    ]
    monkeypatch.setattr(jsonl, "iter_session_paths", lambda _resolver: sessions)
    monkeypatch.setattr(jsonl, "iter_tool_uses", lambda _path: list(events))
    monkeypatch.setattr(jsonl, "read_head_tail", lambda _path: list(records))
    return sessions, events, records


def test_agent_discussion_tool_and_command_loaders(monkeypatch):
    install_session_fixtures(monkeypatch)

    def resolver(name):
        return f"domain:{name}"

    agents = jsonl.load_agent_events(resolver)
    assert agents == [{"subagent_type": "Explore", "domain": "alpha", "count": 4}]

    tool_uses = jsonl.load_discussion_tool_uses(resolver)
    by_tool = {row["tool"]: row["count"] for row in tool_uses}
    assert by_tool["Task"] == 6
    assert by_tool["Read"] == 4
    assert by_tool["Edit"] == 2
    assert by_tool["Bash"] == 6

    discussion_agents = jsonl.load_discussion_agents(resolver)
    assert discussion_agents == [
        {
            "session_id": "session",
            "domain": "alpha",
            "subagent_type": "Explore",
            "count": 4,
        }
    ]
    commands = jsonl.load_discussion_commands(
        resolver,
        lambda cmd: f"hash:{cmd}",
        lambda text: next(
            (line.strip() for line in text.splitlines() if line.strip()), ""
        ),
    )
    assert {row["cmd"]: row["count"] for row in commands} == {
        "git status": 2,
        "cat /repo/a.py ./rel.py /repo/a.py; word": 2,
    }


def test_discussion_file_and_file_access_loaders(monkeypatch):
    install_session_fixtures(monkeypatch)

    def resolver(name):
        return name

    files = jsonl.load_discussion_files(resolver)
    counts = {row["file_path"]: row["count"] for row in files}
    assert counts["/repo/a.py"] == 8
    assert counts["./a.py"] == 2
    assert counts["~/note.ipynb"] == 2
    assert counts["./rel.py"] == 2

    access = jsonl.load_file_access_events(resolver)
    read = next(
        row
        for row in access
        if row["tool"] == "Read" and row["file_path"] == "/repo/a.py"
    )
    assert read == {
        "tool": "Read",
        "file_path": "/repo/a.py",
        "domain": "alpha",
        "count": 2,
        "first_ts": "2026-01-03",
        "last_ts": "2026-01-03",
    }
    edit = next(row for row in access if row["tool"] == "Edit")
    assert edit["first_ts"] == "2026-01-01"
    bash = next(
        row
        for row in access
        if row["tool"] == "Bash" and row["file_path"] == "/repo/a.py"
    )
    assert bash["count"] == 2


def test_collect_tool_touches_and_refs_dedupe_bash_within_event():
    buckets = {}
    jsonl._collect_tool_file_touches(
        {"name": "Read", "input": {"file_path": "a", "path": "b"}},
        "s",
        buckets,
    )
    jsonl._collect_tool_file_touches(
        {"name": "Bash", "input": {"command": "cat /tmp/a.py /tmp/a.py; ./b.py"}},
        "s",
        buckets,
    )
    assert buckets[("s", "a")] == 1
    assert buckets[("s", "/tmp/a.py")] == 2
    refs = list(
        jsonl._tool_file_refs(
            {
                "name": "Bash",
                "input": {"command": "cat /tmp/a.py /tmp/a.py; ./b.py"},
                "ts": "t",
            }
        )
    )
    assert refs == [("/tmp/a.py", "t"), ("./b.py", "t")]


def test_skill_and_mcp_usage_loaders(monkeypatch):
    install_session_fixtures(monkeypatch)

    def resolver(name):
        return name

    skills = jsonl.load_skill_usage(resolver)
    assert {row["name"]: row["count"] for row in skills} == {
        "refine": 2,
        "review": 2,
    }
    mcp = jsonl.load_mcp_usage(resolver)
    assert mcp == [
        {"server": "cortex", "tool": "recall", "domain": "alpha", "count": 2}
    ]


def test_extract_user_text_supported_shapes():
    assert jsonl._extract_user_text({"type": "assistant"}) == ""
    assert (
        jsonl._extract_user_text({"type": "user", "message": {"content": "plain"}})
        == "plain"
    )
    assert (
        jsonl._extract_user_text(
            {
                "type": "user",
                "message": {"content": ["bad", {"type": "text", "text": "first"}]},
            }
        )
        == "first"
    )
    assert jsonl._extract_user_text({"type": "user", "message": {"content": {}}}) == ""


def test_discussion_metadata_projection(monkeypatch):
    monkeypatch.setattr(
        jsonl,
        "discover_conversations",
        lambda: [
            {
                "sessionId": "s1",
                "project": "alpha",
                "firstMessage": "x" * 70,
                "messageCount": "3",
                "startedAt": "start",
                "lastActivity": "last",
                "endedAt": "ended",
                "duration": 5,
            },
            {"project": "beta", "endedAt": "ended"},
        ],
    )
    rows = jsonl.load_discussions(lambda project: f"domain:{project}")
    assert rows[0]["session_id"] == "s1"
    assert len(rows[0]["title"]) == 60
    assert rows[0]["last_activity"] == "last"
    assert rows[1]["session_id"] == "beta"
    assert rows[1]["title"] is None
    assert rows[1]["last_activity"] == "ended"
