"""P4 node-unification tests for ``core.activity_graph`` — the live
session-activity spine's FILE target ids must equal the galaxy workflow
graph's ``NodeIdFactory.file_id`` id for the same file, for absolute,
``~``-prefixed, and cwd-relative raw inputs, and legacy pre-fix rows must
self-heal to the same id in ``event_to_graph`` (the SSE replay path).
"""

from __future__ import annotations

from cortex_viz.core.activity_graph import classify, event_to_graph, normalize_event
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


def test_read_absolute_path_mints_galaxy_hash_id():
    c = classify("Read", {"file_path": "/Users/dev/repo/foo.py"}, "/Users/dev/repo")
    assert c["target_id"] == NodeIdFactory.file_id("/Users/dev/repo/foo.py")


def test_edit_relative_path_resolves_against_cwd_before_hashing():
    c = classify("Edit", {"file_path": "src/foo.py"}, "/Users/dev/repo")
    assert c["target_id"] == NodeIdFactory.file_id("/Users/dev/repo/src/foo.py")


def test_write_tilde_path_expands_before_hashing(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/dev")
    c = classify("Write", {"file_path": "~/scratch.md"}, "/Users/dev/repo")
    assert c["target_id"] == NodeIdFactory.file_id("/Users/dev/scratch.md")


def test_bash_command_touched_path_is_canonicalized_and_hashed():
    c = classify("Bash", {"command": "cat ./src/foo.py"}, "/Users/dev/repo")
    assert c["command_path"] == "/Users/dev/repo/src/foo.py"


def test_missing_path_falls_back_to_degenerate_id_not_a_crash():
    c = classify("Read", {}, "/Users/dev/repo")
    assert c["target_id"] == "file:?"


def test_normalize_event_carries_canonical_path_in_detail():
    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/Users/dev/repo/foo.py"},
        "cwd": "/Users/dev/repo",
        "session_id": "s1",
        "ts": 1.0,
    }
    row = normalize_event(event)
    assert row["target_id"] == NodeIdFactory.file_id("/Users/dev/repo/foo.py")
    assert row["detail"]["path"] == "/Users/dev/repo/foo.py"


def test_event_to_graph_self_heals_legacy_raw_path_target_id():
    # A row written BEFORE the P4 fix shipped: target_id embeds the raw path
    # literally instead of the hash.
    legacy_row = {
        "session_id": "s1", "seq": 1, "action": "edit", "tool": "Edit",
        "target_id": "file:/Users/dev/repo/foo.py", "target_kind": "file",
        "target_label": "foo.py", "edge_kind": "edit",
        "cwd": "/Users/dev/repo", "detail": {},
    }
    frag = event_to_graph(legacy_row)
    file_nodes = [n for n in frag["nodes"] if n["kind"] == "file"]
    assert len(file_nodes) == 1
    assert file_nodes[0]["id"] == NodeIdFactory.file_id("/Users/dev/repo/foo.py")


def test_event_to_graph_leaves_fresh_canonical_target_id_untouched():
    canonical_id = NodeIdFactory.file_id("/Users/dev/repo/foo.py")
    fresh_row = {
        "session_id": "s1", "seq": 1, "action": "edit", "tool": "Edit",
        "target_id": canonical_id, "target_kind": "file",
        "target_label": "foo.py", "edge_kind": "edit",
        "cwd": "/Users/dev/repo", "detail": {"path": "/Users/dev/repo/foo.py"},
    }
    frag = event_to_graph(fresh_row)
    file_nodes = [n for n in frag["nodes"] if n["kind"] == "file"]
    assert file_nodes[0]["id"] == canonical_id


def test_event_to_graph_legacy_and_fresh_row_for_same_file_produce_same_id():
    legacy_row = {
        "session_id": "s1", "seq": 1, "action": "read", "tool": "Read",
        "target_id": "file:/Users/dev/repo/foo.py", "target_kind": "file",
        "target_label": "foo.py", "edge_kind": "read",
        "cwd": "/Users/dev/repo", "detail": {},
    }
    fresh_row = {
        "session_id": "s1", "seq": 2, "action": "edit", "tool": "Edit",
        "target_id": NodeIdFactory.file_id("/Users/dev/repo/foo.py"),
        "target_kind": "file", "target_label": "foo.py", "edge_kind": "edit",
        "cwd": "/Users/dev/repo", "detail": {"path": "/Users/dev/repo/foo.py"},
    }
    legacy_fid = [n["id"] for n in event_to_graph(legacy_row)["nodes"] if n["kind"] == "file"][0]
    fresh_fid = [n["id"] for n in event_to_graph(fresh_row)["nodes"] if n["kind"] == "file"][0]
    assert legacy_fid == fresh_fid


def test_event_to_graph_command_path_edge_matches_galaxy_hash():
    row = {
        "session_id": "s1", "seq": 1, "action": "run", "tool": "Bash",
        "target_id": "cmd:cat foo.py", "target_kind": "command",
        "target_label": "cat foo.py", "edge_kind": "run",
        "cwd": "/Users/dev/repo", "detail": {"command_path": "/Users/dev/repo/foo.py"},
    }
    frag = event_to_graph(row)
    file_nodes = [n for n in frag["nodes"] if n["kind"] == "file"]
    assert file_nodes[0]["id"] == NodeIdFactory.file_id("/Users/dev/repo/foo.py")
