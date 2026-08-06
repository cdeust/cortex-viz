"""The wiki lens's documentation graph (``GET /api/wiki/graph``).

Covers the pure builder, the toggle parsing, the loader added for cross-lens,
and the dispatch arm — including the ``unavailable`` marker that makes a
not-served op distinguishable from an empty result (#119).
"""

from __future__ import annotations

import json

import pytest

from cortex_viz.core.wiki_doc_graph import build_doc_graph
from cortex_viz.infrastructure import wiki_graph
from cortex_viz.server import http_standalone_wiki as wiki_http

_DEFAULT = object()


def _page(pg_id: int, *, domain: str = "cortex", rel_path=_DEFAULT, **extra):
    """A ``wiki.pages`` row. ``rel_path=""`` means genuinely pathless — the
    sentinel is what distinguishes that from "not specified", and using a falsy
    default here instead silently rewrote the empty case away."""
    page = {
        "id": pg_id,
        "title": f"Page {pg_id}",
        "kind": "note",
        "domain": domain,
        "status": "live",
        "heat": 0.0,
        "rel_path": f"p{pg_id}.md" if rel_path is _DEFAULT else rel_path,
        "memory_id": None,
    }
    page.update(extra)
    return page


def _memory(pg_id: int, *, domain: str = "cortex"):
    return {
        "id": pg_id,
        "content": f"memory {pg_id}",
        "domain": domain,
        "heat": 0.0,
        "consolidation_stage": "semantic",
    }


def _build(**overrides):
    args = {
        "pages": [],
        "links": [],
        "memory_links": [],
        "memories": [],
        "tags_by_path": {},
    }
    args.update(overrides)
    return build_doc_graph(**args)


def _kinds(payload, kind):
    return [node["id"] for node in payload["nodes"] if node["kind"] == kind]


def _edges(payload, kind):
    return [(e["source"], e["target"]) for e in payload["edges"] if e["kind"] == kind]


def test_authored_links_are_the_graph_and_need_no_toggle():
    payload = _build(
        pages=[_page(1), _page(2)],
        links=[{"src_page_id": 1, "dst_page_id": 2, "link_kind": "documents"}],
    )

    assert _kinds(payload, "wiki") == ["wiki:1", "wiki:2"]
    assert _edges(payload, "wiki_links") == [("wiki:1", "wiki:2")]


def test_a_link_to_a_page_outside_the_selection_is_dropped():
    """A dangling endpoint would render as an edge into nothing."""
    payload = _build(
        pages=[_page(1)],
        links=[{"src_page_id": 1, "dst_page_id": 99, "link_kind": "documents"}],
    )

    assert _edges(payload, "wiki_links") == []


def test_the_domain_filter_selects_only_that_domains_pages():
    payload = _build(
        pages=[_page(1, domain="cortex"), _page(2, domain="other")],
        domain="cortex",
    )

    assert _kinds(payload, "wiki") == ["wiki:1"]


def test_an_absent_domain_selects_every_page():
    payload = _build(pages=[_page(1, domain="cortex"), _page(2, domain="other")])

    assert _kinds(payload, "wiki") == ["wiki:1", "wiki:2"]


def test_cross_lens_brings_in_the_memory_a_page_was_written_from():
    payload = _build(
        pages=[_page(1)],
        memory_links=[{"page_id": 1, "memory_id": 7}],
        memories=[_memory(7)],
        cross_lens=True,
    )

    assert _kinds(payload, "memory") == ["memory:7"]
    assert _edges(payload, "documents") == [("wiki:1", "memory:7")]


def test_cross_lens_off_leaves_out_both_the_memory_and_its_edge():
    payload = _build(
        pages=[_page(1)],
        memory_links=[{"page_id": 1, "memory_id": 7}],
        memories=[_memory(7)],
        cross_lens=False,
    )

    assert _kinds(payload, "memory") == []
    assert _edges(payload, "documents") == []


