"""Behavioral contracts for the workflow graph source facade."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from cortex_viz.infrastructure import workflow_graph_source as source


def test_command_domain_and_tool_helpers(monkeypatch):
    assert source._cmd_hash("echo hi") == source._cmd_hash("echo hi")
    assert len(source._cmd_hash("echo hi")) == 12
    assert source._first_line("\n first \nsecond") == "first"
    assert source._first_line("   ") == ""
    assert source._domain_from_directory(None) is None

    monkeypatch.setattr(
        source, "resolve_cwd", lambda value: "canonical" if value == "cwd" else None
    )
    monkeypatch.setattr(
        source, "resolve_domain", lambda value: "domain" if value == "alias" else None
    )
    monkeypatch.setattr(source, "cwd_to_project_id", lambda value: f"id:{value}")
    monkeypatch.setattr(source, "project_id_to_label", lambda value: f"label:{value}")
    monkeypatch.setattr(source, "domain_id_from_label", lambda value: f"slug:{value}")
    assert source._domain_from_directory("cwd") == "canonical"
    assert source._domain_from_directory("alias") == "domain"
    assert source._domain_from_directory("unknown").startswith("slug:")
    assert source._domain_from_project_dir("") == ""
    assert source._domain_from_project_dir("alias") == "domain"
    assert source._domain_from_project_dir("unknown").startswith("slug:")

    assert source._tool_from_tags(["other", "tool:edit"]) == "Edit"
    assert source._tool_from_tags(["tool:unknown"]) is None
    assert source._tool_from_tags([]) is None


def test_iter_skill_files_and_hook_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "CLAUDE_DIR", tmp_path)
    user_skill = tmp_path / "skills" / "user.md"
    plugin_skill = tmp_path / "plugins" / "cache" / "plugin" / "skills" / "plugin.md"
    user_skill.parent.mkdir(parents=True)
    plugin_skill.parent.mkdir(parents=True)
    user_skill.write_text("skill")
    plugin_skill.write_text("skill")
    assert set(source._iter_skill_files()) == {user_skill, plugin_skill}

    settings = tmp_path / "settings.json"
    hooks = tmp_path / "plugins" / "cache" / "plugin" / "hooks" / "hooks.json"
    plugin_settings = (
        tmp_path / "plugins" / "cache" / "plugin" / ".claude" / "settings.json"
    )
    settings.write_text("{}")
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{}")
    plugin_settings.parent.mkdir(parents=True)
    plugin_settings.write_text("{}")
    assert set(source._iter_hook_sources()) == {
        (settings, None),
        (hooks, None),
        (plugin_settings, None),
    }


def test_load_skills_deduplicates_stems(monkeypatch):
    monkeypatch.setattr(
        source,
        "_iter_skill_files",
        lambda: iter(
            [Path("/one/review.md"), Path("/two/review.md"), Path("/x/test.md")]
        ),
    )
    rows = source.WorkflowGraphSource().load_skills()
    assert rows == [
        {"name": "review", "path": "/one/review.md", "domains": []},
        {"name": "test", "path": "/x/test.md", "domains": []},
    ]


def test_load_hooks_parses_nested_commands_and_skips_bad_inputs(tmp_path, monkeypatch):
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    no_hooks = tmp_path / "no-hooks.json"
    valid.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit",
                            "hooks": [{"command": "run"}, {"command": ""}],
                        },
                        {"hooks": [{"command": "global"}]},
                    ]
                }
            }
        )
    )
    invalid.write_text("bad-json")
    no_hooks.write_text('{"hooks": []}')
    monkeypatch.setattr(
        source,
        "_iter_hook_sources",
        lambda: iter([(valid, "domain"), (invalid, None), (no_hooks, None)]),
    )
    assert source.WorkflowGraphSource().load_hooks() == [
        {
            "event": "PostToolUse",
            "matcher": "Edit",
            "command": "run",
            "domain": "domain",
        },
        {
            "event": "PostToolUse",
            "matcher": "",
            "command": "global",
            "domain": "domain",
        },
    ]


def test_facade_delegates_every_pg_and_jsonl_stream(monkeypatch):
    pg_names = [
        "load_tool_events",
        "load_command_events",
        "load_memories",
        "iter_memories_chunked",
        "load_command_files",
        "load_entities",
        "load_memory_entity_edges",
        "load_memory_associations",
        "load_supersede_edges",
        "load_wiki_pages",
        "load_wiki_links",
        "load_wiki_memory_links",
        "load_wiki_page_sources",
        "load_wiki_session_links",
    ]
    jsonl_names = [
        "load_file_access_events",
        "load_agent_events",
        "load_discussions",
        "load_discussion_tool_uses",
        "load_discussion_agents",
        "load_discussion_commands",
        "load_discussion_files",
        "load_skill_usage",
        "load_mcp_usage",
    ]
    calls = {}

    def install(module, name):
        mock = MagicMock(return_value=[name])
        monkeypatch.setattr(module, name, mock)
        calls[name] = mock

    for name in pg_names:
        install(source._pg, name)
    for name in jsonl_names:
        install(source._jsonl, name)

    facade = source.WorkflowGraphSource()
    store = object()
    assert facade.load_tool_events(store) == [
        "load_tool_events",
        "load_file_access_events",
    ]
    assert facade.load_agent_events() == ["load_agent_events"]
    assert facade.load_command_events(store) == ["load_command_events"]
    assert facade.load_memories(store, min_heat=0.2, limit=3) == ["load_memories"]
    assert facade.iter_memories_chunked(store, min_heat=0.2, chunk_size=4, limit=5) == [
        "iter_memories_chunked"
    ]
    assert facade.load_discussions("ignored") == ["load_discussions"]
    assert facade.load_discussion_tool_uses() == ["load_discussion_tool_uses"]
    assert facade.load_discussion_agents() == ["load_discussion_agents"]
    assert facade.load_discussion_commands() == ["load_discussion_commands"]
    assert facade.load_discussion_files() == ["load_discussion_files"]
    assert facade.load_command_files(store, ["a.py"]) == ["load_command_files"]
    assert facade.load_skill_usage() == ["load_skill_usage"]
    assert facade.load_mcp_usage() == ["load_mcp_usage"]
    assert facade.load_entities(store, min_heat=0.4) == ["load_entities"]
    assert facade.load_memory_entity_edges(store) == ["load_memory_entity_edges"]
    assert facade.load_memory_associations(store, top_k=7) == [
        "load_memory_associations"
    ]
    assert facade.load_supersede_edges(store) == ["load_supersede_edges"]
    assert facade.load_wiki_pages(store) == ["load_wiki_pages"]
    assert facade.load_wiki_links(store) == ["load_wiki_links"]
    assert facade.load_wiki_memory_links(store) == ["load_wiki_memory_links"]
    assert facade.load_wiki_page_sources(store) == ["load_wiki_page_sources"]
    assert facade.load_wiki_session_links(store) == ["load_wiki_session_links"]

    calls["load_memories"].assert_called_once_with(store, min_heat=0.2, limit=3)
    calls["load_entities"].assert_called_once_with(store, min_heat=0.4)
    calls["load_memory_associations"].assert_called_once_with(store, top_k=7)
