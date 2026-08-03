"""Behavioral contracts for filesystem, scanner parsing, and graph LOD."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_viz.infrastructure import file_io, scanner_parse
from cortex_viz.server import layout_authority_lod as lod
from cortex_viz.server.layout_authority_protocol import NodeDelta


def test_json_and_text_file_round_trips(tmp_path, capsys):
    nested = tmp_path / "nested" / "record.json"
    payload = {"unicode": "mémoire", "items": [1, 2]}
    file_io.write_json(nested, payload)
    assert file_io.read_json(nested) == payload
    assert "mémoire" in nested.read_text()

    text_path = tmp_path / "note.txt"
    text_path.write_text("hello", encoding="utf-8")
    assert file_io.read_text_file(text_path) == "hello"
    assert file_io.read_text_file(tmp_path / "missing.txt") is None
    assert file_io.read_json(tmp_path / "missing.json") is None
    assert capsys.readouterr().err == ""


def test_file_helpers_fail_open_and_report_parse_errors(tmp_path, capsys, monkeypatch):
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert file_io.read_json(broken) is None
    assert "Failed to read" in capsys.readouterr().err

    directory = tmp_path / "tree" / "leaf"
    file_io.ensure_dir(directory)
    (directory / "a.txt").write_text("a")
    (directory / "b.txt").write_text("b")
    assert set(file_io.list_dir(directory) or []) == {"a.txt", "b.txt"}
    typed = file_io.list_dir(directory, with_file_types=True)
    assert typed is not None and {p.name for p in typed} == {"a.txt", "b.txt"}
    assert file_io.list_dir(tmp_path / "absent") is None
    assert file_io.stat_file(directory / "a.txt").st_size == 1
    assert file_io.stat_file(tmp_path / "absent") is None

    monkeypatch.setattr(
        Path, "read_text", lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied"))
    )
    assert file_io.read_text_file(directory / "a.txt") is None
    assert "denied" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("plain", "plain"),
        (
            [
                {"type": "text", "text": "one"},
                {"type": "image", "text": "skip"},
                {"type": "text", "text": "two"},
            ],
            "one two",
        ),
        ({"type": "text", "text": "not-a-list"}, ""),
        (None, ""),
    ],
)
def test_extract_user_text(content, expected):
    assert scanner_parse.extract_user_text(content) == expected


def test_extract_metadata_fields_uses_first_identity_and_timestamp_bounds():
    records = [
        {"timestamp": "2026-01-02T00:00:00Z", "sessionId": "s1", "slug": "first"},
        {"timestamp": "2026-01-01T00:00:00Z", "sessionId": "s2", "cwd": "/repo"},
        {"timestamp": "2026-01-03T00:00:00Z", "cwd": "/ignored"},
    ]
    assert scanner_parse.extract_metadata_fields(records) == {
        "session_id": "s1",
        "slug": "first",
        "cwd": "/repo",
        "first_timestamp": "2026-01-01T00:00:00Z",
        "last_timestamp": "2026-01-03T00:00:00Z",
    }
    assert scanner_parse.extract_metadata_fields([])["session_id"] is None


def test_extract_message_stats_filters_meta_results_and_interrupts():
    long_text = "x" * 4_050
    records = [
        {"type": "user", "message": {"content": "[Request interrupted by user]"}},
        {"type": "user", "isMeta": True, "message": {"content": "meta"}},
        {
            "type": "user",
            "toolUseResult": {"ok": True},
            "message": {"content": "result"},
        },
        {"type": "user", "message": {"content": long_text}},
        {"type": "user", "message": {"content": "ignored after cap"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Read"},
                    {"type": "text", "text": "answer"},
                ]
            },
        },
        {"type": "assistant", "message": {"content": "plain"}},
    ]
    stats = scanner_parse.extract_message_stats(records)
    assert stats["user_count"] == 5
    assert stats["assistant_count"] == 2
    assert stats["tools_used"] == {"Read"}
    assert stats["first_message"] == long_text
    assert stats["all_text"] == "x" * 4_000


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:01.250Z", 1_250),
        (None, "2026-01-01T00:00:01Z", None),
        ("bad", "also-bad", None),
    ],
)
def test_compute_duration(first, last, expected):
    assert scanner_parse.compute_duration(first, last) == expected


def test_build_conversation_record_assembles_measured_fields(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("12345")
    monkeypatch.setattr(scanner_parse, "extract_keywords", lambda text: [text[:3]])
    meta = {
        "session_id": None,
        "slug": "slug",
        "cwd": "/repo",
        "first_timestamp": "2026-01-01T00:00:00Z",
        "last_timestamp": "2026-01-01T00:00:02Z",
    }
    stats = {
        "all_text": "question",
        "first_message": "question",
        "user_count": 2,
        "assistant_count": 3,
        "tools_used": {"Read", "Bash"},
    }
    record = scanner_parse.build_conversation_record(
        meta, stats, transcript, "demo", "fallback"
    )
    assert record["sessionId"] == "fallback"
    assert record["messageCount"] == 5
    assert record["turnCount"] == 3
    assert set(record["toolsUsed"]) == {"Read", "Bash"}
    assert record["keywords"] == ["que"]
    assert record["duration"] == 2_000
    assert record["fileSize"] == 5

    missing = scanner_parse.build_conversation_record(
        meta, stats, tmp_path / "none", "demo", "fallback"
    )
    assert missing["fileSize"] is None


@pytest.mark.parametrize(
    ("zoom", "expected"),
    [(-1.0, 8), (0.0, 8), (0.25, 4), (0.5, 2), (0.75, 1), (1.0, 1), (2.0, 1)],
)
def test_lod_stride_contract(zoom, expected):
    assert lod.stride(zoom) == expected


def _id_for_remainder(modulus: int, remainder: int) -> str:
    return next(
        node_id
        for i in range(1_000)
        if lod._stable_hash(node_id := f"node:{i}") % modulus == remainder
    )


def test_lod_visibility_by_kind_and_zoom():
    assert lod.visible_at_zoom("domain:1", "domain", 0.0)
    assert lod.visible_at_zoom("future:1", "future-kind", 0.0)
    assert lod.visible_at_zoom("symbol:1", "symbol", 1.0)

    even = _id_for_remainder(2, 0)
    odd = _id_for_remainder(2, 1)
    assert lod.visible_at_zoom(even, "symbol", 0.5)
    assert not lod.visible_at_zoom(odd, "symbol", 0.5)
    assert lod.visible_at_zoom(odd, "memory", 0.4)
    assert not lod.visible_at_zoom(odd, "memory", 0.39)
    assert lod.visible_at_zoom(even, "entity", 0.0)


def test_lod_visible_subset_is_streaming_and_selfcheck_is_structured():
    kept_symbol = _id_for_remainder(8, 0)
    dropped_symbol = _id_for_remainder(8, 1)
    nodes = [
        NodeDelta("domain", "domain", "domain"),
        NodeDelta(kept_symbol, "symbol", "domain", parent_id="file"),
        NodeDelta(dropped_symbol, "symbol", "domain", parent_id="file"),
    ]
    subset = lod.visible_subset(nodes, 0.0)
    assert iter(subset) is subset
    assert [node.node_id for node in subset] == ["domain", kept_symbol]

    rows = lod._selfcheck_powerlaw(n_symbols=128)
    assert [row[:2] for row in rows] == [
        (0.0, 8),
        (0.25, 4),
        (0.5, 2),
        (0.75, 1),
        (1.0, 1),
    ]
    assert all(visible >= 0 and ratio >= 0 for _, _, visible, ratio in rows)
