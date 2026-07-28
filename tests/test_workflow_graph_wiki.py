"""Unit tests for the "brain wiki nodes" feature (v1 — reliable edges
only): ``core.workflow_graph_wiki`` ingestion + a full
``_build_interleaved`` regression guard for the cap-mode endpoint
widening in ``handlers.workflow_graph_streaming_wiki.
ingest_wiki_memory_edges``.

Mirrors ``test_workflow_graph_association.py``'s style for the pure
ingestion unit tests, and adds an integration-level test driving
``_build_interleaved`` directly (with a fake ``WorkflowGraphSource``)
because that is the ONLY path that exercises the
``_RetainedNodesView`` widening — a regression there silently drops
every wiki->memory edge in the uncapped (full-corpus) build, which a
pure-ingestion unit test cannot catch.
"""

from __future__ import annotations

import pytest

from cortex_viz.core.workflow_graph_schema import EdgeKind, NodeIdFactory, NodeKind
from cortex_viz.core.workflow_graph_wiki import (
    ingest_wiki_citation,
    ingest_wiki_link,
    ingest_wiki_memory,
    ingest_wiki_page,
)
from cortex_viz.handlers.workflow_graph_streaming import _build_interleaved
from cortex_viz.infrastructure.wiki_graph import load_wiki_session_links


class _FakeTarget:
    """Minimal duck-typed stand-in for a builder — matches
    ``test_workflow_graph_association.py``'s ``_FakeTarget``."""

    def __init__(self, node_ids):
        self._nodes = set(node_ids)
        self._edges: list = []


# ── ingest_wiki_page ────────────────────────────────────────────────


class _FakeBuilder:
    """Real ``_add_child``/``_ensure_domain``/``_assign_domain`` surface,
    borrowed from the actual builder so ``ingest_wiki_page`` is tested
    against its real contract rather than a re-implemented stub."""

    def __init__(self):
        from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder

        self._impl = WorkflowGraphBuilder()

    def __getattr__(self, name):
        return getattr(self._impl, name)


def test_ingest_wiki_page_creates_node_with_in_domain_edge():
    b = _FakeBuilder()
    ingest_wiki_page(
        b,
        {
            "id": 7,
            "title": "ADR-0046",
            "kind": "reference",
            "domain": None,
            "status": "active",
            "heat": 0.5,
            "rel_path": "adr/0046.md",
        },
    )
    wiki_id = NodeIdFactory.wiki_id(7)
    assert wiki_id in b._nodes
    node = b._nodes[wiki_id]
    assert node.kind == NodeKind.WIKI.value
    assert node.label == "ADR-0046"
    in_domain_edges = [e for e in b._edges if e.source == wiki_id]
    assert len(in_domain_edges) == 1
    assert in_domain_edges[0].kind == EdgeKind.IN_DOMAIN.value


def test_ingest_wiki_page_process_title_gets_short_label_and_full_name():
    b = _FakeBuilder()
    ingest_wiki_page(
        b,
        {
            "id": 8,
            "title": "Process — process::tests_py/hooks/test_x.py::test_x",
            "kind": "process",
            "domain": None,
            "status": "active",
            "heat": 0.1,
            "rel_path": None,
        },
    )
    node = b._nodes[NodeIdFactory.wiki_id(8)]
    assert node.label == "test_x"
    assert node.full_name == "Process — process::tests_py/hooks/test_x.py::test_x"


def test_ingest_wiki_page_missing_id_raises():
    b = _FakeBuilder()
    try:
        ingest_wiki_page(b, {"title": "no id"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ── ingest_wiki_link ────────────────────────────────────────────────


def test_ingest_wiki_link_creates_edge_when_both_endpoints_present():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), NodeIdFactory.wiki_id(2)})
    ingest_wiki_link(target, {"src_page_id": 1, "dst_page_id": 2, "link_kind": "ref"})
    assert len(target._edges) == 1
    edge = target._edges[0]
    assert edge.source == NodeIdFactory.wiki_id(1)
    assert edge.target == NodeIdFactory.wiki_id(2)
    assert edge.kind == EdgeKind.WIKI_LINKS.value
    assert edge.label == "ref"


