"""Impact / blast-radius computation for the execution-trace file drill.

Split out of ``http_standalone_trace.py`` (was 772 lines) to respect the
500-line file limit. Holds the warm AP AST-source singleton plus the pure
impact-graph computation (``_impact_for_graph``, ``_ast_and_impact``,
path normalisation). The serve/format glue stays in
``http_standalone_trace``, which re-exports these so call sites and tests
keep resolving.
"""

from __future__ import annotations

from cortex_viz.server.trace_impact_directions import (
    _basename,
    _blast_radius,
    _fallback_directions,
    _file_edges,
    _processes_from,
    _rollup,
    _short_name,
    _typed_directions,
)

# Re-exported for ``http_standalone_trace``, which imports ``_basename``
# from this module and predates the directions split.
__all__ = ["_basename", "impact_for_path"]

# ── AP AST source: ONE warm instance per viz process ───────────────────
# WorkflowGraphASTSource pins a single event loop on a dedicated thread
# (_SyncLoop) and keeps the AP MCP connection alive across calls. The old
# code spawned a fresh APBridge + asyncio.run() per request, which failed
# to connect from the detached viz subprocess ("connect_failed"). A
# module-level singleton connects once and is reused, and its label-by-
# label queries match AP's LadybugDB schema (a single multi-label MATCH
# is rejected by the engine). source: 2026-05-31 Phase 2 warm-pool.
_AST_SOURCE = None
_AST_SOURCE_LOCK = None


def _get_ast_source():
    global _AST_SOURCE, _AST_SOURCE_LOCK
    if _AST_SOURCE_LOCK is None:
        import threading

        _AST_SOURCE_LOCK = threading.Lock()
    with _AST_SOURCE_LOCK:
        if _AST_SOURCE is None:
            from cortex_viz.infrastructure.workflow_graph_source_ast import (
                WorkflowGraphASTSource,
            )

            _AST_SOURCE = WorkflowGraphASTSource()
        return _AST_SOURCE


_AST_CONTEXT_CAP = 20  # source: architect plan — bound get_context fan-out


def _ast_and_impact(path: str) -> dict:
    """AST symbols defined in the file + per-symbol 360° context and the
    blast radius of the first symbol, via the warm AP source. Uses AP's
    typed ``get_context`` (per symbol, capped at N=20) and ``get_impact``
    (once, on the first symbol) instead of hand-written Cypher. Degrades
    gracefully to ``{available: False, reason}`` when AP is off /
    unreachable.

    Returned shape is preserved: ``{available, symbols, impact}``. ``impact``
    is enriched — it carries ``communities_affected`` / ``processes_affected``
    from ``get_impact`` plus the raw affected lists.
    """
    try:
        from cortex_viz.infrastructure import ap_bridge
        from cortex_viz.infrastructure.ap_bridge import resolve_graph_paths

        if not ap_bridge.is_enabled():
            return {"available": False, "reason": "ap_disabled"}

        src = _get_ast_source()
        # load_symbols([path]) returns rows shaped
        # {file_path, qualified_name, symbol_type, signature, language,
        #  line, domain} — matched by path tail, so abs or repo-relative
        # both work.
        symbols = src.load_symbols([path]) or []
        if not symbols:
            return {"available": True, "symbols": [], "impact": []}

        graph_paths = resolve_graph_paths()
        gp = graph_paths[0] if graph_paths else None
        if not gp:
            return {"available": True, "symbols": symbols, "impact": []}

        loop_run = src._loop_owner.run  # noqa: SLF001
        bridge = src._bridge  # noqa: SLF001

        _attach_symbol_context(loop_run, bridge, gp, symbols)
        impact = _first_symbol_impact(loop_run, bridge, gp, symbols)
        return {"available": True, "symbols": symbols, "impact": impact}
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "error": str(exc)}


def _attach_symbol_context(loop_run, bridge, gp: str, symbols: list[dict]) -> None:
    """Enrich the first up-to-N symbols in place with AP's typed 360° context.

    Each call is serialized onto the warm pinned loop — no fresh APBridge,
    no asyncio.run. A symbol whose context cannot be fetched keeps its
    unenriched shape rather than failing the whole panel.
    """
    for sym in symbols[:_AST_CONTEXT_CAP]:
        qn = sym.get("qualified_name")
        if not qn:
            continue
        try:
            ctx = loop_run(bridge.get_context(gp, qn))
        except Exception:
            continue
        if isinstance(ctx, dict):
            sym["context"] = {
                "relationships": ctx.get("relationships") or {},
                "community": ctx.get("community") or {},
                "processes": ctx.get("processes") or [],
            }


def _first_symbol_impact(loop_run, bridge, gp: str, symbols: list[dict]) -> dict:
    """Blast radius of the file's first symbol, via the typed get_impact tool.

    Empty dict on any failure: the symbol list is the primary payload and
    must still render without the impact rollup.
    """
    qn0 = symbols[0].get("qualified_name")
    if not qn0:
        return {}
    try:
        impact_raw = loop_run(bridge.get_impact(gp, qn0))
    except Exception:
        return {}
    if not isinstance(impact_raw, dict):
        return {}
    return {
        "qualified_name": impact_raw.get("qualified_name"),
        "communities": impact_raw.get("communities") or [],
        "communities_affected": impact_raw.get("communities_affected"),
        "processes": impact_raw.get("processes") or [],
        "processes_affected": impact_raw.get("processes_affected"),
    }


