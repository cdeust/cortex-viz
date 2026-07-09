"""Wiki HTTP endpoints (Wiki view).

GET  /api/wiki/list                 — page tree (filesystem)
GET  /api/wiki/projects             — pages grouped by domain
GET  /api/wiki/page?path=           — one page {path, meta, body}
GET  /api/wiki/page_meta?path=      — PG state + backlinks (best-effort)
GET  /api/wiki/memos?subject_*=     — decision memos (best-effort)
GET  /api/wiki/actions?page_id=     — live Claude activity on a page's
                                       source files (best-effort)
GET  /api/wiki/bibliography         — .bib files
GET  /api/wiki/bibliography/read?path= — one .bib file
POST /api/wiki/save  {rel_path, body} — overwrite a page

Composition root: filesystem reads in ``infrastructure.wiki_read``, PG reads
in ``infrastructure.wiki_pg`` / ``infrastructure.wiki_page_actions_pg`` /
``infrastructure.activity_store``. Unknown ``/api/wiki/*`` paths return a
valid empty shape (never 410/404) so wiki.js never hangs on a missing op.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from cortex_viz.infrastructure import wiki_pg, wiki_read
from cortex_viz.server.http_standalone_response import send_json_error, send_json_ok

# Response bound for /api/wiki/actions — reuses the replay-log cap
# ``infrastructure.activity_store.read_recent`` already uses as its
# session_activity replay bound, so one number governs "how much
# session_activity history a single response can carry" everywhere it's
# read. source: activity_store.py's ``read_recent(limit: int = 2000)``.
_ACTIONS_DEFAULT_LIMIT = 200
_ACTIONS_MAX_LIMIT = 2000


def _params(handler) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items() if v}


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _source_summary(sources: list[dict], source_target_ids: dict[str, str]) -> list[dict]:
    return [
        {
            "source_path": s.get("source_path"),
            "link_kind": s.get("link_kind"),
            "resolved": s.get("source_path") in source_target_ids,
        }
        for s in sources
    ]


def _action_view(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "session_id": r.get("session_id"),
        "ts": r.get("ts"),
        "tool": r.get("tool"),
        "action": r.get("action"),
        "target_label": r.get("target_label"),
        "edge_kind": r.get("edge_kind"),
        "source_path": r.get("source_path"),
    }


def _fetch_matched_actions(store, source_target_ids: dict[str, str]) -> list[dict]:
    """Fast-path canonical rows + bounded legacy scan, joined
    (``core.wiki_page_actions.match_activity_rows`` — see that module's
    docstring for why BOTH fetches are needed: pre- and post-P4 rows use
    different ``target_id`` shapes)."""
    from cortex_viz.core.wiki_page_actions import match_activity_rows
    from cortex_viz.infrastructure import activity_store

    canonical_rows = activity_store.find_by_target_ids(
        store, list(source_target_ids.values()), limit=_ACTIONS_MAX_LIMIT
    )
    legacy_rows = activity_store.scan_legacy_file_rows(store, limit=_ACTIONS_MAX_LIMIT)
    return match_activity_rows(source_target_ids, canonical_rows, legacy_rows)


def _page_actions(store, params: dict[str, str]) -> dict:
    """``GET /api/wiki/actions?page_id=<id>&limit=<n>`` — the live Claude
    actions (``session_activity`` rows) that touched one wiki page's source
    files.

    Join: ``wiki.page_sources`` (page -> ``source_path``) -> reconstructed
    absolute path -> the SAME ``file:<hash>`` id the P4-unified activity
    spine now mints (``core.activity_paths`` / ``core.wiki_source_resolve``)
    -> matching ``session_activity`` rows (``core.wiki_page_actions``).
    Degrades to a valid empty ``{actions: []}`` shape — never an error —
    for: a malformed/missing ``page_id``, a page with no PG row, a page
    with no ``wiki.page_sources`` rows, sources whose domain has no known
    filesystem root, or simply no activity yet on the resolved files.
    """
    from cortex_viz.core.wiki_page_actions import resolve_source_target_ids
    from cortex_viz.core.workflow_graph_schema import NodeIdFactory
    from cortex_viz.infrastructure.wiki_page_actions_pg import (
        load_page_by_id,
        load_page_sources,
    )

    try:
        page_id = int(params.get("page_id", ""))
    except (TypeError, ValueError):
        return {
            "ok": True, "page_id": None, "sources": [], "actions": [],
            "count": 0, "note": "missing_or_invalid_page_id",
        }

    page = load_page_by_id(store, page_id)
    if page is None:
        return {
            "ok": True, "page_id": page_id, "sources": [], "actions": [],
            "count": 0, "note": "page_not_found",
        }

    sources = load_page_sources(store, page_id)
    domain_id = NodeIdFactory.domain_id(page.get("domain"))
    source_target_ids = resolve_source_target_ids(domain_id, sources)
    source_summary = _source_summary(sources, source_target_ids)
    base = {
        "ok": True, "page_id": page_id, "rel_path": page.get("rel_path"),
        "sources": source_summary,
    }
    if not source_target_ids:
        return {**base, "actions": [], "count": 0}

    limit = max(1, min(_safe_int(params.get("limit"), _ACTIONS_DEFAULT_LIMIT), _ACTIONS_MAX_LIMIT))
    matched = _fetch_matched_actions(store, source_target_ids)
    truncated = len(matched) > limit
    matched = matched[:limit]
    return {
        **base,
        "actions": [_action_view(r) for r in matched],
        "count": len(matched),
        "truncated": truncated,
    }


def _dispatch_get(store, path_no_qs: str, p: dict[str, str]) -> dict:
    if path_no_qs == "/api/wiki/list":
        return wiki_read.list_pages()
    if path_no_qs == "/api/wiki/projects":
        return wiki_read.list_projects()
    if path_no_qs == "/api/wiki/page":
        return wiki_read.read_page(p.get("path", ""))
    if path_no_qs == "/api/wiki/page_meta":
        return wiki_pg.page_meta(store, p.get("path", ""))
    if path_no_qs == "/api/wiki/memos":
        return wiki_pg.list_memos(
            store,
            p.get("subject_type", "page"),
            p.get("subject_id"),
            _safe_int(p.get("limit"), 50),
        )
    if path_no_qs == "/api/wiki/actions":
        return _page_actions(store, p)
    if path_no_qs == "/api/wiki/bibliography":
        return wiki_read.list_bibliography()
    if path_no_qs == "/api/wiki/bibliography/read":
        return wiki_read.read_bibliography(p.get("path", ""))
    # Unknown wiki op (concepts/drafts/views/…): a valid empty shape, NOT 410 —
    # so any stray frontend call degrades instead of hanging the view.
    return {"ok": True, "items": [], "note": "not_served_by_viz"}


def serve_wiki(handler, store, path_no_qs: str) -> None:
    """GET dispatch for the /api/wiki/* family."""
    try:
        send_json_ok(handler, _dispatch_get(store, path_no_qs, _params(handler)))
    except Exception as e:  # pragma: no cover - defensive
        send_json_error(handler, e)


def serve_wiki_save(handler) -> None:
    """POST /api/wiki/save — overwrite a page from {rel_path, body}."""
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
        payload = json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, json.JSONDecodeError) as e:
        send_json_error(handler, e, status=400)
        return
    rel_path = payload.get("rel_path") or payload.get("path") or ""
    body = payload.get("body")
    if body is None:
        body = payload.get("content", "")
    result = wiki_read.save_page(rel_path, body)
    status = 200 if result.get("ok") else 400
    if status == 200:
        send_json_ok(handler, result)
    else:
        # Shape parity with the frontend's {error} expectation.
        send_json_error(
            handler, RuntimeError(result.get("error", "save failed")), status=400
        )


__all__ = ["serve_wiki", "serve_wiki_save"]
