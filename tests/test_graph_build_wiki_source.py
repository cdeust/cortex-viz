"""Unit tests for the post-L6 wiki -> source-file edge resolution
(``server.graph_build_wiki_source.resolve_wiki_source_over_cache``).

Migrated from ``test_workflow_graph_wiki.py``'s ``ingest_wiki_source`` suite:
the resolution logic moved out of the baseline builder into a finalisation
pass over the cumulative cache (VOLET ①, mem 4262203), because a wiki page's
FILE endpoint is only complete after the L6 AST sweep. The contract is
identical — resolve via ``wiki_source_resolve.resolve_file_node_id``, emit an
edge only when BOTH the WIKI node and the resolved FILE node are present, never
fabricate — so the same cases are exercised, now against the ``_node_index``
dict + ``merge`` closure instead of a builder.
"""

from __future__ import annotations

import cortex_viz.core.wiki_source_resolve as resolve_mod
import cortex_viz.infrastructure.wiki_graph as wiki_graph
import cortex_viz.server.graph_cache_state as state
from cortex_viz.core.workflow_graph_schema import NodeIdFactory
from cortex_viz.server.graph_build_wiki_source import (
    resolve_wiki_source_over_cache,
)


def _capture_merge():
    """Return (merge, captured) where ``captured["edges"]`` accumulates
    every edge submitted to merge — the real merge's dedup/SSE side is
    irrelevant to this unit."""
    captured: dict = {"edges": []}

    def merge(new_nodes, new_edges, **_kw):
        captured["edges"].extend(new_edges)

    return merge, captured


def _seed_index(nodes: dict) -> None:
    state._node_index.clear()
    state._node_index.update(nodes)


def _set_sources(monkeypatch, rows, resolver):
    monkeypatch.setattr(wiki_graph, "load_wiki_page_sources", lambda store: rows)
    monkeypatch.setattr(resolve_mod, "resolve_file_node_id", resolver)


def teardown_function() -> None:
    state._node_index.clear()


def test_edge_emitted_when_resolved_and_present(monkeypatch):
    page_id = NodeIdFactory.wiki_id(1)
    file_id = NodeIdFactory.file_id("/repo/cortex/foo.py")
    _seed_index({page_id: {"domain_id": "domain:cortex"}, file_id: {"kind": "file"}})
    _set_sources(
        monkeypatch,
        [
            {
                "page_id": 1,
                "source_path": "foo.py",
                "link_kind": "documents",
                "confidence": 1.0,
            }
        ],
        lambda domain_id, source_path: file_id,
    )
    merge, captured = _capture_merge()
    count = resolve_wiki_source_over_cache(object(), merge=merge)
    assert count == 1
    assert len(captured["edges"]) == 1
    edge = captured["edges"][0]
    assert edge["source"] == page_id
    assert edge["target"] == file_id
    assert edge["kind"] == "wiki_source"
    assert edge["type"] == "wiki_source"
    assert edge["label"] == "documents"
    assert edge["confidence"] == 1.0
    assert edge["reason"] == "wiki-source"


def test_missing_page_node_dropped(monkeypatch):
    _seed_index({})
    _set_sources(
        monkeypatch,
        [{"page_id": 1, "source_path": "foo.py"}],
        lambda domain_id, source_path: "file:whatever",
    )
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 0
    assert captured["edges"] == []


def test_unresolved_path_dropped(monkeypatch):
    page_id = NodeIdFactory.wiki_id(1)
    _seed_index({page_id: {"domain_id": "domain:cortex"}})
    _set_sources(
        monkeypatch,
        [{"page_id": 1, "source_path": "foo.py"}],
        lambda domain_id, source_path: None,
    )
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 0
    assert captured["edges"] == []


def test_resolved_but_file_node_absent_dropped(monkeypatch):
    """No fabricated node: resolution yields a candidate id, but if no FILE
    node with that id is in the cache, no edge is drawn."""
    page_id = NodeIdFactory.wiki_id(1)
    _seed_index({page_id: {"domain_id": "domain:cortex"}})
    _set_sources(
        monkeypatch,
        [{"page_id": 1, "source_path": "foo.py"}],
        lambda domain_id, source_path: "file:not-in-graph",
    )
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 0
    assert captured["edges"] == []


