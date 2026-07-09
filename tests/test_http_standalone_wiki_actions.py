"""Endpoint-orchestration tests for ``GET /api/wiki/actions``
(``server.http_standalone_wiki._page_actions``) — the three degrade-
gracefully cases the task requires (page with sources+activity, page with
no sources/no activity, page that doesn't exist) plus a malformed
``page_id``. PG I/O is monkeypatched at the infra-function boundary (the
established pattern for these thin wrappers, mirroring the rest of the
``wiki_*`` infra modules, none of which mock a live psycopg connection);
what's under test is ``_page_actions``'s orchestration and its use of the
Task-A path-unified join (``core.wiki_page_actions``).
"""

from __future__ import annotations

import cortex_viz.infrastructure.activity_store as activity_store_mod
import cortex_viz.infrastructure.wiki_page_actions_pg as pg_mod
import cortex_viz.core.wiki_source_resolve as resolve_mod
from cortex_viz.core.activity_paths import file_target_id
from cortex_viz.server.http_standalone_wiki import _page_actions


def test_page_not_found_degrades_to_empty_valid_shape(monkeypatch):
    monkeypatch.setattr(pg_mod, "load_page_by_id", lambda store, pid: None)
    got = _page_actions(store=None, params={"page_id": "999"})
    assert got["ok"] is True
    assert got["page_id"] == 999
    assert got["sources"] == []
    assert got["actions"] == []
    assert got["count"] == 0
    assert got["note"] == "page_not_found"


def test_missing_page_id_degrades_to_empty_valid_shape():
    got = _page_actions(store=None, params={})
    assert got["ok"] is True
    assert got["page_id"] is None
    assert got["actions"] == []
    assert got["note"] == "missing_or_invalid_page_id"


def test_page_with_no_sources_degrades_to_empty_actions(monkeypatch):
    monkeypatch.setattr(
        pg_mod, "load_page_by_id",
        lambda store, pid: {"id": pid, "domain": "cortex", "rel_path": "adr/0001.md"},
    )
    monkeypatch.setattr(pg_mod, "load_page_sources", lambda store, pid: [])
    got = _page_actions(store=None, params={"page_id": "7"})
    assert got["ok"] is True
    assert got["rel_path"] == "adr/0001.md"
    assert got["sources"] == []
    assert got["actions"] == []
    assert got["count"] == 0


def test_page_with_sources_but_no_matching_activity(monkeypatch):
    monkeypatch.setattr(
        pg_mod, "load_page_by_id",
        lambda store, pid: {"id": pid, "domain": "cortex", "rel_path": "adr/0002.md"},
    )
    monkeypatch.setattr(
        pg_mod, "load_page_sources",
        lambda store, pid: [{"source_path": "foo.py", "link_kind": "documents", "confidence": 1.0}],
    )
    monkeypatch.setattr(resolve_mod, "_project_source_root", lambda c: "/repo/cortex")
    monkeypatch.setattr(activity_store_mod, "find_by_target_ids", lambda store, ids, limit=2000: [])
    monkeypatch.setattr(activity_store_mod, "scan_legacy_file_rows", lambda store, limit=2000: [])
    got = _page_actions(store=None, params={"page_id": "7"})
    assert got["sources"] == [{"source_path": "foo.py", "link_kind": "documents", "resolved": True}]
    assert got["actions"] == []
    assert got["count"] == 0


def test_page_with_sources_and_matching_activity(monkeypatch):
    monkeypatch.setattr(
        pg_mod, "load_page_by_id",
        lambda store, pid: {"id": pid, "domain": "cortex", "rel_path": "adr/0003.md"},
    )
    monkeypatch.setattr(
        pg_mod, "load_page_sources",
        lambda store, pid: [{"source_path": "foo.py", "link_kind": "documents", "confidence": 1.0}],
    )
    monkeypatch.setattr(resolve_mod, "_project_source_root", lambda c: "/repo/cortex")
    tid = file_target_id("/repo/cortex/foo.py", cwd="")
    monkeypatch.setattr(
        activity_store_mod, "find_by_target_ids",
        lambda store, ids, limit=2000: [
            {"id": 42, "session_id": "s1", "ts": 100.0, "tool": "Edit", "action": "edit",
             "target_id": tid, "target_kind": "file", "target_label": "foo.py",
             "edge_kind": "edit", "cwd": ""},
        ],
    )
    monkeypatch.setattr(activity_store_mod, "scan_legacy_file_rows", lambda store, limit=2000: [])
    got = _page_actions(store=None, params={"page_id": "7"})
    assert got["count"] == 1
    assert got["actions"][0]["id"] == 42
    assert got["actions"][0]["source_path"] == "foo.py"
    assert got["actions"][0]["tool"] == "Edit"
    assert got["truncated"] is False


def test_limit_is_bounded_and_reports_truncation(monkeypatch):
    monkeypatch.setattr(
        pg_mod, "load_page_by_id",
        lambda store, pid: {"id": pid, "domain": "cortex", "rel_path": "adr/0004.md"},
    )
    monkeypatch.setattr(
        pg_mod, "load_page_sources",
        lambda store, pid: [{"source_path": "foo.py", "link_kind": "documents", "confidence": 1.0}],
    )
    monkeypatch.setattr(resolve_mod, "_project_source_root", lambda c: "/repo/cortex")
    tid = file_target_id("/repo/cortex/foo.py", cwd="")
    rows = [
        {"id": i, "session_id": "s1", "ts": float(i), "tool": "Edit", "action": "edit",
         "target_id": tid, "target_kind": "file", "target_label": "foo.py",
         "edge_kind": "edit", "cwd": ""}
        for i in range(5)
    ]
    monkeypatch.setattr(activity_store_mod, "find_by_target_ids", lambda store, ids, limit=2000: rows)
    monkeypatch.setattr(activity_store_mod, "scan_legacy_file_rows", lambda store, limit=2000: [])
    got = _page_actions(store=None, params={"page_id": "7", "limit": "2"})
    assert got["count"] == 2
    assert got["truncated"] is True
    assert got["actions"][0]["id"] == 4  # newest-first
