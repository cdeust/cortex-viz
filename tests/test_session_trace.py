"""Unit tests for ``core.session_trace`` — the Trace-view P4 residual fix.

Prior to this fix ``build_chain`` minted ``file:<raw literal path>`` FILE
node ids (the exact same disjoint-id-space bug fixed for the live activity
spine in ``core.activity_graph`` / ``core.activity_paths``, but left
un-applied here). This verifies the Trace view now mints the SAME
``file:<hash>`` id the galaxy workflow graph and the live activity spine
mint for the identical file (the join key ``appendGraphDelta`` dedups on
client-side, per ``core.activity_paths``'s module docstring) — while the
human-readable ``path``/``label`` fields Trace's L3 drill and detail panel
rely on (``ui/unified/js/trace_detail.js``) stay a real filesystem path.

``build_chain`` has no persisted storage of its own (unlike
``session_activity``, which needed read-time self-heal for legacy DB rows)
— it recomputes the chain fresh from the JSONL transcript on every request,
so there is nothing to migrate: the id-space fix applies uniformly to
every session, past and present, the moment this module ships.
"""

from __future__ import annotations

from cortex_viz.core.activity_paths import file_target_id
from cortex_viz.core.session_trace import build_chain
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


def _action_event(tool: str, inp: dict, cwd: str = "/Users/dev/repo") -> dict:
    return {"kind": "action", "tool": tool, "input": inp, "ts": "t1", "cwd": cwd}


def _file_nodes(result: dict) -> list[dict]:
    return [n for n in result["nodes"] if n["kind"] == "file"]


def test_file_node_id_matches_galaxy_hash_for_absolute_path():
    events = [_action_event("Read", {"file_path": "/Users/dev/repo/foo.py"})]
    result = build_chain(events, "sess1")
    files = _file_nodes(result)
    assert len(files) == 1
    expected = NodeIdFactory.file_id("/Users/dev/repo/foo.py")
    assert files[0]["id"] == expected


def test_file_node_id_matches_activity_paths_file_target_id():
    events = [_action_event("Edit", {"file_path": "src/bar.py"}, cwd="/Users/dev/repo")]
    result = build_chain(events, "sess1")
    files = _file_nodes(result)
    assert files[0]["id"] == file_target_id("src/bar.py", "/Users/dev/repo")


def test_file_node_label_and_path_stay_human_readable():
    events = [_action_event("Write", {"file_path": "/Users/dev/repo/pkg/mod.py"})]
    result = build_chain(events, "sess1")
    files = _file_nodes(result)
    assert files[0]["label"] == "mod.py"
    assert files[0]["path"] == "/Users/dev/repo/pkg/mod.py"


def test_two_raw_spellings_of_the_same_file_dedup_to_one_canonical_node():
    # "./foo.py" and the absolute equivalent both resolve to the same
    # file under the same cwd — before this fix they minted two disjoint
    # ``file:<raw path>`` nodes; now they must collapse to one.
    events = [
        _action_event("Read", {"file_path": "./foo.py"}, cwd="/Users/dev/repo"),
        _action_event(
            "Edit", {"file_path": "/Users/dev/repo/foo.py"}, cwd="/Users/dev/repo"
        ),
    ]
    result = build_chain(events, "sess1")
    files = _file_nodes(result)
    assert len(files) == 1


def test_file_node_id_from_bash_path_token_also_canonicalizes():
    events = [
        _action_event("Bash", {"command": "cat ./notes.md"}, cwd="/Users/dev/repo")
    ]
    result = build_chain(events, "sess1")
    files = _file_nodes(result)
    assert len(files) == 1
    assert files[0]["id"] == file_target_id("./notes.md", "/Users/dev/repo")
    assert files[0]["path"] == "/Users/dev/repo/notes.md"


def test_action_to_file_edge_targets_the_canonical_id():
    events = [_action_event("Read", {"file_path": "/Users/dev/repo/foo.py"})]
    result = build_chain(events, "sess1")
    file_id = _file_nodes(result)[0]["id"]
    file_edges = [e for e in result["edges"] if e["target"] == file_id]
    assert len(file_edges) == 1
    assert file_edges[0]["kind"] == "read"