def test_ingest_wiki_link_missing_endpoint_dropped_silently():
    target = _FakeTarget({NodeIdFactory.wiki_id(1)})
    ingest_wiki_link(target, {"src_page_id": 1, "dst_page_id": 2, "link_kind": None})
    assert target._edges == []


def test_ingest_wiki_link_none_ids_skipped():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), NodeIdFactory.wiki_id(2)})
    ingest_wiki_link(target, {"src_page_id": None, "dst_page_id": 2})
    assert target._edges == []


# ── ingest_wiki_memory ──────────────────────────────────────────────


def test_ingest_wiki_memory_creates_documents_edge_when_both_present():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), NodeIdFactory.memory_id(100)})
    ingest_wiki_memory(target, {"page_id": 1, "memory_id": 100})
    assert len(target._edges) == 1
    edge = target._edges[0]
    assert edge.source == NodeIdFactory.wiki_id(1)
    assert edge.target == NodeIdFactory.memory_id(100)
    assert edge.kind == EdgeKind.DOCUMENTS.value


def test_ingest_wiki_memory_missing_memory_endpoint_dropped_silently():
    target = _FakeTarget({NodeIdFactory.wiki_id(1)})
    ingest_wiki_memory(target, {"page_id": 1, "memory_id": 100})
    assert target._edges == []


# ── ingest_wiki_citation ────────────────────────────────────────────


def test_ingest_wiki_citation_creates_cited_in_edge_when_both_present():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), "discussion:sess-abc"})
    ingest_wiki_citation(
        target, {"page_id": 1, "session_id": "sess-abc", "cited_at": "2026-07-09"}
    )
    assert len(target._edges) == 1
    edge = target._edges[0]
    assert edge.source == NodeIdFactory.wiki_id(1)
    assert edge.target == "discussion:sess-abc"
    assert edge.kind == EdgeKind.CITED_IN.value
    assert edge.label == "2026-07-09"


def test_ingest_wiki_citation_missing_discussion_endpoint_dropped_silently():
    target = _FakeTarget({NodeIdFactory.wiki_id(1)})
    ingest_wiki_citation(target, {"page_id": 1, "session_id": "sess-abc"})
    assert target._edges == []


def test_ingest_wiki_citation_missing_page_endpoint_dropped_silently():
    target = _FakeTarget({"discussion:sess-abc"})
    ingest_wiki_citation(target, {"page_id": 1, "session_id": "sess-abc"})
    assert target._edges == []


def test_ingest_wiki_citation_empty_session_id_skipped():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), "discussion:"})
    ingest_wiki_citation(target, {"page_id": 1, "session_id": ""})
    assert target._edges == []


def test_ingest_wiki_citation_none_ids_skipped():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), "discussion:sess-abc"})
    ingest_wiki_citation(target, {"page_id": None, "session_id": "sess-abc"})
    assert target._edges == []


def test_ingest_wiki_citation_omits_label_when_cited_at_absent():
    target = _FakeTarget({NodeIdFactory.wiki_id(1), "discussion:sess-abc"})
    ingest_wiki_citation(target, {"page_id": 1, "session_id": "sess-abc"})
    assert target._edges[0].label is None


# ── load_wiki_session_links (infrastructure loader) ────────────────


class _RaisingPgStore:
    def query(self, sql, params, *, batch=True):
        raise RuntimeError("wiki.citations absent (pre-ADR-0051 DB)")


class _RowPgStore:
    def __init__(self, rows):
        self._rows = rows

    def query(self, sql, params, *, batch=True):
        return self._rows


