"""PG reads backing ``GET /api/wiki/actions`` — one wiki page's identity
(domain, for the ``wiki_source_resolve`` join) plus its ``wiki.page_sources``
rows. Split out of ``wiki_graph.py`` (which loads EVERY page's sources for
the full workflow-graph build) because this endpoint needs exactly ONE
page, filtered server-side rather than fetched-then-filtered in Python.

Best-effort, matching ``infrastructure.wiki_pg``'s style: every function
degrades to ``None`` / ``[]`` on any exception or absent schema so the
endpoint returns a valid empty shape instead of a 500.
"""

from __future__ import annotations

from typing import Any


def load_page_by_id(store, page_id: int) -> dict[str, Any] | None:
    """One wiki page's ``{id, domain, rel_path, title}`` by id, or ``None``
    when the page doesn't exist / the ``wiki.*`` schema is absent."""
    try:
        rows = store.query(
            "SELECT id, domain, rel_path, title FROM wiki.pages WHERE id = %s LIMIT 1",
            (page_id,),
        )
    except Exception:
        return None
    return dict(rows[0]) if rows else None


def load_page_sources(store, page_id: int) -> list[dict[str, Any]]:
    """``wiki.page_sources`` rows for ONE page (every ``link_kind`` —
    ``documents``/``references``/... — matches ``wiki_graph.
    load_wiki_page_sources``'s "read them all" convention)."""
    try:
        rows = store.query(
            "SELECT source_path, link_kind, confidence FROM wiki.page_sources "
            "WHERE page_id = %s",
            (page_id,),
        )
    except Exception:
        return []
    return [dict(r) for r in rows]


__all__ = ["load_page_by_id", "load_page_sources"]