def test_a_memory_link_whose_row_was_not_loaded_adds_no_dangling_edge():
    """`ingest_wiki_memory` skips a missing endpoint; assert the skip, because
    silently dropping the edge is the difference between an honest empty
    cross-lens and one that looks populated."""
    payload = _build(
        pages=[_page(1)],
        memory_links=[{"page_id": 1, "memory_id": 7}],
        memories=[],
        cross_lens=True,
    )

    assert _kinds(payload, "memory") == []
    assert _edges(payload, "documents") == []


def test_co_occurrence_links_pages_that_share_a_tag():
    payload = _build(
        pages=[_page(1, rel_path="a.md"), _page(2, rel_path="b.md")],
        tags_by_path={"a.md": ["retrieval", "adr"], "b.md": ["adr"]},
        co_occurrence=True,
    )

    assert _edges(payload, "associates_with") == [("wiki:1", "wiki:2")]
    label = next(e["label"] for e in payload["edges"] if e["kind"] == "associates_with")
    assert label == "adr"


def test_co_occurrence_is_off_by_default():
    payload = _build(
        pages=[_page(1, rel_path="a.md"), _page(2, rel_path="b.md")],
        tags_by_path={"a.md": ["adr"], "b.md": ["adr"]},
    )

    assert _edges(payload, "associates_with") == []


def test_pages_sharing_no_tag_are_not_linked():
    payload = _build(
        pages=[_page(1, rel_path="a.md"), _page(2, rel_path="b.md")],
        tags_by_path={"a.md": ["retrieval"], "b.md": ["layout"]},
        co_occurrence=True,
    )

    assert _edges(payload, "associates_with") == []


def test_a_co_occurring_pair_yields_one_edge_with_the_lower_id_first():
    """`EdgeKind.ASSOCIATES_WITH` is undirected with source < target, so the
    same pair must never produce two differently-oriented edges."""
    payload = _build(
        pages=[_page(2, rel_path="b.md"), _page(1, rel_path="a.md")],
        tags_by_path={"a.md": ["adr"], "b.md": ["adr"]},
        co_occurrence=True,
    )

    assert _edges(payload, "associates_with") == [("wiki:1", "wiki:2")]


def test_the_payload_is_identical_however_the_rows_arrive():
    """Determinism is the acceptance criterion the static export depends on, so
    it is asserted against reordered inputs rather than assumed from the code."""
    pages = [_page(1, rel_path="a.md"), _page(2, rel_path="b.md"), _page(3)]
    links = [
        {"src_page_id": 2, "dst_page_id": 3, "link_kind": None},
        {"src_page_id": 1, "dst_page_id": 2, "link_kind": "documents"},
    ]
    memory_links = [{"page_id": 3, "memory_id": 9}, {"page_id": 1, "memory_id": 7}]
    memories = [_memory(9), _memory(7)]
    tags = {"a.md": ["adr"], "b.md": ["adr"]}
    kwargs = {"cross_lens": True, "co_occurrence": True, "tags_by_path": tags}

    forward = _build(
        pages=pages, links=links, memory_links=memory_links, memories=memories, **kwargs
    )
    reversed_ = _build(
        pages=list(reversed(pages)),
        links=list(reversed(links)),
        memory_links=list(reversed(memory_links)),
        memories=list(reversed(memories)),
        **kwargs,
    )

    assert json.dumps(forward, sort_keys=False) == json.dumps(
        reversed_, sort_keys=False
    )


def test_an_empty_wiki_is_a_valid_payload_that_says_it_is_empty():
    payload = _build(pages=[])

    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["meta"]["empty"] is True
    assert payload["meta"]["schema"] == "workflow_graph.v1"


def test_meta_reports_the_lens_and_the_toggles_it_was_given():
    payload = _build(
        pages=[_page(1)], domain="cortex", cross_lens=False, co_occurrence=True
    )

    assert payload["meta"]["lens"] == "wiki"
    assert payload["meta"]["domain"] == "cortex"
    assert payload["meta"]["cross_lens"] is False
    assert payload["meta"]["co_occurrence"] is True
    assert payload["meta"]["empty"] is False
    assert payload["meta"]["node_count"] == len(payload["nodes"])
    assert payload["meta"]["edge_count"] == len(payload["edges"])


