"""Unit tests for ``core.wiki_page_actions`` — the page->source-file->
live-activity join behind ``GET /api/wiki/actions``.
"""

from __future__ import annotations

import cortex_viz.core.wiki_page_actions as mod
from cortex_viz.core.activity_paths import file_target_id
from cortex_viz.core.workflow_graph_schema import NodeIdFactory


def test_resolve_source_target_ids_matches_wiki_source_resolve(monkeypatch):
    # Boundary: resolve_source_target_ids delegates the source_path ->
    # FILE-node-id reconstruction entirely to resolve_file_node_id (the
    # sole public export of core.wiki_source_resolve post PR#11). Patching
    # that boundary -- rather than the resolver's private root lookup --
    # proves resolve_source_target_ids' own join/dict-building logic
    # without coupling this test to the resolver's internals.
    monkeypatch.setattr(
        mod,
        "resolve_file_node_id",
        lambda domain_id, source_path: NodeIdFactory.file_id(f"/repo/cortex/{source_path}")
        if domain_id == "domain:cortex"
        else None,
    )
    sources = [
        {"source_path": "foo.py", "link_kind": "documents"},
        {"source_path": "bar.py", "link_kind": "references"},
    ]
    got = mod.resolve_source_target_ids("domain:cortex", sources)
    assert got == {
        "foo.py": NodeIdFactory.file_id("/repo/cortex/foo.py"),
        "bar.py": NodeIdFactory.file_id("/repo/cortex/bar.py"),
    }


def test_resolve_source_target_ids_drops_unresolvable_domain(monkeypatch):
    # resolve_file_node_id returning None (e.g. a memory-only domain with
    # no checked-out repo) must drop the source rather than fabricate an id.
    monkeypatch.setattr(mod, "resolve_file_node_id", lambda domain_id, source_path: None)
    got = mod.resolve_source_target_ids(
        "domain:no-repo", [{"source_path": "foo.py"}]
    )
    assert got == {}


def test_match_activity_rows_canonical_fast_path():
    tid = file_target_id("/repo/cortex/foo.py", cwd="")
    target_map = {"foo.py": tid}
    canonical_rows = [
        {"id": 5, "target_id": tid, "target_kind": "file", "cwd": ""},
        {"id": 3, "target_id": "file:unrelated0", "target_kind": "file", "cwd": ""},
    ]
    matched = mod.match_activity_rows(target_map, canonical_rows, [])
    assert len(matched) == 1
    assert matched[0]["id"] == 5
    assert matched[0]["source_path"] == "foo.py"


def test_match_activity_rows_legacy_row_self_heals_and_matches():
    tid = file_target_id("/repo/cortex/foo.py", cwd="/repo/cortex")
    target_map = {"foo.py": tid}
    legacy_rows = [
        {"id": 1, "target_id": "file:/repo/cortex/foo.py", "target_kind": "file",
         "cwd": "/repo/cortex"},
    ]
    matched = mod.match_activity_rows(target_map, [], legacy_rows)
    assert len(matched) == 1
    assert matched[0]["target_id"] == tid
    assert matched[0]["source_path"] == "foo.py"


def test_match_activity_rows_legacy_row_that_does_not_resolve_is_skipped():
    target_map = {"foo.py": file_target_id("/repo/cortex/foo.py", cwd="")}
    legacy_rows = [
        {"id": 1, "target_id": "file:/repo/other/unrelated.py", "target_kind": "file",
         "cwd": ""},
    ]
    assert mod.match_activity_rows(target_map, [], legacy_rows) == []


def test_match_activity_rows_sorted_newest_first():
    tid = file_target_id("/repo/cortex/foo.py", cwd="")
    target_map = {"foo.py": tid}
    canonical_rows = [
        {"id": 1, "target_id": tid, "target_kind": "file", "cwd": ""},
        {"id": 9, "target_id": tid, "target_kind": "file", "cwd": ""},
        {"id": 4, "target_id": tid, "target_kind": "file", "cwd": ""},
    ]
    matched = mod.match_activity_rows(target_map, canonical_rows, [])
    assert [r["id"] for r in matched] == [9, 4, 1]


def test_match_activity_rows_no_sources_no_activity_yields_empty_list():
    assert mod.match_activity_rows({}, [], []) == []
