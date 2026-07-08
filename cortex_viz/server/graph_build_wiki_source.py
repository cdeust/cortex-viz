"""Post-L6 wiki-page -> source-file edge resolution over the cumulative cache.

Split from ``graph_build_run`` (SRP + 500-line ceiling): the ``wiki_source``
edges (ADR-0051) must resolve against the COMPLETE FILE-node set, but FILE
nodes arrive in TWO waves — tool-touched files at the baseline (Phase 1b/1d)
and AST-indexed files at L6 (``graph_build_l6``). The baseline's Phase 1d
``ingest_wiki_source_edges`` therefore only reaches tool-touched files;
AST-only files (indexed by automatised-pipeline but never touched by a Claude
tool) do not exist yet when it runs.

This pass re-resolves every ``wiki.page_sources`` row against the cumulative
node index AFTER L6 has materialised the AST FILE nodes, so wiki pages link to
code-graph files too — the ``décision -> code`` visibility the brain view
requires. ``merge`` dedups the overlap with the baseline's edges (identical
``(source, target, "wiki_source")`` key), so re-emitting the baseline subset is
free.

Correctness precondition (VOLET ①, 2026-07-08 — see mem 4262203): L6 must key
AST FILE nodes by the ABSOLUTE path (``file_id(join(repo_root, ap_relative))``),
the SAME scheme ``resolve_file_node_id`` reconstructs. AP emits repo-relative
paths; if L6 keyed AST files by that relative path, the ``file_id`` computed
here (absolute) would never match, and this pass would resolve tool-files only —
exactly the pre-fix behaviour. This module is the ordering half; the keying half
lives in ``graph_build_l6``.

Composition-root-adjacent (like its caller): reads infrastructure
(``wiki_graph``) + core (``wiki_source_resolve``) and mutates the cumulative
cache only through the injected ``merge`` closure. No business logic of its own —
the resolution rule is ``core.wiki_source_resolve.resolve_file_node_id`` and the
skip-missing-endpoint contract mirrors ``core.workflow_graph_wiki.ingest_wiki_source``.
"""

from __future__ import annotations

from typing import Callable

from cortex_viz.server import graph_cache_state as state


def resolve_wiki_source_over_cache(store, *, merge: Callable) -> int:
    """Resolve ``wiki.page_sources`` rows to WIKI -> FILE edges against the
    cumulative node index and merge the resolvable ones.

    Mirrors ``core.workflow_graph_wiki.ingest_wiki_source`` but operates on
    the post-L6 cumulative cache (plain dicts in ``state._node_index``)
    instead of a live ``WorkflowGraphBuilder``. Same two-gate contract:
    an edge is emitted only when BOTH the WIKI node is present AND the
    resolved FILE node id is present — never fabricates a FILE node for an
    unresolved path.

    Args:
        store: read-only PG store passed to ``load_wiki_page_sources``.
        merge: the build's cumulative-cache merge closure (``make_merge``).

    Returns:
        The number of edges submitted to ``merge`` (pre-dedup) — the caller
        reports it as an L6-finalisation progress line.
    """
    from cortex_viz.core.wiki_source_resolve import resolve_file_node_id
    from cortex_viz.core.workflow_graph_schema import NodeIdFactory
    from cortex_viz.infrastructure.wiki_graph import load_wiki_page_sources

    rows = load_wiki_page_sources(store)
    if not rows:
        return 0

    node_index = state._node_index
    edges: list[dict] = []
    for row in rows:
        page_pg = row.get("page_id")
        source_path = row.get("source_path")
        if page_pg is None or not source_path:
            continue
        wiki_id = NodeIdFactory.wiki_id(page_pg)
        wiki_node = node_index.get(wiki_id)
        if wiki_node is None:
            # Page filtered out (domain_filter) or never ingested — skip,
            # matching ingest_wiki_source's missing-page-node guard.
            continue
        file_id = resolve_file_node_id(wiki_node.get("domain_id"), source_path)
        if file_id is None or file_id not in node_index:
            # Unresolvable (no known source root for the domain) or the FILE
            # node is genuinely absent from the graph — no fabricated edge.
            continue
        edge: dict = {
            "source": wiki_id,
            "target": file_id,
            "kind": "wiki_source",
            "type": "wiki_source",
            "reason": "wiki-source",
        }
        link_kind = row.get("link_kind")
        if link_kind is not None:
            edge["label"] = link_kind
        confidence = row.get("confidence")
        if confidence is not None:
            edge["confidence"] = float(confidence)
        edges.append(edge)

    if edges:
        merge(
            [],
            edges,
            stage="wiki_source",
            pct=0.96,
            message=f"wiki->source: {len(edges)} resolved edges (post-AST)",
            phase_key=None,
        )
    return len(edges)


__all__ = ["resolve_wiki_source_over_cache"]
