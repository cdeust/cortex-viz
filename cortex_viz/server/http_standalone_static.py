"""Sandboxed static-file endpoints for the standalone HTTP server.

Split out of ``http_standalone_endpoints`` (500-line limit, §4.1) — this
is a distinct concern from the graph/discussion endpoints in that
module: reading files off disk under a traversal guard, rather than
shaping a JSON response from the store.

Owns:

* ``serve_static`` — sandboxed reader for ``/js/`` + ``/css/``
* ``serve_shared_asset`` — sandboxed reader for the vendored
  design-system foundation (``/shared/``, nested token/component tree)
* ``serve_file_diff`` — thin delegate to ``http_file_diff``
"""

from __future__ import annotations

import re
from pathlib import Path

from cortex_viz.server.http_standalone_response import send_plain_error

# Content types for the shared design-system foundation (ui/shared/*). Kept
# small and explicit — the foundation ships only these kinds.
_SHARED_CONTENT_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".json": "application/json",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".svg": "image/svg+xml",
}


def serve_static(handler, base_dir: Path, filename: str, content_type: str) -> None:
    """Sandboxed read-only static-file reader for ``/js/`` and ``/css/``.

    Security: strip directory components, reject hidden files / null
    bytes / non-alphanumeric names, match against a directory-listing
    whitelist so the user-supplied path never drives the filesystem
    read.
    """
    safe_name = Path(filename).name
    if (
        not safe_name
        or safe_name.startswith(".")
        or "\x00" in safe_name
        or not re.match(r"^[\w][\w.\-]*$", safe_name)
    ):
        send_plain_error(handler, 403)
        return
    resolved_base = base_dir.resolve()
    actual_files = {f.name: f for f in resolved_base.iterdir() if f.is_file()}
    if safe_name not in actual_files:
        send_plain_error(handler, 404)
        return
    body = actual_files[safe_name].read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type + "; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def serve_shared_asset(handler, shared_dir: Path, rel_path: str) -> None:
    """Sandboxed reader for the vendored design-system foundation (``/shared/``).

    Unlike ``serve_static`` (single-level, filename-only) this serves the
    NESTED token/component tree — ``tokens/colors.css``, ``components/core/
    core.css`` — because ``ds.css`` ``@import``s them by relative subpath. The
    depth is the reason for a distinct reader; the traversal guard is the price:
    the resolved path MUST stay inside ``shared_dir`` and be an existing file,
    so a crafted ``../`` can never escape the foundation directory.
    """
    # Reject the obvious attacks before touching the filesystem.
    if (
        not rel_path
        or "\x00" in rel_path
        or any(
            part in ("", "..") or part.startswith(".") for part in rel_path.split("/")
        )
    ):
        send_plain_error(handler, 403)
        return
    base = shared_dir.resolve()
    target = (base / rel_path).resolve()
    # Containment check — the resolved path must be within the foundation dir.
    if base != target and base not in target.parents:
        send_plain_error(handler, 403)
        return
    if not target.is_file():
        send_plain_error(handler, 404)
        return
    content_type = _SHARED_CONTENT_TYPES.get(target.suffix.lower(), "text/plain")
    body = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type + "; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def serve_file_diff(handler, store=None) -> None:
    """Thin delegate to ``http_file_diff.serve_file_diff``.

    ``store`` is threaded through for the basename/relative-name resolution
    ladder (contract A.3), which needs the activity spine in PG.
    """
    from cortex_viz.server.http_file_diff import serve_file_diff as _serve

    _serve(handler, store)
