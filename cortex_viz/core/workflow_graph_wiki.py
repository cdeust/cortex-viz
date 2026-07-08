"""Wiki-node and wiki-edge ingestion for ``WorkflowGraphBuilder``
("brain wiki nodes", v1 — reliable edges only).

Holds the three helpers that project Cortex's wiki surface into the
workflow graph:

  * ``ingest_wiki_page(b, page)`` — creates one WIKI node per row of
    ``infrastructure.wiki_graph.load_wiki_pages``; anchored to its
    domain via ``IN_DOMAIN`` (mirrors ``ingest_entity``).
  * ``ingest_wiki_link(b, link)`` — creates one WIKI -> WIKI
    ``WIKI_LINKS`` edge per row of
    ``infrastructure.wiki_graph.load_wiki_links``. Silently skips
    links whose endpoints are not in the graph.
  * ``ingest_wiki_memory(b, row)`` — creates one WIKI -> MEMORY
    ``DOCUMENTS`` edge per row of
    ``infrastructure.wiki_graph.load_wiki_memory_links``. Same
    skip-missing-endpoint contract.
  * ``ingest_wiki_source(b, row)`` — creates one WIKI -> FILE
    ``WIKI_SOURCE`` edge per row of
    ``infrastructure.wiki_graph.load_wiki_page_sources`` (ADR-0051),
    after resolving ``source_path`` via
    ``core.wiki_source_resolve.resolve_file_node_id``. Same
    skip-missing-endpoint contract as the other two — additionally
    skips when the endpoint can't even be RESOLVED (no known
    filesystem source root for the page's domain).

Deliberately its own module rather than folded into
``workflow_graph_entity`` (MEMORY -> ENTITY) or
``workflow_graph_association`` (MEMORY <-> MEMORY): a different node
kind is minted here (WIKI), with two distinct edge shapes of its own
(wiki<->wiki, wiki->memory) and a distinct producer
(``infrastructure.wiki_graph``). Folding them together would violate
SRP. Kept well under CLAUDE.md's 300-line ceiling.

Pure core logic — no I/O. Every helper here only reads ``b._nodes``
(membership check) and appends to ``b._nodes`` / ``b._edges``, or (for
``ingest_wiki_page``) calls ``b._assign_domain`` / ``b._ensure_domain``
/ ``b._add_child`` exactly like ``ingest_entity`` does.
"""

from __future__ import annotations

from cortex_viz.core.graph_builder_nodes import WIKI_COLOR
from cortex_viz.core.wiki_source_resolve import resolve_file_node_id
from cortex_viz.core.workflow_graph_schema import (
    EdgeKind,
    NodeIdFactory,
    NodeKind,
    WorkflowEdge,
)


def _require(rec: dict, key: str, ctx: str):
    """Match the tiny validator ``workflow_graph_entity`` uses —
    duplicated locally so this module has zero cross-module pulls."""
    if key not in rec or rec[key] is None:
        raise ValueError(f"{ctx}: missing key {key!r} in {rec!r}")
    return rec[key]


def ingest_wiki_page(b, page: dict) -> None:
    """Create one WIKI node from a ``wiki.pages`` row.

    Args:
        b: ``WorkflowGraphBuilder`` instance (for ``_assign_domain`` /
           ``_ensure_domain`` / ``_add_child``).
        page: ``{"id": int, "title": str, "kind": str, "domain": str,
           "status": str, "heat": float, "rel_path": str,
           "memory_id": int | None}`` — output of
           ``infrastructure.wiki_graph.load_wiki_pages``.

    Side effects: adds one WIKI node + one ``in_domain`` edge. WIKI is
    single-domain (not in ``_MULTI_DOMAIN_KINDS``) — a page belongs to
    exactly one domain, so ``_add_child``'s single ``in_domain`` edge
    satisfies the schema invariant without further work.
    """
    pg_id = _require(page, "id", "wiki page")
    dom = b._assign_domain(page.get("domain"))
    b._ensure_domain(dom)
    heat = float(page.get("heat") or 0.0)
    b._add_child(
        NodeIdFactory.wiki_id(pg_id),
        NodeKind.WIKI,
        page.get("title") or f"wiki page {pg_id}",
        WIKI_COLOR,
        dom,
        1.0 + min(3.0, heat * 3.0),
        page_kind=page.get("kind"),
        status=page.get("status"),
        heat=heat,
        path=page.get("rel_path"),
    )


