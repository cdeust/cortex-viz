"""Pure mapping: a wiki page's source files -> the live activity rows that
touched them (``GET /api/wiki/actions``).

Bridges ADR-0051's ``wiki.page_sources`` (page -> ``source_path``) to the
``session_activity`` spine by reusing ``core.wiki_source_resolve`` (the
SAME ``source_path`` -> ``abs_path`` -> ``file:<hash>`` reconstruction the
wiki->FILE graph edge already relies on) and the P4 path-unification join
key (``core.activity_paths``) that makes activity ``target_id``s comparable
to that resolved id in the first place — this endpoint would return nothing
useful without Task A's fix.

Pure: no I/O. The infrastructure layer (``infrastructure.wiki_page_actions_pg``
for ``wiki.pages``/``wiki.page_sources``, ``infrastructure.activity_store``
for ``session_activity``) fetches the rows this module joins.
"""

from __future__ import annotations

from typing import Any

from cortex_viz.core.activity_paths import (
    canonical_file_id_for_legacy,
    is_canonical_file_target_id,
)
from cortex_viz.core.wiki_source_resolve import resolve_file_node_id


def resolve_source_target_ids(
    domain_id: str | None, sources: list[dict[str, Any]]
) -> dict[str, str]:
    """``source_path -> file:<hash>`` for every source whose domain has a
    known filesystem source root. Sources for a memory-only domain (no
    checked-out repo) are silently dropped — matches
    ``resolve_file_node_id``'s no-fabricated-node contract; the endpoint
    degrades to an empty ``actions`` list rather than a fabricated match.
    """
    out: dict[str, str] = {}
    for src in sources:
        sp = src.get("source_path")
        tid = resolve_file_node_id(domain_id, sp)
        if tid:
            out[sp] = tid
    return out


def match_activity_rows(
    source_target_ids: dict[str, str],
    canonical_rows: list[dict[str, Any]],
    legacy_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join fetched ``session_activity`` rows against the resolved
    ``target_id`` set for one page's sources.

    ``canonical_rows`` already carry the post-P4 ``file:<hash>`` id
    (fetched via ``activity_store.find_by_target_ids`` — an indexed
    equality lookup, the fast path covering every row written after Task
    A's fix). ``legacy_rows`` are pre-P4 rows (``activity_store.
    scan_legacy_file_rows``, a bounded newest-first scan) whose ``target_id``
    still embeds the raw literal path; each is re-canonicalized here with
    the SAME formula ``core.activity_graph.event_to_graph`` uses for SSE
    replay, so activity captured before the fix shipped still joins to its
    wiki page. Returns rows newest-first, each tagged with the
    ``source_path`` it matched (and, for healed legacy rows, the
    recomputed canonical ``target_id``).
    """
    # ``source_target_ids`` is keyed by source_path (its producer,
    # ``resolve_source_target_ids``, documents ``source_path -> target_id``
    # — the natural direction for building it). The join below needs the
    # inverse (target_id -> source_path) to look up an activity row's
    # target_id in O(1); invert once here rather than at every call site.
    source_by_target_id = {tid: sp for sp, tid in source_target_ids.items()}

    matches: list[dict[str, Any]] = []
    for row in canonical_rows:
        sp = source_by_target_id.get(row.get("target_id") or "")
        if sp:
            r = dict(row)
            r["source_path"] = sp
            matches.append(r)
    for row in legacy_rows:
        raw_tid = row.get("target_id") or ""
        if is_canonical_file_target_id(raw_tid):
            continue  # already covered by the fast path above
        healed = canonical_file_id_for_legacy(raw_tid, row.get("cwd") or "")
        sp = source_by_target_id.get(healed)
        if sp:
            r = dict(row)
            r["target_id"] = healed
            r["source_path"] = sp
            matches.append(r)
    matches.sort(key=lambda r: r.get("id") or 0, reverse=True)
    return matches


__all__ = ["resolve_source_target_ids", "match_activity_rows"]