def _impact_for_graph(graph_path: str, rel_path: str) -> dict | None:
    """Run the exact-path impact queries against ONE code-graph.

    Returns the impact dict (``downstream, upstream, members, processes,
    references, referenced_by, depends_on, depended_on_by`` plus the new
    ``implements``, ``community``, ``communities_affected``,
    ``processes_affected``) or None if the file has no symbols in this
    graph (so the caller can try the next graph).

    Symbol-level direction (calls/imports/callers/implements) is derived
    from AP's typed ``get_context`` tool — one call per member symbol,
    capped at N=20. Files with more than N members fall back to the
    targeted per-file Cypher path (bounds AP fan-out). File→File edges,
    the present-gate, the members list, and the rollup stay on Cypher —
    AP exposes no file-level typed tool for those.
    """
    from cortex_viz.infrastructure.workflow_graph_source_ast import _as_list

    # Reuse the warm AST source's pinned loop + persistent AP connection.
    # A fresh APBridge + asyncio.run() per HTTP request collides with the
    # warm bridge on the same AP subprocess (relationship MATCH queries
    # silently returned 0 over HTTP while single-node MATCH worked). The
    # source serializes every call onto one loop, which is reliable.
    src = _get_ast_source()
    loop_run = src._loop_owner.run  # noqa: SLF001
    bridge = src._bridge  # noqa: SLF001

    async def _run() -> dict | None:
        async def q(cypher):
            rows = await bridge.query_graph(graph_path, cypher)
            return _as_list(rows)

        esc = rel_path.replace("'", "")
        # LEGITIMATE query_graph use: present-gate. AP has no typed
        # "is this file in the graph" tool. (cheap gate)
        present = await q(
            f"MATCH (f:File) WHERE f.id = '{esc}' RETURN f.id AS id LIMIT 1"
        )
        if not present:
            return None

        # LEGITIMATE query_graph use: members list. This is the N source
        # that drives the typed get_context fan-out below; AP has no
        # file-scoped "list members" typed tool.
        members_rows = await q(
            f"MATCH (s:Function) WHERE s.qualified_name STARTS WITH '{esc}::' "
            "RETURN DISTINCT s.qualified_name AS name LIMIT 200"
        )
        member_qns = [r.get("name") for r in members_rows if r.get("name")]

        implements: list[dict] = []
        community: dict = {}
        if member_qns and len(member_qns) <= _AST_CONTEXT_CAP:
            # Typed path: AP's get_context per member (capped at N).
            downstream, upstream, implements, community = await _typed_directions(
                bridge, graph_path, member_qns
            )
        else:
            # FALLBACK path: file has >N members (or none).
            downstream, upstream = await _fallback_directions(q, esc)

        # LEGITIMATE query_graph use: File→File edges. AP all-file
        # indexing (>= 0.2.0): Imports_File_File = .js import/require;
        # References_File_File = Markdown/doc links. No typed tool covers
        # these non-AST direct file edges.
        file_imports = await q(
            "MATCH (f:File)-[r:Imports_File_File]->(d:File) "
            f"WHERE f.id = '{esc}' "
            "RETURN DISTINCT d.id AS name, r.confidence AS conf LIMIT 200"
        )
        file_imported_by = await q(
            "MATCH (s:File)-[r:Imports_File_File]->(f:File) "
            f"WHERE f.id = '{esc}' "
            "RETURN DISTINCT s.id AS name, r.confidence AS conf LIMIT 200"
        )
        doc_refs = await q(
            "MATCH (f:File)-[r:References_File_File]->(d:File) "
            f"WHERE f.id = '{esc}' "
            "RETURN DISTINCT d.id AS name, r.confidence AS conf LIMIT 200"
        )
        doc_referenced_by = await q(
            "MATCH (s:File)-[r:References_File_File]->(f:File) "
            f"WHERE f.id = '{esc}' "
            "RETURN DISTINCT s.id AS name, r.confidence AS conf LIMIT 200"
        )

        members = [
            {
                "file": rel_path,
                "name": qn,
                "label": _short_name(qn),
                "kind": "member",
                "confidence": None,
            }
            for qn in member_qns
        ]

        # Blast-radius counts via AP's typed get_impact (first member).
        # processes/communities affected are the headline numbers the
        # panel shows; the entry-point process list below stays on Cypher.
        communities_affected, processes_affected = await _blast_radius(
            bridge, graph_path, member_qns
        )

        # LEGITIMATE query_graph use: entry-point processes (causal
        # chains ENTERED from this file). AP's get_processes is graph-wide
        # (not file-scoped); this targeted Cypher filters to this file's
        # entry points. entry_point_id is ``file::symbol``.
        processes_rows = await q(
            f"MATCH (p:Process) WHERE p.entry_point_id STARTS WITH '{esc}::' "
            "RETURN DISTINCT p.entry_point_id AS entry, p.entry_kind AS kind, "
            "p.depth AS depth, p.symbol_count AS n "
            "ORDER BY p.symbol_count DESC LIMIT 40"
        )
        processes = _processes_from(processes_rows)

        # Direct File→File edges (AP all-file indexing): code imports for
        # non-AST files (.js) and doc references (Markdown). Folded into the
        # file-level direction so the panel shows them even when a file has
        # no AST symbols at all.
        imports_files = _file_edges(file_imports, "imports", rel_path)
        imported_by_files = _file_edges(file_imported_by, "imports", rel_path)
        references = _file_edges(doc_refs, "references", rel_path)
        referenced_by = _file_edges(doc_referenced_by, "references", rel_path)

        return {
            "downstream": downstream,
            "upstream": upstream,
            "members": members,
            "processes": processes,
            "references": references,
            "referenced_by": referenced_by,
            "depends_on": _rollup(downstream + imports_files, rel_path),
            "depended_on_by": _rollup(upstream + imported_by_files, rel_path),
            # New AP-typed enrichment fields (additive — frontend may
            # ignore them without breaking the existing direction view).
            "implements": implements,
            "community": community,
            "communities_affected": communities_affected,
            "processes_affected": processes_affected,
        }

    return loop_run(_run())


