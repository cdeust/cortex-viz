"""Direction, rollup and blast-radius helpers for ``/api/trace/impact``.

Split out of ``trace_impact.py`` (§4 500-line limit). Pure shaping of AP
bridge results plus the two async collectors that fan out to AP; the
orchestration (which graph, which path, in what order) stays in
``trace_impact``, which re-exports ``_basename`` for
``http_standalone_trace``.
"""

from __future__ import annotations


def _basename(p: str) -> str:
    return (p or "").replace("\\", "/").rstrip("/").split("/")[-1]


def _file_of(qn) -> str:
    """The file half of an AP qualified name (``file::symbol``)."""
    return str(qn or "").partition("::")[0]


def _short_name(qn) -> str:
    """The symbol half of an AP qualified name."""
    return str(qn or "").split("::")[-1]


def _conf(r) -> float | None:
    """AP's edge confidence as a float, or None when absent/unparseable."""
    try:
        return float(r.get("conf")) if r.get("conf") is not None else None
    except (TypeError, ValueError):
        return None


def _rel_item(rel: dict, kind: str) -> dict | None:
    """One direction entry from a ``get_context`` relationship row."""
    qn = rel.get("qualified_name") or rel.get("name")
    if not qn:
        return None
    return {
        "file": _file_of(qn),
        "name": qn,
        "label": _short_name(qn),
        "kind": kind,
        # get_context items carry no confidence of their own.
        "confidence": None,
    }


# (relationship key in get_context, direction kind reported to the UI).
# The two implements keys merge into one bucket: the panel shows the
# interface relationship, not which side declared it.
_DOWNSTREAM_RELS = (("calls", "calls"), ("imports", "imports"), ("uses", "uses"))
_UPSTREAM_RELS = (
    ("called_by", "calls"),
    ("imported_by", "imports"),
    ("used_by", "uses"),
)
_IMPLEMENTS_RELS = (("implements", "implements"), ("implemented_by", "implements"))


def _collect_rels(
    rels: dict,
    spec: tuple[tuple[str, str], ...],
    bucket: list[dict],
    seen: set[str],
) -> None:
    """Append the named relationships into ``bucket``, deduplicated by name."""
    for key, kind in spec:
        for rel in rels.get(key) or []:
            it = _rel_item(rel, kind)
            if it and it["name"] not in seen:
                seen.add(it["name"])
                bucket.append(it)


async def _typed_directions(
    bridge,
    graph_path: str,
    member_qns: list,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Direction lists from AP's typed ``get_context``, one call per member.

    Returns (downstream, upstream, implements, community). A member whose
    context cannot be fetched is skipped: the enrichment is additive and a
    partial fan-out still renders.
    """
    downstream: list[dict] = []
    upstream: list[dict] = []
    implements: list[dict] = []
    community: dict = {}
    seen_down: set[str] = set()
    seen_up: set[str] = set()
    seen_impl: set[str] = set()

    for qn in member_qns:
        try:
            ctx = await bridge.get_context(graph_path, qn)
        except Exception:
            ctx = None
        if not isinstance(ctx, dict):
            continue
        rels = ctx.get("relationships") or {}
        _collect_rels(rels, _DOWNSTREAM_RELS, downstream, seen_down)
        _collect_rels(rels, _UPSTREAM_RELS, upstream, seen_up)
        _collect_rels(rels, _IMPLEMENTS_RELS, implements, seen_impl)
        if not community and ctx.get("community"):
            community = ctx.get("community") or {}

    return downstream, upstream, implements, community


async def _fallback_directions(q, esc: str) -> tuple[list[dict], list[dict]]:
    """Direction lists for a file with more members than the get_context cap.

    LEGITIMATE query_graph use: firing 200 typed calls to answer one panel
    is worse than three targeted Cypher queries.
    """
    calls = await q(
        "MATCH (s:Function)-[r:Calls_Function_Function]->(d:Function) "
        f"WHERE s.qualified_name STARTS WITH '{esc}::' "
        "RETURN DISTINCT d.qualified_name AS name, r.confidence AS conf "
        "LIMIT 200"
    )
    imports = await q(
        "MATCH (f:File)-[r:Imports_File_Function]->(d:Function) "
        f"WHERE f.id = '{esc}' "
        "RETURN DISTINCT d.qualified_name AS name, r.confidence AS conf "
        "LIMIT 200"
    )
    callers = await q(
        "MATCH (s:Function)-[r:Calls_Function_Function]->(d:Function) "
        f"WHERE d.qualified_name STARTS WITH '{esc}::' "
        "RETURN DISTINCT s.qualified_name AS name, r.confidence AS conf "
        "LIMIT 200"
    )
    downstream = [
        {
            "file": _file_of(r.get("name")),
            "name": r.get("name"),
            "label": _short_name(r.get("name")),
            "kind": kind,
            "confidence": _conf(r),
        }
        for rows, kind in ((calls, "calls"), (imports, "imports"))
        for r in rows
        if r.get("name")
    ]
    upstream = [
        {
            "file": _file_of(r.get("name")),
            "name": r.get("name"),
            "label": _short_name(r.get("name")),
            "kind": "calls",
            "confidence": _conf(r),
        }
        for r in callers
        if r.get("name")
    ]
    return downstream, upstream


async def _blast_radius(bridge, graph_path: str, member_qns: list):
    """AP's typed blast-radius counts, keyed off the file's first member.

    Additive enrichment: on any failure the direction view still renders,
    so both counts come back None rather than raising.
    """
    if not member_qns:
        return None, None
    try:
        imp = await bridge.get_impact(graph_path, member_qns[0])
    except Exception:
        return None, None
    if not isinstance(imp, dict):
        return None, None
    return imp.get("communities_affected"), imp.get("processes_affected")


def _processes_from(rows: list[dict]) -> list[dict]:
    """Entry-point processes, dropping rows with no entry point id."""
    return [
        {
            "entry": r.get("entry"),
            "label": _short_name(r.get("entry")),
            "kind": r.get("kind"),
            "depth": r.get("depth"),
            "symbol_count": r.get("n"),
        }
        for r in rows
        if r.get("entry")
    ]


def _rollup(items: list[dict], rel_path: str) -> list[dict]:
    """Collapse symbol-level edges to distinct FILES with edge counts.

    This is the "what does changing this break" view: the file itself is
    excluded, and the result is ordered by edge count so the heaviest
    dependency reads first.
    """
    agg: dict[str, dict] = {}
    for it in items:
        fp = it.get("file")
        if not fp or fp == rel_path:
            continue
        e = agg.setdefault(
            fp,
            {"file": fp, "label": _basename(fp), "edges": 0, "kinds": set()},
        )
        e["edges"] += 1
        if it.get("kind"):
            e["kinds"].add(it["kind"])
    out = [{**e, "kinds": sorted(e["kinds"])} for e in agg.values()]
    out.sort(key=lambda x: x["edges"], reverse=True)
    return out


def _member_items(rel_path: str, member_qns: list[str]) -> list[dict]:
    """Shape a file's member qualified names into the panel's member list."""
    return [
        {
            "file": rel_path,
            "name": qn,
            "label": _short_name(qn),
            "kind": "member",
            "confidence": None,
        }
        for qn in member_qns
    ]


def _file_edges(rows: list[dict], kind: str, rel_path: str) -> list[dict]:
    """Direct File→File edges, excluding the file's own self-reference."""
    return [
        {
            "file": r.get("name"),
            "label": _basename(r.get("name")),
            "kind": kind,
            "confidence": _conf(r),
        }
        for r in rows
        if r.get("name") and r.get("name") != rel_path
    ]
