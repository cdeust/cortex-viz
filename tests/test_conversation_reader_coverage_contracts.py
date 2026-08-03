"""Behavioral contracts for JSONL conversation loading and formatting."""

from __future__ import annotations

from pathlib import Path

from cortex_viz.infrastructure import conversation_reader as conversations


def test_read_full_conversation_streams_valid_dicts_and_keeps_json_values(tmp_path):
    transcript = tmp_path / "conversation.jsonl"
    transcript.write_text(
        '\n{"type":"user","message":{"content":"hello"}}\n'
        "partial-json\n"
        '[1,2,3]\n{"type":"assistant","message":{"content":[]}}\n'
    )
    assert conversations.read_full_conversation(transcript) == [
        {"type": "user", "message": {"content": "hello"}},
        [1, 2, 3],
        {"type": "assistant", "message": {"content": []}},
    ]
    assert conversations.read_full_conversation(tmp_path / "missing") == []


def test_read_full_conversation_fails_open_on_oserror(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda _self: True)
    monkeypatch.setattr(
        "builtins.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    assert conversations.read_full_conversation("/denied") == []


def test_content_and_tool_extractors_cover_supported_shapes():
    assert conversations._extract_text("plain") == "plain"
    assert (
        conversations._extract_text(
            [
                {"type": "text", "text": "one"},
                "two",
                {"type": "image", "data": "ignored"},
            ]
        )
        == "one two"
    )
    assert conversations._extract_text({"text": "unsupported"}) == ""
    assert conversations._extract_tool_calls("not-a-list") == []
    assert conversations._extract_tool_calls(
        [
            {
                "type": "tool_use",
                "name": "Read",
                "input": {"path": "x"},
                "output": None,
            },
            {"type": "text", "text": "ignored"},
        ]
    ) == [{"name": "Read", "input": "{'path': 'x'}", "output": "None"}]


def test_skippable_records_cover_metadata_and_permission_events():
    assert conversations._is_skippable({"type": "system"})
    assert conversations._is_skippable({"isMeta": True})
    assert conversations._is_skippable({"type": "user", "toolUseResult": {}}) is False
    assert conversations._is_skippable({"type": "user", "toolUseResult": {"ok": 1}})
    assert conversations._is_skippable({"permissionMode": "ask"})
    assert not conversations._is_skippable({"type": "user"})


def test_format_conversation_messages_filters_and_normalizes_records():
    raw = [
        {"type": "system", "message": {"content": "hidden"}},
        {"type": "user", "isMeta": True, "message": {"content": "hidden"}},
        {"type": "progress", "message": {"content": "hidden"}},
        {"type": "user", "message": {}},
        {
            "type": "user",
            "timestamp": "t1",
            "message": {"content": "question"},
        },
        {
            "type": "assistant",
            "timestamp": "t2",
            "message": {
                "content": [
                    {"type": "text", "text": "answer"},
                    {"type": "tool_use", "name": "Read", "input": "x"},
                ]
            },
        },
    ]
    assert conversations.format_conversation_messages(raw) == [
        {
            "role": "user",
            "text": "question",
            "timestamp": "t1",
            "toolCalls": [],
        },
        {
            "role": "assistant",
            "text": "answer",
            "timestamp": "t2",
            "toolCalls": [{"name": "Read", "input": "x", "output": ""}],
        },
    ]
