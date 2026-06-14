"""Keyset-paged memory browser + facet aggregation (read-only PG).

Backs the Knowledge and Board (timeline) views. Ported from the parent
Cortex viz server (``mcp_server/handlers/memories_page.py`` +
``memories_facets.py``); the response shapes match exactly so the extracted
``knowledge.js`` / ``timeline.js`` consume them unchanged.

Pure data access over a :class:`MemoryReader`. No HTTP — the handler layer
(``http_standalone_memories``) serializes these dicts to JSON.

Pagination is keyset (seek), not OFFSET: the cursor is a base64 JSON
``{"k": <sort value>, "id": <row id>}`` so page N costs the same as page 1.
"""

from __future__ import annotations

import base64
import json
from typing import Any

# sort key → (ORDER BY expression, direction). heat_base is COALESCEd so NULL
# heats sort deterministically instead of jumping under PG's NULLS FIRST/LAST.
_SORTS = {
    "heat": ("COALESCE(heat_base, 0)", "DESC"),
    "recent": ("created_at", "DESC"),
    "oldest": ("created_at", "ASC"),
}

# Columns the browser item needs. Kept explicit (not SELECT *) so the payload
# stays small and the shape is auditable.
_COLS = (
    "id, content, domain, tags, heat_base, importance, consolidation_stage, "
    "emotional_valence, dominant_emotion, arousal, created_at, last_accessed, "
    "is_protected, is_global, store_type, access_count, useful_count"
)