def _to_repo_relative(path: str) -> str:
    """Normalize a file path to the project-root-relative form AP indexes.

    AP's ``File.id`` is repo-relative (``mcp_server/server/http_standalone.py``).
    Graph file nodes carry the absolute tool-call path, so callers pass an
    absolute path; make it relative to its git root so the exact ``f.id =``
    match in ``_impact_for_graph`` lands. A relative path is just cleaned;
    if no git root resolves, fall back to stripping the leading slash.
    """
    import os

    p = os.path.expanduser(path or "").replace("\\", "/")
    if not p.startswith("/"):
        # Relative input — reject ``..`` traversal, return cleaned.
        if ".." in p.split("/"):
            return ""
        return p.lstrip("./")
    try:
        real = os.path.realpath(p)
    except (OSError, ValueError):
        return p.lstrip("/")
    if not _is_contained(real):
        return p.lstrip("/")
    return _relative_to_git_root(real) or p.lstrip("/")


def _is_contained(real: str) -> bool:
    """CWE-22 containment: is the resolved path under an allowed root?

    Self-contained (the original git_diff / http_file_diff helpers were
    never ported in the extraction — their absence broke
    /api/trace/impact AND the live P3 impact pass). A crafted absolute
    path outside HOME / cwd / temp is never made relative and never
    reaches a filesystem op against an arbitrary location.
    """
    import os
    import tempfile
    from pathlib import Path

    roots: list[str] = []
    for base in (Path.home(), Path.cwd(), Path(tempfile.gettempdir())):
        try:
            roots.append(os.path.realpath(base))
        except (OSError, ValueError):
            # A base that will not resolve is simply not a containment candidate.
            pass
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def _relative_to_git_root(real: str) -> str:
    """``real`` relative to its git root, or "" when that cannot be determined.

    Empty covers all three misses — no git, rev-parse timed out, or the
    file sits outside the root it reported — and the caller applies the
    same leading-slash fallback to each.
    """
    import subprocess
    from pathlib import Path

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(Path(real).parent),
            timeout=5,
            shell=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    root = res.stdout.strip() if res.returncode == 0 else ""
    if not root:
        return ""
    try:
        return str(Path(real).relative_to(root))
    except ValueError:
        return ""


# Edge-bearing keys whose totals rank a per-graph impact result; the richest
# (most edges) wins when several code-graphs contain the same file.
_IMPACT_EDGE_KEYS = (
    "downstream",
    "upstream",
    "members",
    "processes",
    "references",
    "referenced_by",
    "depends_on",
    "depended_on_by",
)


def impact_for_path(path: str) -> dict | None:
    """Richest AP blast-radius dict for ``path`` across every code-graph.

    The shared impact lookup behind BOTH ``/api/trace/impact`` (the HTTP L4
    view) and the live session-activity impact pass (P3): when an edit/write
    is captured, the ingest path calls this to draw the blast radius live.
    Returns the impact dict (``downstream/upstream/members/...``) for the
    graph with the most edges, or None when AP is off or the file is in no
    graph. Never raises.
    """
    from cortex_viz.infrastructure import ap_bridge

    if not ap_bridge.is_enabled():
        return None
    rel = _to_repo_relative(path)
    if not rel:
        return None
    result = None
    best_edges = -1
    for gp in ap_bridge.resolve_graph_paths():
        try:
            r = _impact_for_graph(gp, rel)
        except Exception:
            r = None
        if r is None:
            continue
        n = sum(len(r.get(k, [])) for k in _IMPACT_EDGE_KEYS)
        if n > best_edges:
            best_edges = n
            result = r
    return result