def test_load_wiki_session_links_degrades_to_empty_on_exception():
    assert load_wiki_session_links(_RaisingPgStore()) == []


def test_load_wiki_session_links_returns_rows():
    rows = [{"page_id": 1, "session_id": "sess-abc", "cited_at": "2026-07-09"}]
    assert load_wiki_session_links(_RowPgStore(rows)) == rows


# ── Full-build regression guard: both cap modes ────────────────────
#
# wiki -> FILE (``wiki_source``) edges are NO LONGER emitted by
# ``_build_interleaved``: their FILE endpoint is only complete after the L6 AST
# sweep, so they are resolved at finalisation over the cumulative cache. Those
# edges are covered by ``test_graph_build_wiki_source.py``; the tests below
# guard only the wiki NODE + wiki->memory (``documents``) behaviour, which
# stays builder-local (VOLET ①, mem 4262203).


class _FakeWikiSource:
    """Minimal ``WorkflowGraphSource``-shaped stand-in. Every stream is
    empty EXCEPT one wiki page, one wiki link, one memory, and one
    wiki->memory link — just enough to exercise every ingestion phase
    ``_build_interleaved`` runs, without a live database."""

    def load_skills(self):
        return []

    def load_hooks(self):
        return []

    def load_agent_events(self):
        return []

    def load_command_events(self, store):
        return []

    def load_discussions(self):
        return [{"session_id": "sess-abc", "domain": None, "message_count": 3}]

    def load_tool_events(self, store):
        return [
            {
                "tool": "Read",
                "domain": None,
                "file_path": "/repo/one.py",
                "count": 1,
            }
        ]

    def load_entities(self, store):
        return []

    def load_wiki_pages(self, store):
        return [
            {
                "id": 1,
                "title": "Wiki Page One",
                "kind": "reference",
                "domain": None,
                "status": "active",
                "heat": 0.4,
                "rel_path": "one.md",
                "memory_id": None,
            }
        ]

    def load_wiki_links(self, store):
        return []

    def load_wiki_page_sources(self, store):
        return [
            {
                "page_id": 1,
                "source_path": "one.py",
                "link_kind": "documents",
                "confidence": 1.0,
            },
            {
                "page_id": 1,
                "source_path": "not-a-real-file.py",
                "link_kind": "references",
                "confidence": 0.5,
            },
        ]

    def load_discussion_files(self):
        return []

    def load_command_files(self, store, known_paths):
        return []

    def load_skill_usage(self):
        return []

    def load_mcp_usage(self):
        return []

    def load_discussion_tool_uses(self):
        return []

    def load_discussion_agents(self):
        return []

    def load_discussion_commands(self):
        return []

    def load_memory_entity_edges(self, store):
        return []

    def iter_memories_chunked(self, store, min_heat=0.0, chunk_size=1000, limit=0):
        yield [{"id": 100, "domain": None, "content": "a memory"}]

    def load_memory_associations(self, store):
        return []

    def load_supersede_edges(self, store):
        return []

    def load_wiki_memory_links(self, store):
        return [{"page_id": 1, "memory_id": 100}]

    def load_wiki_session_links(self, store):
        return [{"page_id": 1, "session_id": "sess-abc", "cited_at": "2026-07-09"}]


class _FakeAssociatedSource(_FakeWikiSource):
    """Two memories joined by one association AND one supersede edge.

    ``_FakeWikiSource`` returns empty lists for both channels, so Phase
    3b/3c run their prologue and emit path over ZERO rows — enough to
    prove no crash, not enough to prove an edge survives. This subclass
    seeds the smallest input that makes both channels produce a real
    edge, so the delta-baseline locals those phases keep (``_assoc_prev_*``
    / ``_sup_prev_*``) are read against a list that actually grew.
    """

    def iter_memories_chunked(self, store, min_heat=0.0, chunk_size=1000, limit=0):
        yield [
            {"id": 100, "domain": None, "content": "older memory"},
            {"id": 101, "domain": None, "content": "newer memory"},
        ]

    def load_memory_associations(self, store):
        return [
            {
                "source_memory_id": 100,
                "target_memory_id": 101,
                "weight": 0.75,
                "shared_count": 2,
            }
        ]

    def load_supersede_edges(self, store):
        return [{"source_memory_id": 101, "target_memory_id": 100}]


