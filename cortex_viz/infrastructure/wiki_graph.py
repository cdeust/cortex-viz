"""Read-only wiki-page loaders for the workflow graph ("brain wiki
nodes", v1 — reliable edges only).

Four independent SELECTs, each best-effort (degrades to ``[]`` on any
exception — matches ``infrastructure.wiki_pg``'s style: the ``wiki.*``
schema is a separate, optional Cortex feature and the workflow graph
must render fine without it):

  * ``load_wiki_pages`` — one row per live (non-stale) ``wiki.pages``
    entry, projected into WIKI workflow-graph nodes.
  * ``load_wiki_links`` — page-to-page links from ``wiki.links``,
    projected into ``wiki_links`` edges.
  * ``load_wiki_memory_links`` — page-to-memory links, the UNION of
    ``wiki.pages.memory_id`` (a page's anchor memory) and
    ``wiki.citations`` (memories cited while writing the page),
    projected into ``documents`` edges.
  * ``load_wiki_page_sources`` — page-to-source-file links from
    ``wiki.page_sources`` (ADR-0051), projected into ``wiki_source``
    edges once ``core.wiki_source_resolve`` resolves ``source_path`` to
    a live FILE node id.

A wiki→source-file edge was originally investigated and DROPPED in v1
(no ``wiki.page_sources`` table existed yet, and wiki pages carried no
reliable file-linkage frontmatter field). ADR-0051 (STEP 2, Cortex
side) added that table with real provenance tracking, superseding the
v1 decision — see ``load_wiki_page_sources`` below and
``core.wiki_source_resolve.resolve_file_node_id``.

No I/O beyond four read-only ``pg_store.query`` SELECTs — this module
never INSERTs, UPDATEs, or DELETEs; cortex-viz is a read-only bridge
over Cortex's shared Postgres store.
"""

from __future__ import annotations

from typing import Any

_WIKI_PAGES_SQL = """
SELECT id, title, kind, domain, status, heat, rel_path, memory_id
FROM wiki.pages
WHERE NOT is_stale
"""

_WIKI_LINKS_SQL = """
SELECT src_page_id, dst_page_id, link_kind
FROM wiki.links
WHERE dst_page_id IS NOT NULL
"""

# UNION of the two page->memory evidence sources: a page's own anchor
# memory (wiki.pages.memory_id, may be NULL) and every memory cited
# while writing the page (wiki.citations, one row per citation event).
# DISTINCT collapses a page citing the same memory more than once —
# the workflow graph draws presence, not citation frequency.
_WIKI_MEMORY_LINKS_SQL = """
SELECT page_id, memory_id FROM (
    SELECT id AS page_id, memory_id
    FROM wiki.pages
    WHERE memory_id IS NOT NULL AND NOT is_stale
    UNION
    SELECT page_id, memory_id
    FROM wiki.citations
    WHERE memory_id IS NOT NULL
) unioned
"""

# Every link_kind is read (not just 'documents') — 'references' rows are
# added in parallel by another agent against the same table; this loader
# must not depend on their presence to compile or degrade. Styling per
# link_kind is the ingest layer's concern (label carried through verbatim).
_WIKI_PAGE_SOURCES_SQL = """
SELECT page_id, source_path, link_kind, confidence
FROM wiki.page_sources
"""


def load_wiki_pages(pg_store) -> list[dict[str, Any]]:
    """Return every live wiki page as a dict ready for
    ``core.workflow_graph_wiki.ingest_wiki_page``.

    Args:
        pg_store: read-only store exposing ``.query(sql, params, *,
            batch=True)`` (``MemoryReader`` — see
            ``infrastructure.memory_read``). Read-only: this function
            only SELECTs.

    Returns:
        ``[{"id": int, "title": str, "kind": str, "domain": str,
        "status": str, "heat": float, "rel_path": str,
        "memory_id": int | None}, ...]``. Empty list when the
        ``wiki.*`` schema is absent or the query otherwise fails
        (best-effort, matches ``infrastructure.wiki_pg``).
    """
    try:
        rows = pg_store.query(_WIKI_PAGES_SQL, (), batch=True)
    except Exception:
        return []
    return [dict(r) for r in rows]


def load_wiki_links(pg_store) -> list[dict[str, Any]]:
    """Return every page-to-page wiki link.

    Returns:
        ``[{"src_page_id": int, "dst_page_id": int,
        "link_kind": str | None}, ...]``. Empty list on any failure
        (best-effort).
    """
    try:
        rows = pg_store.query(_WIKI_LINKS_SQL, (), batch=True)
    except Exception:
        return []
    return [dict(r) for r in rows]


def load_wiki_memory_links(pg_store) -> list[dict[str, Any]]:
    """Return every page-to-memory link (anchor memory + citations).

    Returns:
        ``[{"page_id": int, "memory_id": int}, ...]``. Empty list on
        any failure (best-effort).
    """
    try:
        rows = pg_store.query(_WIKI_MEMORY_LINKS_SQL, (), batch=True)
    except Exception:
        return []
    return [dict(r) for r in rows]


def load_wiki_page_sources(pg_store) -> list[dict[str, Any]]:
    """Return every wiki-page -> source-file link (``wiki.page_sources``,
    ADR-0051), every ``link_kind`` included.

    Returns:
        ``[{"page_id": int, "source_path": str, "link_kind": str,
        "confidence": float}, ...]``. Empty list when the table is
        absent (pre-ADR-0051 databases) or the query otherwise fails
        (best-effort, matches the other three loaders in this module).
    """
    try:
        rows = pg_store.query(_WIKI_PAGE_SOURCES_SQL, (), batch=True)
    except Exception:
        return []
    return [dict(r) for r in rows]


__all__ = [
    "load_wiki_pages",
    "load_wiki_links",
    "load_wiki_memory_links",
    "load_wiki_page_sources",
]
