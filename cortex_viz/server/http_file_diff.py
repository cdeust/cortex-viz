"""GET /api/file-diff?name=<path> — git diff for a file node.

Ported (2026-07-03) to fill a crashing stub: ``serve_file_diff`` in
``http_standalone_endpoints`` delegated to this module, which never existed —
so every diff request raised ModuleNotFoundError and reset the connection
(the detail panel's "See diff" showed nothing in BOTH the galaxy and the
brain view). This restores the feature.

Response contract (consumed verbatim by ui/unified/js/detail_diff.js
``renderFromGit`` / ``renderModal``)::

    {
      "available": bool,
      "diff_type": "uncommitted" | "last_commit" | "untracked" | "none",
      "lines": [ {"type": "hunk"|"add"|"del"|"context", "text": "..."} ],
      "truncated": bool,
      "reason": "<optional human note>",
      "commit": {"sha": "...", "subject": "..."}  # only for last_commit
    }

The diff ladder itself lives in ``git_diff_engine`` (shared with
``/api/trace/file``) — this module's job is purely resolving ``name`` (which
may be an absolute path, a bare basename, or a repo-relative fragment) to an
absolute filesystem path before handing off to the engine (contract A.3).
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

from cortex_viz.server.git_diff_engine import diff_for_path
from cortex_viz.server.http_standalone_response import send_json_error, send_json_ok

# Re-exported for tests/test_file_diff.py and any historical import path.
from cortex_viz.server.git_diff_engine import (  # noqa: F401
    _MAX_LINES,
    _full_content_as_adds,
    _parse_unified,
    _resolve_diff,
)


def _resolve_by_basename(store, name: str) -> tuple[str | None, str | None]:
    """Bare basename (no ``/``) → absolute path via the activity spine.

    Returns ``(abs_path, reason)``; ``reason`` set only on failure.
    """
    if store is None:
        return None, "unresolved basename: activity store unavailable"
    from cortex_viz.infrastructure.activity_store import find_abs_path_by_label

    try:
        found = find_abs_path_by_label(store, name)
    except Exception:
        return None, "unresolved basename: activity store lookup failed"
    if found:
        return found, None
    return None, "unresolved basename: not found in activity index"


def _resolve_by_relative_fragment(store, name: str) -> tuple[str | None, str | None]:
    """Repo-relative fragment (contains ``/``) → absolute path.

    First tries every known repo root from the domain registry
    (``<repo.fs_path>/<name>`` exists on disk); falls back to a suffix
    search over the activity spine. Returns ``(abs_path, reason)``.
    """
    from cortex_viz.shared.domain_mapping import _build_registry

    for repo in _build_registry().repos:
        candidate = os.path.join(repo.fs_path, name)
        if os.path.exists(candidate):
            return candidate, None

    if store is None:
        return None, "unresolved relative name: not found in known repos"
    from cortex_viz.infrastructure.activity_store import find_abs_path_by_suffix

    try:
        found = find_abs_path_by_suffix(store, name)
    except Exception:
        return None, "unresolved relative name: activity store lookup failed"
    if found:
        return found, None
    return None, "unresolved relative name: not found in activity index or known repos"


def _resolve_name(store, name: str) -> tuple[str | None, str | None]:
    """``name`` → absolute path (contract A.3). Never falls back to server CWD."""
    expanded = os.path.expanduser(name)
    if expanded.startswith("/"):
        return expanded, None
    if "/" in expanded:
        return _resolve_by_relative_fragment(store, expanded)
    return _resolve_by_basename(store, expanded)


def serve_file_diff(handler, store=None) -> None:
    """GET /api/file-diff?name=<absolute-path|basename|repo-relative>.

    See module docstring for the response contract. ``store`` is optional —
    when absent (or unreachable), basename/suffix resolution against the
    activity spine degrades cleanly to a "not found" reason instead of
    crashing.
    """
    try:
        params = parse_qs(urlparse(handler.path).query)
        name = (params.get("name") or [""])[0]
        if not name:
            send_json_ok(
                handler,
                {
                    "available": False,
                    "diff_type": "none",
                    "lines": [],
                    "truncated": False,
                    "reason": "no file given",
                },
            )
            return
        abs_path, reason = _resolve_name(store, name)
        if abs_path is None:
            send_json_ok(
                handler,
                {
                    "available": False,
                    "diff_type": "none",
                    "lines": [],
                    "truncated": False,
                    "reason": reason,
                },
            )
            return
        send_json_ok(handler, diff_for_path(abs_path))
    except Exception as e:  # pragma: no cover - defensive
        send_json_error(handler, e)
