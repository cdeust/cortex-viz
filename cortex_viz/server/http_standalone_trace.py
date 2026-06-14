"""HTTP endpoints for the domain-split execution-trace graph.

Live, no-snapshot serving of the four navigation levels:

    GET /api/trace/domains            -> L0 domain hubs
    GET /api/trace/sessions?domain=   -> L1 sessions + has_session edges
    GET /api/trace/chain?session=     -> L2 ordered prompt/action/file chain
    GET /api/trace/file?path=         -> L3 file drill (AST + impact + git)

Each reads live from JSONL / AP graph / git per request — nothing cached
to disk.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

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
    _impact_for_graph,
    _to_repo_relative,
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


def _git_history(path: str) -> dict:
    """Working-tree/last-commit diff + when-changed for one file."""
    try:
        from cortex_viz.infrastructure.git_diff import (
            find_git_root,
            get_file_diff,
            resolve_file,
        )
        from cortex_viz.server.http_file_diff import _git_root_for_name

        # Resolve the repo from the FILE's own path (graph nodes carry
        # absolute paths), not the server CWD — which is never a repo.
        root = _git_root_for_name(path, find_git_root)
        if root is None:
            return {"available": False}
        rel = resolve_file(path, root) or path
        diff = get_file_diff(rel, root)
        return {
            "available": True,
            "diff_type": diff.get("diff_type"),
            "lines": diff.get("lines", []),
            "truncated": diff.get("truncated", False),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "error": str(exc)}


def _git_versions(path: str, limit: int = 25) -> dict:
    """Full commit history for one file — the 'versioning' axis.

    Returns ``{available, versions:[{sha, date, author, subject}]}`` from
    ``git log`` scoped to the file (follows renames). Pure git, no AP — a
    reliable longitudinal view of how this file changed over time, to sit
    next to AP's static dependency direction and causal chains.
    """
    try:
        import subprocess

        from cortex_viz.infrastructure.git_diff import find_git_root, resolve_file
        from cortex_viz.server.http_file_diff import _git_root_for_name

        # Resolve the repo from the file's own path, not the server CWD.
        root = _git_root_for_name(path, find_git_root)
        if root is None:
            return {"available": False}
        rel = (resolve_file(path, root) or path or "").replace("\\", "/")
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
    """GET /api/trace/file?path=<p> — L3 file drill: AST + impact + git."""
    try:
        path = _param(handler, "path")
        if not path:
            send_json_ok(handler, {"error": "missing path"})
            return
        send_json_ok(
            handler,
            {
                "path": path,
                "git": _git_history(path),
                "versions": _git_versions(path),
                "ast": _ast_and_impact(path),
                "meta": {"schema": "trace.v1", "level": 3},
            },
        )
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
        rel = _to_repo_relative(path)
        # Several graphs may contain the same relative path (a stale legacy
        # index AND the fresh Cortex code-graph). The first hit can be the
        # stale one with members-only and no edges, so pick the RICHEST
        # result (most call/import edges) across all graphs that have it.
        result = None
        best_edges = -1
        for gp in ap_bridge.resolve_graph_paths():
            try:
                r = _impact_for_graph(gp, rel)
            except Exception:
                r = None
            if r is None:
                continue
            n = (
                len(r.get("downstream", []))
                + len(r.get("upstream", []))
                + len(r.get("members", []))
                + len(r.get("processes", []))
                + len(r.get("references", []))
                + len(r.get("referenced_by", []))
                + len(r.get("depends_on", []))
                + len(r.get("depended_on_by", []))
            )
            if n > best_edges:
                best_edges = n
                result = r

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
