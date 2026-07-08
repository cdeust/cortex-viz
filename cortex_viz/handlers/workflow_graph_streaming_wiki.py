"""Wiki-node streaming injection for ``_build_interleaved``.

Split out of ``workflow_graph_streaming.py`` to keep that file under
the project's 500-line ceiling (CLAUDE.md §4.1) — this module owns
only the glue between the interleaved builder's per-phase closures
(``_emit_delta``, ``_ingest_loop``, the cap-mode ``_assoc_target``
adapter) and the pure ``core.workflow_graph_wiki`` ingestion
functions. No business logic of its own; every decision (skip-missing-
endpoint, cap-mode branching) is delegated to the core/infrastructure
layers it calls.

Composition-root-adjacent layer (like its caller): imports both core
and infrastructure, never the reverse.
"""

from __future__ import annotations

from typing import Any, Callable

from cortex_viz.core.workflow_graph_wiki import (
    ingest_wiki_link,
    ingest_wiki_memory,
    ingest_wiki_page,
)


def ingest_wiki_pages_and_links(
    *,
    builder,
    source,
    store,
    filter_by_domain: Callable[[list], list],
    notify_loaded: Callable[[str, list], None],
    ingest_loop: Callable[..., None],
) -> None:
    """Phase 1d: WIKI nodes + WIKI -> WIKI ``wiki_links`` edges.

    Pages are retained in the builder like entities (small — hundreds,
    not tens of thousands), so ``wiki_links`` can resolve endpoints
    immediately after. Caller must run this AFTER entities (Phase 1c)
    and BEFORE Phase 2's relational edges — nothing downstream depends
    on wiki ordering, but wiki nodes must exist before wiki_links is
    ingested.

    Precondition: ``ingest_loop`` is the caller's ``_ingest_loop``
    closure (owns SSE-batch emission + the builder reference).
    Postcondition: one WIKI node per live ``wiki.pages`` row and one
    WIKI_LINKS edge per resolvable ``wiki.links`` row are added to
    ``builder``.
    """
    wiki_pages = filter_by_domain(source.load_wiki_pages(store))
    notify_loaded("wiki_pages", wiki_pages)
    ingest_loop("wiki_pages", wiki_pages, ingest_wiki_page, fn_takes_builder=True)

    # Links carry no "domain" key (they connect two already-domained
    # pages) — unlike pages, NOT run through filter_by_domain.
    wiki_links = source.load_wiki_links(store)
    notify_loaded("wiki_links", wiki_links)
    ingest_loop("wiki_links", wiki_links, ingest_wiki_link, fn_takes_builder=True)


def ingest_wiki_memory_edges(
    *,
    builder,
    source,
    store,
    mem_cap: int,
    assoc_target: Any,
    retained_memory_edges: list,
    emit_delta: Callable[[str, int, int], None],
    on_source_loaded,
    edge_to_dict: Callable[[Any], dict],
) -> int:
    """Phase 3d: WIKI -> MEMORY ``documents`` edges.

    Same endpoint-presence constraints as Phase 3b/3c (associations /
    supersede), reusing the caller's ``assoc_target`` adapter. CRITICAL
    difference from those two phases: in uncapped mode (``mem_cap <=
    0``) ``assoc_target``'s ``_nodes`` set was built from retained
    MEMORY pg-ids only — it never included wiki node ids, because wiki
    nodes are never purged from ``builder._nodes`` (they are part of
    the structural baseline captured before the memory phase, same as
    entities). Without widening that set here, EVERY wiki->memory edge
    would fail ``ingest_wiki_memory``'s skip-missing-endpoint check on
    the wiki-page side and be silently dropped.

    Returns:
        Count of DOCUMENTS edges added (for ``on_source_loaded``
        progress reporting by the caller's convention).
    """
    wiki_memory_rows = source.load_wiki_memory_links(store)
    if mem_cap > 0:
        wm_target = builder
        prev_n = len(builder._nodes)  # noqa: SLF001
        prev_e = len(builder._edges)  # noqa: SLF001
    else:
        wiki_node_ids = {
            nid for nid, n in builder._nodes.items() if n.kind == "wiki"  # noqa: SLF001
        }
        assoc_target._nodes = assoc_target._nodes | wiki_node_ids
        wm_target = assoc_target
        prev_e = len(wm_target._edges)
    for row in wiki_memory_rows:
        ingest_wiki_memory(wm_target, row)
    if mem_cap > 0:
        emit_delta("wiki_memory", prev_n, prev_e)
        count = len(builder._edges) - prev_e  # noqa: SLF001
    else:
        retained_memory_edges.extend(
            edge_to_dict(e) for e in wm_target._edges[prev_e:]
        )
        count = len(wm_target._edges) - prev_e
    if on_source_loaded is not None:
        on_source_loaded("wiki_memory", count)
    return count


__all__ = ["ingest_wiki_pages_and_links", "ingest_wiki_memory_edges"]