def ingest_wiki_link(b, link: dict) -> None:
    """Create one WIKI -> WIKI ``WIKI_LINKS`` edge.

    ``link`` carries ``src_page_id`` + ``dst_page_id`` (the same PG
    primary keys used by ``wiki.links``). Silently skips when either
    endpoint is not present in the graph — matches the
    "skip-missing-endpoint" contract of ``ingest_about_entity``.
    """
    src_pg = link.get("src_page_id")
    dst_pg = link.get("dst_page_id")
    if src_pg is None or dst_pg is None:
        return
    src_id = NodeIdFactory.wiki_id(src_pg)
    dst_id = NodeIdFactory.wiki_id(dst_pg)
    if src_id not in b._nodes or dst_id not in b._nodes:
        return
    b._edges.append(
        WorkflowEdge(
            source=src_id,
            target=dst_id,
            kind=EdgeKind.WIKI_LINKS,
            label=link.get("link_kind"),
            reason="wiki-link",
        )
    )


def ingest_wiki_memory(b, row: dict) -> None:
    """Create one WIKI -> MEMORY ``DOCUMENTS`` edge.

    ``row`` carries ``page_id`` + ``memory_id`` (the union of a page's
    anchor memory and its citations — see
    ``infrastructure.wiki_graph.load_wiki_memory_links``). Same
    skip-missing-endpoint contract as ``ingest_wiki_link``.
    """
    page_pg = row.get("page_id")
    mem_pg = row.get("memory_id")
    if page_pg is None or mem_pg is None:
        return
    page_id = NodeIdFactory.wiki_id(page_pg)
    mem_id = NodeIdFactory.memory_id(mem_pg)
    if page_id not in b._nodes or mem_id not in b._nodes:
        return
    b._edges.append(
        WorkflowEdge(
            source=page_id,
            target=mem_id,
            kind=EdgeKind.DOCUMENTS,
            reason="wiki-documents",
        )
    )


def ingest_wiki_source(b, row: dict) -> None:
    """Create one WIKI -> FILE ``WIKI_SOURCE`` edge from a
    ``wiki.page_sources`` row (ADR-0051).

    Precondition: the WIKI node for ``row["page_id"]`` already exists in
    ``b._nodes`` (phase ordering: wiki pages are ingested before this
    function runs) and, if resolvable, so does the FILE node for
    ``row["source_path"]`` (file nodes are finalised before the wiki
    phase — see ``handlers.workflow_graph_streaming``'s Phase 1b/1d
    ordering).

    Postcondition: adds exactly one WIKI_SOURCE edge when BOTH (a)
    ``resolve_file_node_id`` can reconstruct a candidate FILE node id
    from the page's own ``domain_id`` + ``source_path``, AND (b) that
    id is actually present in ``b._nodes``. Otherwise no edge and no
    exception — never fabricates a FILE node for an unresolved path
    (matches ``ingest_wiki_link`` / ``ingest_wiki_memory``'s
    skip-missing-endpoint contract).

    ``row["link_kind"]`` ('documents' / 'references' / 'derived') is
    carried through as the edge label so the frontend can style the
    three provenance kinds distinctly.
    """
    page_pg = row.get("page_id")
    source_path = row.get("source_path")
    if page_pg is None or not source_path:
        return
    page_id = NodeIdFactory.wiki_id(page_pg)
    page_node = b._nodes.get(page_id)
    if page_node is None:
        return
    file_id = resolve_file_node_id(page_node.domain_id, source_path)
    if file_id is None or file_id not in b._nodes:
        return
    confidence = row.get("confidence")
    b._edges.append(
        WorkflowEdge(
            source=page_id,
            target=file_id,
            kind=EdgeKind.WIKI_SOURCE,
            label=row.get("link_kind"),
            confidence=float(confidence) if confidence is not None else None,
            reason="wiki-source",
        )
    )


__all__ = [
    "ingest_wiki_page",
    "ingest_wiki_link",
    "ingest_wiki_memory",
    "ingest_wiki_source",
]