def test_none_ids_skipped(monkeypatch):
    _seed_index({NodeIdFactory.wiki_id(1): {"domain_id": "domain:cortex"}})
    _set_sources(
        monkeypatch,
        [
            {"page_id": None, "source_path": "foo.py"},
            {"page_id": 1, "source_path": None},
        ],
        lambda domain_id, source_path: "file:x",
    )
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 0
    assert captured["edges"] == []


def test_no_rows_returns_zero(monkeypatch):
    _seed_index({})
    monkeypatch.setattr(wiki_graph, "load_wiki_page_sources", lambda store: [])
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 0
    assert captured["edges"] == []


def test_absolute_keying_closes_the_sibling_gap(monkeypatch, tmp_path):
    """End-to-end proof of VOLET ① (mem 4262203) with the REAL resolver.

    The bug: an AST-only file (indexed by AP, never touched by a Claude tool)
    was keyed by its repo-RELATIVE path in the graph, while the wiki resolver
    reconstructs the ABSOLUTE path — so the ids never matched and the wiki page
    could not link to the file. The fix keys AST files by the absolute path
    (L6's ``absolutize``), the SAME scheme the resolver uses.

    This drives the actual ``resolve_file_node_id`` + ``file_id`` + ``absolutize``
    (only the DB and the repo registry are stubbed) and asserts:
      * the ABSOLUTE-keyed AST node (post-fix) resolves an edge;
      * a RELATIVE-keyed AST node (pre-fix) does NOT — i.e. absolute keying is
        precisely what closes the gap.
    """
    from cortex_viz.core.workflow_graph_schema import NodeIdFactory
    from cortex_viz.infrastructure.ap_graph_root import absolutize

    # A real repo on disk with a sibling source file AP would index.
    repo = tmp_path / "cortex-viz"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "sibling.py").write_text("x = 1\n")
    rel = "pkg/sibling.py"

    # The registry resolves domain:cortex to this repo (real _select_root then
    # picks it by filesystem presence).
    monkeypatch.setattr(
        resolve_mod, "source_roots_for_domain", lambda canonical: [str(repo)]
    )
    monkeypatch.setattr(
        wiki_graph,
        "load_wiki_page_sources",
        lambda store: [{"page_id": 1, "source_path": rel, "link_kind": "documents"}],
    )

    wiki_id = NodeIdFactory.wiki_id(1)
    abs_fid = NodeIdFactory.file_id(absolutize(str(repo), rel))  # post-fix key
    rel_fid = NodeIdFactory.file_id(rel)  # pre-fix (buggy) key
    assert abs_fid != rel_fid  # the whole point

    # Post-fix: L6 keyed the AST file by its ABSOLUTE path → edge resolves.
    _seed_index({wiki_id: {"domain_id": "domain:cortex"}, abs_fid: {"kind": "file"}})
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 1
    assert captured["edges"][0]["target"] == abs_fid

    # Pre-fix: the SAME file keyed by its RELATIVE path → NO edge (the gap).
    _seed_index({wiki_id: {"domain_id": "domain:cortex"}, rel_fid: {"kind": "file"}})
    merge, captured = _capture_merge()
    assert resolve_wiki_source_over_cache(object(), merge=merge) == 0


def test_optional_fields_omitted_when_absent(monkeypatch):
    """label/confidence are omitted (not None) when the row lacks them, so
    _edge_to_dict's exclude_none contract is matched exactly."""
    page_id = NodeIdFactory.wiki_id(2)
    file_id = NodeIdFactory.file_id("/repo/cortex/bar.py")
    _seed_index({page_id: {"domain_id": "domain:cortex"}, file_id: {"kind": "file"}})
    _set_sources(
        monkeypatch,
        [{"page_id": 2, "source_path": "bar.py"}],
        lambda domain_id, source_path: file_id,
    )
    merge, captured = _capture_merge()
    resolve_wiki_source_over_cache(object(), merge=merge)
    edge = captured["edges"][0]
    assert "label" not in edge
    assert "confidence" not in edge
