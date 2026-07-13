"""HTTP endpoints for the domain-split execution-trace graph.

Live, no-snapshot serving of the four navigation levels:

    GET /api/trace/domains            -> L0 domain hubs
    GET /api/trace/sessions?domain=   -> L1 sessions + has_session edges
    GET /api/trace/chain?session=     -> L2 ordered prompt/action/file chain
    GET /api/trace/file?path=         -> L3 file drill (git + versions;
                                          add ?include=ast for AST + impact)

Each reads live from JSONL / AP graph / git per request — nothing cached
to disk.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from cortex_viz.server.git_diff_engine import diff_for_path
from cortex_viz.server.http_standalone_response import (
    send_json_error,
    send_json_ok,
)

# Impact / blast-radius computation + the warm AP AST-source singleton
# were split into ``trace_impact`` (500-line limit). Re-imported here for
# the serve_* glue below; re-exported so any historical import path keeps
# resolving.
from cortex_viz.server.trace_impact import (  # noqa: F401
    _ast_and_impact,
    _basename,
    _get_ast_source,
    _to_repo_relative,
    impact_for_path,
)


def _param(handler, key: str) -> str:
    qs = parse_qs(urlparse(handler.path).query)
    vals = qs.get(key)
    return unquote(vals[0]) if vals else ""


def serve_trace_domains(handler) -> None:
    """GET /api/trace/domains — collapsed domain hubs (L0)."""
    try:
        from cortex_viz.infrastructure.trace_source import list_domains

        nodes = list_domains()
        send_json_ok(
            handler,
            {"nodes": nodes, "edges": [], "meta": {"schema": "trace.v1", "level": 0}},
        )
    except Exception as e:
        send_json_error(handler, e)


def serve_trace_sessions(handler) -> None:
    """GET /api/trace/sessions?domain=<domain:id> — sessions in a domain (L1)."""
    try:
        from cortex_viz.infrastructure.trace_source import list_sessions

        domain = _param(handler, "domain")
        if not domain:
            send_json_ok(handler, {"nodes": [], "edges": [], "error": "missing domain"})
            return
        payload = list_sessions(domain)
        payload["meta"] = {"schema": "trace.v1", "level": 1, "domain": domain}
        send_json_ok(handler, payload)
    except Exception as e:
        send_json_error(handler, e)


def serve_trace_chain(handler) -> None:
    """GET /api/trace/chain?session=<sid> — ordered causal chain (L2)."""
    try:
        from cortex_viz.core.session_trace import build_chain
        from cortex_viz.infrastructure.trace_source import iter_session_events

        sid = _param(handler, "session")
        if not sid:
            send_json_ok(
                handler, {"nodes": [], "edges": [], "error": "missing session"}
            )
            return
        # ``since`` = chain steps the client already holds (live tail poll).
        # 0/absent → whole chain. Out-of-range → empty delta (dedup-safe).
        try:
            since = int(_param(handler, "since") or "0")
        except ValueError:
            since = 0
        events = iter_session_events(sid)
        payload = build_chain(events, sid, since=since)
        payload["meta"] = {
            "schema": "trace.v1",
            "level": 2,
            "session": sid,
            "event_count": len(events),
            "since": since,
        }
        send_json_ok(handler, payload)
    except Exception as e:
        send_json_error(handler, e)


def _file_git_root_rel(path: str) -> tuple[str | None, str]:
    """``(git_root, repo_relative_path)`` for a file — delegates entirely to
    the shared engine's ``repo_root_and_relpath`` (contract A.1/A.5:
    expanduser + symlink-canonicalize, walk to the nearest existing
    ancestor when the file's own directory is gone). Delegating instead of
    recomputing the relpath locally is load-bearing: an earlier version of
    this function reimplemented the relpath computation without
    canonicalizing symlinks, which silently disagreed with
    ``resolve_repo_root``'s own (canonicalizing) internals whenever an
    ancestor directory was a symlink — e.g. macOS's ``/tmp`` ->
    ``/private/tmp``. Returns ``(None, cleaned_path)`` outside a repo so
    callers report "no git data" rather than erroring.
    """
    import os

    from cortex_viz.server.git_diff_engine import repo_root_and_relpath

    root, rel, _reason = repo_root_and_relpath(path)
    if root is None:
        cleaned = os.path.expanduser(path or "").replace("\\", "/")
        return None, cleaned.lstrip("./").lstrip("/")
    return root, rel


def _git_history(path: str) -> dict:
    """Git diff for one file (contract A.2/A.5) — delegates to the shared
    engine so ``/api/trace/file`` and ``/api/file-diff`` never diverge.
    """
    return diff_for_path(path)


def _git_versions(path: str, limit: int = 25) -> dict:
    """Full commit history for one file — the 'versioning' axis.

    Returns ``{available, versions:[{sha, date, author, subject}]}`` from
    ``git log`` scoped to the file (follows renames). Pure git, no AP — a
    reliable longitudinal view of how this file changed over time, to sit
    next to AP's static dependency direction and causal chains.
    """
    try:
        import subprocess

        # Self-contained (the git_diff helpers were never ported). Resolve the
        # repo from the file's own path, not the server CWD.
        root, rel = _file_git_root_rel(path)
        if root is None:
            return {"available": False}
        rel = (rel or path or "").replace("\\", "/")
        # %x1f = unit separator (safe field delim); %x1e = record separator.
        fmt = "%h%x1f%aI%x1f%an%x1f%s%x1e"
        out = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--follow",
                f"-n{int(limit)}",
                f"--format={fmt}",
                "--",
                rel,
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if out.returncode != 0:
            return {"available": False, "error": (out.stderr or "").strip()[:200]}
        versions = []
        for rec in out.stdout.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            parts = rec.split("\x1f")
            if len(parts) < 4:
                continue
            versions.append(
                {
                    "sha": parts[0],
                    "date": parts[1],
                    "author": parts[2],
                    "subject": parts[3],
                }
            )
        return {"available": True, "versions": versions, "count": len(versions)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "error": str(exc)}


def serve_trace_file(handler) -> None:
    """GET /api/trace/file?path=<p> — L3 file drill: git + versions, AST opt-in.

    Contract A.5: without ``?include=ast`` this responds in milliseconds
    with ``{git, versions}`` only — the AST/impact bridge
    (``_ast_and_impact``, up to 20 MCP ``get_context`` calls, measured
    65-155s on first click) is added under the ``ast`` key only when the
    caller explicitly asks for it via ``include=ast``.
    """
    try:
        path = _param(handler, "path")
        if not path:
            send_json_ok(handler, {"error": "missing path"})
            return
        payload = {
            "path": path,
            "git": _git_history(path),
            "versions": _git_versions(path),
            "meta": {"schema": "trace.v1", "level": 3},
        }
        if _param(handler, "include") == "ast":
            payload["ast"] = _ast_and_impact(path)
        send_json_ok(handler, payload)
    except Exception as e:
        send_json_error(handler, e)


def serve_trace_impact(handler) -> None:
    """GET /api/trace/impact?path=<file> — dependency/impact subgraph.

    Blast-radius for a file from the AP code-graph:
      * downstream — what this file calls / imports (it depends on these)
      * upstream   — what calls this file (these break if it changes)
      * members    — symbols this file defines
    Queries the FIRST code-graph that contains the file (exact File.id
    match), so it hits the Cortex graph for a Cortex path rather than
    scanning all 6 graphs. ``{available: False, reason}`` when off / not
    indexed.
    """
    try:
        from cortex_viz.infrastructure import ap_bridge

        path = _param(handler, "path")
        if not path:
            send_json_ok(handler, {"available": False, "reason": "missing path"})
            return
        if not ap_bridge.is_enabled():
            send_json_ok(handler, {"available": False, "reason": "ap_disabled"})
            return

        # AP indexes File.id project-root-RELATIVE (e.g.
        # "mcp_server/server/http_standalone.py"). File nodes in the graph
        # carry the ABSOLUTE tool-call path, so the frontend sends an
        # absolute path here; the old ``lstrip("./")`` turned
        # "/Users/.../mcp_server/x.py" into "Users/.../mcp_server/x.py",
        # matching no File.id → every diagram returned "not_indexed". Strip
        # the git root so the exact ``f.id =`` match lands.
        # Several graphs may contain the same relative path (a stale legacy
        # index AND the fresh Cortex code-graph). impact_for_path picks the
        # RICHEST result (most call/import edges) across all graphs — the same
        # lookup the live activity-impact pass (P3) uses.
        result = impact_for_path(path)

        if result is None:
            send_json_ok(
                handler,
                {
                    "available": False,
                    "reason": "not_indexed",
                    "path": path,
                    "center": {"file": path, "label": _basename(path)},
                },
            )
            return

        result.update(
            {
                "available": True,
                "path": path,
                "center": {"file": path, "label": _basename(path)},
                "versions": _git_versions(path),
                "meta": {"schema": "trace.v1", "level": 4},
            }
        )
        send_json_ok(handler, result)
    except Exception as e:
        send_json_error(handler, e)


__all__ = [
    "serve_trace_domains",
    "serve_trace_sessions",
    "serve_trace_chain",
    "serve_trace_file",
    "serve_trace_impact",
]