def test_cross_lens_is_on_by_default_matching_the_client_toggle():
    """`wiki.js` ships the cross-lens toggle ON, so the builder's default has to
    agree — a disagreement means the first paint contradicts the checkbox."""
    payload = _build(
        pages=[_page(1)],
        memory_links=[{"page_id": 1, "memory_id": 7}],
        memories=[_memory(7)],
    )

    assert _kinds(payload, "memory") == ["memory:7"]
    assert payload["meta"]["cross_lens"] is True


def test_a_memory_row_without_an_id_is_skipped_rather_than_crashing():
    payload = _build(
        pages=[_page(1)],
        memory_links=[{"page_id": 1, "memory_id": 7}],
        memories=[{"content": "no id"}, _memory(7)],
        cross_lens=True,
    )

    assert _kinds(payload, "memory") == ["memory:7"]


def test_a_page_with_no_path_never_co_occurs_with_another():
    """Two pages with no rel_path would both fall back to the same "" key and
    link to each other for no reason at all."""
    payload = _build(
        pages=[_page(1, rel_path=""), _page(2, rel_path="")],
        tags_by_path={"": ["adr"]},
        co_occurrence=True,
    )

    assert _edges(payload, "associates_with") == []


def test_a_pathless_page_does_not_stop_the_pages_after_it_co_occurring():
    """The pathless page is skipped, not treated as the end of the list — an
    early exit here would silently drop every co-occurrence behind it."""
    payload = _build(
        pages=[
            _page(1, rel_path=""),
            _page(2, rel_path="b.md"),
            _page(3, rel_path="c.md"),
        ],
        tags_by_path={"b.md": ["adr"], "c.md": ["adr"]},
        co_occurrence=True,
    )

    assert _edges(payload, "associates_with") == [("wiki:2", "wiki:3")]


def test_a_link_missing_an_endpoint_is_dropped_without_raising():
    """`wiki.links` filters NULL targets in SQL, but this builder is pure and
    is also fed by the static export, so a half-formed row must not crash it."""
    payload = _build(
        pages=[_page(1), _page(2)],
        links=[
            {"src_page_id": 1, "dst_page_id": None, "link_kind": None},
            {"src_page_id": None, "dst_page_id": 2, "link_kind": None},
            {"src_page_id": 1, "dst_page_id": 2, "link_kind": "documents"},
        ],
    )

    assert _edges(payload, "wiki_links") == [("wiki:1", "wiki:2")]


def test_a_co_occurrence_edge_names_the_shared_tags_it_came_from():
    payload = _build(
        pages=[_page(1, rel_path="a.md"), _page(2, rel_path="b.md")],
        tags_by_path={"a.md": ["adr", "retrieval"], "b.md": ["retrieval", "adr"]},
        co_occurrence=True,
    )

    edge = next(e for e in payload["edges"] if e["kind"] == "associates_with")
    assert edge["label"] == "adr, retrieval"
    assert edge["reason"] == "wiki-shared-tag"


def test_a_co_occurrence_label_is_capped_at_three_tags():
    """The label is chrome on an edge, not a tag list; four shared tags must not
    grow it without bound."""
    tags = ["a", "b", "c", "d"]
    payload = _build(
        pages=[_page(1, rel_path="a.md"), _page(2, rel_path="b.md")],
        tags_by_path={"a.md": tags, "b.md": tags},
        co_occurrence=True,
    )

    edge = next(e for e in payload["edges"] if e["kind"] == "associates_with")
    assert edge["label"] == "a, b, c"


def test_the_payload_carries_the_envelope_the_renderer_expects():
    payload = _build(pages=[_page(1)])

    assert set(payload) == {"nodes", "edges", "clusters", "meta"}
    assert payload["clusters"] == []


