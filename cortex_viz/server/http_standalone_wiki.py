"""Wiki HTTP endpoints (Wiki view).

GET  /api/wiki/list                 — page tree (filesystem)
GET  /api/wiki/projects             — pages grouped by domain
GET  /api/wiki/page?path=           — one page {path, meta, body}
GET  /api/wiki/page_meta?path=      — PG state + backlinks (best-effort)
GET  /api/wiki/memos?subject_*=     — decision memos (best-effort)
GET  /api/wiki/bibliography         — .bib files
GET  /api/wiki/bibliography/read?path= — one .bib file
POST /api/wiki/save  {rel_path, body} — overwrite a page

Composition root: filesystem reads in ``infrastructure.wiki_read``, PG reads
in ``infrastructure.wiki_pg``. Unknown ``/api/wiki/*`` paths return a valid
empty shape (never 410/404) so wiki.js never hangs on a missing op.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from cortex_viz.infrastructure import wiki_pg, wiki_read
from cortex_viz.server.http_standalone_response import send_json_error, send_json_ok


def _params(handler) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items() if v}


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
