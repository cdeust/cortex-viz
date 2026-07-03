"""GET /api/file-diff?name=<path> — git diff for a file node.

Ported (2026-07-03) to fill a crashing stub: ``serve_file_diff`` in
``http_standalone_endpoints`` delegated to this module, which never existed —
so every diff request raised ModuleNotFoundError and reset the connection
(the detail panel's "See diff" showed nothing in BOTH the galaxy and the
brain view). This restores the feature.

Response contract (consumed verbatim by ui/unified/js/detail_diff.js
``renderFromGit`` / ``renderModal``)::

    {
      "diff_type": "uncommitted" | "staged" | "last_commit"
                   | "untracked" | "none",
      "lines": [ {"type": "hunk"|"add"|"del"|"context", "text": "..."} ],
      "truncated": bool,
      "reason": "<optional human note>"
    }

Resolution order mirrors what a developer would look at first: unstaged
working-tree changes, then staged, then the last commit that touched the
file, then (for a brand-new file git doesn't track yet) its full content as
additions. Read-only: only ``git diff`` / ``git log`` / ``git ls-files``,
never anything that mutates the tree.
"""

from __future__ import annotations

import os
import subprocess
from urllib.parse import parse_qs, urlparse

from cortex_viz.server.http_standalone_response import send_json_error, send_json_ok

# Cap the rendered diff so a huge file (or a full-content new file) can't ship
# a multi-MB payload the modal would choke on. source: mirrors the galaxy's
# ~2k-line diff budget; larger diffs set truncated=True.
_MAX_LINES = 2000
_GIT_TIMEOUT_S = 6


def _git(root: str, args: list[str]) -> str | None:
    """Run ``git -C root <args>`` read-only; return stdout or None on failure."""
    try:
        return subprocess.check_output(
            ["git", "-C", root, *args],
            stderr=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_S,
        ).decode("utf-8", "replace")
    except Exception:
        return None


def _repo_root(path: str) -> str | None:
    """Git toplevel for ``path``'s directory, or None if not in a repo."""
    directory = os.path.dirname(path) or "."
    out = _git(directory, ["rev-parse", "--show-toplevel"])
    return out.strip() if out else None


def _is_untracked(root: str, rel: str) -> bool:
    """True when the file exists but git does not yet track it."""
    tracked = _git(root, ["ls-files", "--error-unmatch", "--", rel])
    return tracked is None or tracked.strip() == ""


def _parse_unified(diff_text: str) -> tuple[list[dict], bool]:
    """Parse ``git diff`` unified output into typed {type,text} lines.

    Skips the diff header noise (``diff --git``, ``index``, ``---``, ``+++``,
    ``new file mode`` …) and keeps only hunks and their content lines, which
    is exactly what the modal renders. Returns (lines, truncated).
    """
    lines: list[dict] = []
    truncated = False
    for raw in diff_text.splitlines():
        if len(lines) >= _MAX_LINES:
            truncated = True
            break
        if raw.startswith("@@"):
            lines.append({"type": "hunk", "text": raw})
        elif raw.startswith("+++") or raw.startswith("---"):
            continue  # file-header markers, not content
        elif raw.startswith("+"):
            lines.append({"type": "add", "text": raw[1:]})
        elif raw.startswith("-"):
            lines.append({"type": "del", "text": raw[1:]})
        elif raw.startswith(" "):
            lines.append({"type": "context", "text": raw[1:]})
        # diff --git / index / mode lines fall through (ignored)
    return lines, truncated


def _full_content_as_adds(root: str, rel: str) -> tuple[list[dict], bool]:
    """Render an untracked file's whole content as additions."""
    abs_path = os.path.join(root, rel)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read().splitlines()
    except OSError:
        return [], False
    truncated = len(body) > _MAX_LINES
    body = body[:_MAX_LINES]
    lines: list[dict] = [{"type": "hunk", "text": f"@@ -0,0 +1,{len(body)} @@"}]
    lines += [{"type": "add", "text": ln} for ln in body]
    return lines, truncated


def _resolve_diff(root: str, rel: str) -> dict:
    """Pick the most relevant diff for ``rel`` and return the response dict."""
    # 1. Untracked new file — no history to diff against; show full content.
    if _is_untracked(root, rel):
        if os.path.isfile(os.path.join(root, rel)):
            lines, trunc = _full_content_as_adds(root, rel)
            return {"diff_type": "untracked", "lines": lines, "truncated": trunc}
        return {"diff_type": "none", "lines": [], "truncated": False,
                "reason": "not tracked and not found on disk"}

    # 2. Unstaged working-tree changes.
    wt = _git(root, ["diff", "--unified=3", "--", rel])
    if wt and wt.strip():
        lines, trunc = _parse_unified(wt)
        return {"diff_type": "uncommitted", "lines": lines, "truncated": trunc}

    # 3. Staged (index) changes.
    staged = _git(root, ["diff", "--cached", "--unified=3", "--", rel])
    if staged and staged.strip():
        lines, trunc = _parse_unified(staged)
        return {"diff_type": "staged", "lines": lines, "truncated": trunc}

    # 4. Last commit that touched the file.
    last = _git(root, ["log", "-1", "-p", "--unified=3", "--", rel])
    if last and last.strip():
        lines, trunc = _parse_unified(last)
        if lines:
            return {"diff_type": "last_commit", "lines": lines, "truncated": trunc}

    return {"diff_type": "none", "lines": [], "truncated": False,
            "reason": "no working, staged, or committed changes"}


def serve_file_diff(handler) -> None:
    """GET /api/file-diff?name=<absolute-or-repo-path>. See module docstring."""
    try:
        params = parse_qs(urlparse(handler.path).query)
        name = (params.get("name") or [""])[0]
        if not name:
            send_json_ok(handler, {"diff_type": "none", "lines": [],
                                   "truncated": False, "reason": "no file given"})
            return
        root = _repo_root(name)
        if root is None:
            send_json_ok(handler, {"diff_type": "none", "lines": [],
                                   "truncated": False, "reason": "not in a git repo"})
            return
        rel = os.path.relpath(name, root)
        send_json_ok(handler, _resolve_diff(root, rel))
    except Exception as e:  # pragma: no cover - defensive
        send_json_error(handler, e)