def _run(memory_limit: int, source=None) -> dict:
    return _build_interleaved(
        store=object(),
        source=source if source is not None else _FakeWikiSource(),
        domain_filter=None,
        min_memory_heat=0.0,
        memory_limit=memory_limit,
        stage="full",
        defer_native_ast=True,
        on_source_loaded=None,
        on_batch=None,
        notify_loaded=lambda *_a: None,
    )


def _run_collecting(memory_limit: int, source=None):
    """``_run`` with the two progress callbacks wired to collectors.

    ``_run`` passes ``on_batch=None`` and ``on_source_loaded=None``, which
    makes ``_emit_delta`` return immediately — so nothing it computes is
    observable and no test can pin it. These are the phases' only
    outward signals (the SSE delta frame and the /api/graph/progress
    counter), so asserting them requires actually subscribing.

    Returns ``(result, batches, loaded)`` where ``batches`` maps a phase
    label to the list of ``(new_nodes, new_edges)`` it emitted and
    ``loaded`` maps a phase label to its reported item count.
    """
    batches: dict[str, list] = {}
    loaded: dict[str, int] = {}

    def _on_batch(label, new_nodes, new_edges):
        batches.setdefault(label, []).append((new_nodes, new_edges))

    def _on_source_loaded(label, count):
        loaded[label] = count

    result = _build_interleaved(
        store=object(),
        source=source if source is not None else _FakeWikiSource(),
        domain_filter=None,
        min_memory_heat=0.0,
        memory_limit=memory_limit,
        stage="full",
        defer_native_ast=True,
        on_source_loaded=_on_source_loaded,
        on_batch=_on_batch,
        notify_loaded=lambda *_a: None,
    )
    return result, batches, loaded


def test_wiki_node_and_edges_emitted_in_bounded_cap_mode():
    """``memory_limit > 0`` -> ``_mem_cap > 0`` -> ``_assoc_target`` IS
    the real builder, so wiki->memory endpoint presence needs no
    widening (both node kinds already live in ``builder._nodes``)."""
    result = _run(memory_limit=10)
    kinds = {n["kind"] for n in result["nodes"]}
    assert "wiki" in kinds
    edge_kinds = [e["kind"] for e in result["edges"]]
    assert "documents" in edge_kinds
    assert "cited_in" in edge_kinds
    assert result["meta"]["counts"]["wiki"] == 1


def test_no_wiki_source_edges_in_interleaved_output(monkeypatch):
    """``_build_interleaved`` must NOT emit wiki_source edges anymore — the
    FILE endpoint is incomplete until L6, so resolution moved to the
    finalisation pass (``graph_build_wiki_source``). Guards against a
    regression that re-adds the premature baseline-time resolution."""
    import cortex_viz.core.wiki_source_resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "source_roots_for_domain", lambda c: ["/repo"])
    for cap in (10, 0):
        result = _run(memory_limit=cap)
        assert [e for e in result["edges"] if e["kind"] == "wiki_source"] == []