def _truthy(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on") if v is not None else False


def _decode_cursor(cursor: str | None) -> tuple[Any, int] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        obj = json.loads(raw)
        return obj["k"], int(obj["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _encode_cursor(k: Any, row_id: int) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"k": k, "id": row_id}).encode()
    ).decode()


def _emotion_clause(bucket: str) -> str | None:
    """SQL predicate for a facet emotion bucket (matches facet aggregation)."""
    return {
        "urgent": "importance >= 0.75",
        "positive": "emotional_valence >= 0.25 AND importance < 0.75",
        "negative": "emotional_valence <= -0.25 AND importance < 0.75",
        "neutral": (
            "emotional_valence > -0.25 AND emotional_valence < 0.25 "
            "AND importance < 0.75"
        ),
    }.get(bucket)


def _build_where(params: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """Assemble the filter WHERE clauses (excludes the keyset seek)."""
    clauses = ["NOT is_stale"]
    args: list[Any] = []
    domain = params.get("domain")
    if domain:
        if _truthy(params.get("include_global", "1")):
            clauses.append("(domain = %s OR is_global = TRUE)")
        else:
            clauses.append("domain = %s")
        args.append(domain)
    if params.get("stage"):
        clauses.append("consolidation_stage = %s")
        args.append(params["stage"])
    if params.get("search"):
        clauses.append("content_tsv @@ plainto_tsquery('english', %s)")
        args.append(params["search"])
    if params.get("min_heat"):
        try:
            clauses.append("COALESCE(heat_base, 0) >= %s")
            args.append(float(params["min_heat"]))
        except (TypeError, ValueError):
            pass
    emo = _emotion_clause(params.get("emotion") or "")
    if emo:
        clauses.append(emo)
    if _truthy(params.get("protected")):
        clauses.append("is_protected = TRUE")
    if _truthy(params.get("global")):
        clauses.append("is_global = TRUE")
    return clauses, args


def _excerpt(text: str, n: int = 90) -> str:
    s = " ".join((text or "").split())
    return s[: n - 1] + "…" if len(s) > n else s


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _row_to_item(r: dict[str, Any]) -> dict[str, Any]:
    """One memory row → the browser item shape (camel + snake aliases that
    knowledge.js / timeline.js and the detail panel both read)."""
    tags = r.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    domain = r.get("domain") or ""
    stage = r.get("consolidation_stage")
    return {
        "id": f"memory:{r['id']}",
        "memory_id": r["id"],
        "type": "memory",
        "kind": "memory",
        "label": _excerpt(r.get("content") or ""),
        "content": r.get("content") or "",
        "domain": domain,
        "domain_id": f"domain:{domain}" if domain else "",
        "tags": tags or [],
        "heat": r.get("heat_base"),
        "importance": r.get("importance"),
        "stage": stage,
        "consolidationStage": stage,
        "consolidation_stage": stage,
        "createdAt": _iso(r.get("created_at")),
        "created_at": _iso(r.get("created_at")),
        "lastAccessed": _iso(r.get("last_accessed")),
        "last_accessed": _iso(r.get("last_accessed")),
        "isProtected": bool(r.get("is_protected")),
        "is_protected": bool(r.get("is_protected")),
        "isGlobal": bool(r.get("is_global")),
        "is_global": bool(r.get("is_global")),
        "emotion": r.get("dominant_emotion"),
        "dominant_emotion": r.get("dominant_emotion"),
        "emotional_valence": r.get("emotional_valence"),
        "arousal": r.get("arousal"),
        "store_type": r.get("store_type"),
        "access_count": r.get("access_count"),
        "useful_count": r.get("useful_count"),
    }


def list_memories_page(store, params: dict[str, Any]) -> dict[str, Any]:
    """Return one keyset page: ``{items, next_cursor, page_count, sort}``."""
    sort = params.get("sort") or "heat"
    sort_expr, direction = _SORTS.get(sort, _SORTS["heat"])
    try:
        limit = max(1, min(int(params.get("limit", 50)), 5000))
    except (TypeError, ValueError):
        limit = 50

    clauses, args = _build_where(params)
    seek = _decode_cursor(params.get("cursor"))
    if seek is not None:
        cmp = "<" if direction == "DESC" else ">"
        clauses.append(f"({sort_expr}, id) {cmp} (%s, %s)")
        args.extend([seek[0], seek[1]])

    sql = (
        f"SELECT {_COLS} FROM memories WHERE {' AND '.join(clauses)} "
        f"ORDER BY {sort_expr} {direction}, id {direction} LIMIT %s"
    )
    args.append(limit)
    rows = store.query(sql, tuple(args))
    items = [_row_to_item(r) for r in rows]

    next_cursor = None
    if len(rows) == limit and rows:
        last = rows[-1]
        k = (
            float(last["heat_base"] or 0)
            if sort == "heat"
            else _iso(last["created_at"])
        )
        next_cursor = _encode_cursor(k, last["id"])
    return {
        "items": items,
        "next_cursor": next_cursor,
        "page_count": len(items),
        "sort": sort,
    }


def memory_facets(store) -> dict[str, Any]:
    """Aggregate filter facets for the browser chips (no filtering applied)."""
    domains = store.query(
        "SELECT COALESCE(NULLIF(domain, ''), '__unknown__') AS dom, COUNT(*) AS c "
        "FROM memories WHERE NOT is_stale GROUP BY dom ORDER BY c DESC LIMIT 200"
    )
    agg_rows = store.query(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE consolidation_stage = 'labile') AS s_labile,
          COUNT(*) FILTER (WHERE consolidation_stage = 'early_ltp') AS s_early,
          COUNT(*) FILTER (WHERE consolidation_stage = 'late_ltp') AS s_late,
          COUNT(*) FILTER (WHERE consolidation_stage = 'consolidated') AS s_cons,
          COUNT(*) FILTER (WHERE consolidation_stage = 'reconsolidating') AS s_recon,
          COUNT(*) FILTER (WHERE is_global = TRUE) AS n_global,
          COUNT(*) FILTER (WHERE is_protected = TRUE) AS n_protected,
          COUNT(*) FILTER (WHERE COALESCE(heat_base, 0) >= 0.5) AS n_hot,
          COUNT(*) FILTER (WHERE importance >= 0.75) AS e_urgent,
          COUNT(*) FILTER (
            WHERE emotional_valence >= 0.25 AND importance < 0.75) AS e_pos,
          COUNT(*) FILTER (
            WHERE emotional_valence <= -0.25 AND importance < 0.75) AS e_neg,
          COUNT(*) FILTER (
            WHERE emotional_valence > -0.25 AND emotional_valence < 0.25
              AND importance < 0.75) AS e_neutral
        FROM memories WHERE NOT is_stale
        """
    )
    a = agg_rows[0] if agg_rows else {}
    return {
        "total": a.get("total", 0),
        "domains": [{"name": r["dom"], "count": r["c"]} for r in domains],
        "stages": {
            "labile": a.get("s_labile", 0),
            "early_ltp": a.get("s_early", 0),
            "late_ltp": a.get("s_late", 0),
            "consolidated": a.get("s_cons", 0),
            "reconsolidating": a.get("s_recon", 0),
        },
        "emotions": {
            "urgent": a.get("e_urgent", 0),
            "positive": a.get("e_pos", 0),
            "negative": a.get("e_neg", 0),
            "neutral": a.get("e_neutral", 0),
        },
        "global": a.get("n_global", 0),
        "protected": a.get("n_protected", 0),
        "hot": a.get("n_hot", 0),
    }


__all__ = ["list_memories_page", "memory_facets"]
