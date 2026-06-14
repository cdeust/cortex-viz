"""PG-backed wiki enrichment (thermodynamic page state, backlinks, memos).

The wiki's curated half lives on disk (``wiki_read``); its *living* half —
heat, citations, backlinks, decision memos — lives in the shared Cortex
``wiki.*`` schema. These reads are BEST-EFFORT: every function degrades to a
valid empty shape if the schema is absent or a row is missing, so the wiki
view never hangs waiting on enrichment (wiki.js treats all of this as
optional and ``.catch``es failures).
"""

from __future__ import annotations

from typing import Any


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _isodict(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _iso(v) for k, v in row.items()}


def page_meta(store, rel_path: str) -> dict[str, Any]:
    """Thermodynamic state + link graph for one page (by rel_path).

    Returns ``{rel_path, db_row, backlinks, outbound_links, recent_citations}``;
    ``db_row`` is None when the page isn't tracked in PG (pure-filesystem page).
    """
    empty = {
        "rel_path": rel_path,
        "db_row": None,
        "backlinks": [],
        "outbound_links": [],
        "recent_citations": [],
    }
    try:
        rows = store.query(
            """
            SELECT id, title, kind, domain, status, lifecycle_state, heat,
                   access_count, citation_count, backlink_count, is_stale,
                   planted, tended, last_cited_at, archived_at, memory_id,
                   concept_id
            FROM wiki.pages WHERE rel_path = %s LIMIT 1
            """,
            (rel_path,),
        )
    except Exception:
        return empty
    if not rows:
        return empty
    db_row = _isodict(rows[0])
    page_id = db_row["id"]
    try:
        backlinks = [
            _isodict(r)
            for r in store.query(
                """
                SELECT l.src_page_id, l.dst_slug, l.dst_page_id, l.link_kind,
                       (SELECT title FROM wiki.pages WHERE id = l.src_page_id) AS src_title,
                       (SELECT rel_path FROM wiki.pages WHERE id = l.src_page_id) AS src_rel_path
                FROM wiki.links l WHERE l.dst_page_id = %s LIMIT 100
                """,
                (page_id,),
            )
        ]
        outbound = [
            _isodict(r)
            for r in store.query(
                "SELECT dst_slug, dst_page_id, link_kind FROM wiki.links "
                "WHERE src_page_id = %s LIMIT 100",
                (page_id,),
            )
        ]
        citations = [
            _isodict(r)
            for r in store.query(
                "SELECT id, session_id, domain, memory_id, cited_at FROM wiki.citations "
                "WHERE page_id = %s ORDER BY cited_at DESC LIMIT 20",
                (page_id,),
            )
        ]
    except Exception:
        backlinks, outbound, citations = [], [], []
    return {
        "rel_path": rel_path,
        "db_row": db_row,
        "backlinks": backlinks,
        "outbound_links": outbound,
        "recent_citations": citations,
    }


def list_memos(
    store, subject_type: str, subject_id: Any, limit: int = 50
) -> dict[str, Any]:
    """Decision memos (audit trail) for a wiki subject (page/concept/draft)."""
    try:
        sid = int(subject_id)
    except (TypeError, ValueError):
        return {"memos": [], "count": 0}
    try:
        rows = store.query(
            "SELECT id, decision, rationale, alternatives, confidence, author, "
            "created_at, inputs FROM wiki.memos "
            "WHERE subject_type = %s AND subject_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (subject_type, sid, max(1, min(limit, 200))),
        )
    except Exception:
        return {"memos": [], "count": 0}
    memos = [_isodict(r) for r in rows]
    return {"memos": memos, "count": len(memos)}


__all__ = ["page_meta", "list_memos"]