def test_wiki_memory_edge_survives_uncapped_mode_regression_guard():
    """``memory_limit=0`` -> ``_mem_cap <= 0`` -> memories are purged
    from ``builder._nodes`` per chunk and the association/supersede
    passes use the ``_RetainedNodesView`` adapter, whose ``_nodes`` set
    is built from retained MEMORY pg-ids only. Without widening it with
    every wiki node id (see
    ``handlers.workflow_graph_streaming_wiki.ingest_wiki_memory_edges``),
    ``ingest_wiki_memory`` would skip every row on the wiki-page side
    and this assertion would fail — this IS the load-bearing regression
    guard the task brief calls for."""
    result = _run(memory_limit=0)
    kinds = {n["kind"] for n in result["nodes"]}
    assert "wiki" in kinds
    edge_kinds = [e["kind"] for e in result["edges"]]
    assert edge_kinds.count("documents") > 0
    # CITED_IN needs NO _RetainedNodesView widening (unlike documents):
    # both endpoints (wiki, discussion) are structural-baseline nodes
    # never purged in either cap mode — see
    # ingest_wiki_citation_edges' docstring. Asserting it survives the
    # SAME uncapped mode that requires the documents-edge widening is
    # the regression guard that this simplification is actually correct
    # and not merely untested.
    assert edge_kinds.count("cited_in") > 0
    assert result["meta"]["counts"]["wiki"] == 1


@pytest.mark.parametrize("memory_limit", [10, 0])
def test_association_and_supersede_edges_emitted_in_both_cap_modes(memory_limit):
    """Phase 3b/3c produce their edges under BOTH cap modes.

    The two phases take different routes to the same result:
    ``memory_limit > 0`` ingests into the real builder and reports the
    edges through the ``_emit_delta`` baseline; ``memory_limit == 0``
    ingests into the ``_RetainedNodesView`` adapter and copies its edge
    list into ``retained_memory_edges``. Both routes read baseline
    locals captured before the ingest loop, so this pins the property
    that survives either route — the edge reaches the output — rather
    than the branch that produced it.
    """
    result = _run(memory_limit=memory_limit, source=_FakeAssociatedSource())
    edge_kinds = [e["kind"] for e in result["edges"]]
    assert edge_kinds.count("associates_with") == 1
    assert edge_kinds.count("supersedes") == 1
    # The supersede edge is DIRECTIONAL (newer -> older); asserting the
    # orientation catches a swapped baseline that a bare count would not.
    sup = next(e for e in result["edges"] if e["kind"] == "supersedes")
    assert (sup["source"], sup["target"]) == (
        NodeIdFactory.memory_id(101),
        NodeIdFactory.memory_id(100),
    )


@pytest.mark.parametrize("memory_limit", [10, 0])
def test_association_and_supersede_report_their_own_counts(memory_limit):
    """Each phase reports exactly the rows IT contributed.

    The counts are derived by subtracting a baseline captured before the
    ingest loop from the size after it. A baseline that is too low makes
    a phase claim its predecessor's edges as well, which is invisible in
    the final graph (the edges are all there either way) and only shows
    up on this counter.
    """
    _, _, loaded = _run_collecting(memory_limit, source=_FakeAssociatedSource())
    assert loaded["associations"] == 1
    assert loaded["supersede"] == 1


def test_association_delta_frame_carries_only_its_own_edge():
    """Bounded mode streams each phase as an incremental delta frame.

    Phase 3b adds no NODES (both memories were created during the memory
    chunks) and exactly one edge, so its frame must carry an empty node
    list and a single edge. A stale node baseline re-sends nodes already
    streamed — the browser then re-renders the whole graph on what should
    be a one-edge update — and a stale edge baseline re-sends earlier
    phases' edges. Neither is visible in the final graph, so the frame is
    the only place either can be caught.
    """
    _, batches, _ = _run_collecting(10, source=_FakeAssociatedSource())
    assoc_frames = batches["associations"]
    assert len(assoc_frames) == 1
    new_nodes, new_edges = assoc_frames[0]
    assert list(new_nodes) == []
    assert [e.kind for e in new_edges] == [EdgeKind.ASSOCIATES_WITH]

    sup_frames = batches["supersede"]
    assert len(sup_frames) == 1
    sup_nodes, sup_edges = sup_frames[0]
    assert list(sup_nodes) == []
    assert [e.kind for e in sup_edges] == [EdgeKind.SUPERSEDES]