def test_no_domain_filter_is_reported_as_an_empty_domain():
    payload = _build(pages=[_page(1)])

    assert payload["meta"]["domain"] == ""


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("1", False, True),
        ("0", True, False),
        (None, True, True),
        (None, False, False),
        ("true", False, False),
        ("", True, True),
        ("2", False, False),
    ],
)
def test_only_the_two_literals_the_client_sends_move_a_toggle(raw, default, expected):
    """A malformed query must not flip a lens the user did not ask for."""
    params = {} if raw is None else {"xlens": raw}

    assert wiki_http._flag(params, "xlens", default=default) is expected


class _Store:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.queries = []

    def query(self, sql, params, *, batch=False):
        self.queries.append((sql, params, batch))
        if self.fail:
            raise RuntimeError("no such table")
        return self.rows


def test_linked_memories_are_fetched_once_deduplicated_and_sorted():
    store = _Store(rows=[{"id": 7}])

    assert wiki_graph.load_memories_by_ids(store, [9, 7, 9, None]) == [{"id": 7}]
    assert store.queries[0][1] == ([7, 9],)


def test_no_linked_memories_means_no_query_at_all():
    store = _Store()

    assert wiki_graph.load_memories_by_ids(store, [None, None]) == []
    assert store.queries == []


def test_an_absent_memories_table_degrades_to_an_empty_list():
    store = _Store(fail=True)

    assert wiki_graph.load_memories_by_ids(store, [1]) == []


def test_the_graph_route_reaches_the_builder(monkeypatch):
    monkeypatch.setattr(wiki_graph, "load_wiki_pages", lambda _s: [_page(1)])
    monkeypatch.setattr(wiki_graph, "load_wiki_links", lambda _s: [])
    monkeypatch.setattr(wiki_graph, "load_wiki_memory_links", lambda _s: [])
    monkeypatch.setattr(
        wiki_http.wiki_read, "list_pages", lambda: {"pages": [{"path": "p1.md"}]}
    )

    payload = wiki_http._dispatch_get(_Store(), "/api/wiki/graph", {})

    assert payload["meta"]["schema"] == "workflow_graph.v1"
    assert payload["meta"]["lens"] == "wiki"
    assert _kinds(payload, "wiki") == ["wiki:1"]


def test_cross_lens_off_skips_the_memory_query_entirely(monkeypatch):
    """The loader is the only extra I/O cross-lens costs; with the lens off it
    must not run at all."""
    called = []
    monkeypatch.setattr(wiki_graph, "load_wiki_pages", lambda _s: [])
    monkeypatch.setattr(wiki_graph, "load_wiki_links", lambda _s: [])
    monkeypatch.setattr(wiki_graph, "load_wiki_memory_links", lambda _s: [])
    monkeypatch.setattr(
        wiki_graph, "load_memories_by_ids", lambda *a: called.append(a) or []
    )
    monkeypatch.setattr(wiki_http.wiki_read, "list_pages", lambda: {"pages": []})

    wiki_http._dispatch_get(_Store(), "/api/wiki/graph", {"xlens": "0"})

    assert called == []


def test_an_op_this_server_does_not_serve_says_so_explicitly():
    """The regression behind #119: without this marker the reply is shaped like
    a success, and the client mounted it as an empty graph."""
    payload = wiki_http._dispatch_get(_Store(), "/api/wiki/concepts", {})

    assert payload["unavailable"] is True
    assert payload["note"] == "not_served_by_viz"


def test_a_real_graph_is_never_marked_unavailable(monkeypatch):
    monkeypatch.setattr(wiki_graph, "load_wiki_pages", lambda _s: [_page(1)])
    monkeypatch.setattr(wiki_graph, "load_wiki_links", lambda _s: [])
    monkeypatch.setattr(wiki_graph, "load_wiki_memory_links", lambda _s: [])
    monkeypatch.setattr(wiki_http.wiki_read, "list_pages", lambda: {"pages": []})

    payload = wiki_http._dispatch_get(_Store(), "/api/wiki/graph", {})

    assert "unavailable" not in payload
