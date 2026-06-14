"""Pure mapping: an AP blast-radius dict → live directional graph fragment.

P3 — live impact mapping. When a file edit/write is captured (P0 activity
spine), the server asks AP for that file's blast radius (``impact_for_path``)
and this turns the result into nodes/edges that hang off the SAME
``file:<path>`` node the edit action already points to, so the graph shows —
live, the moment you save — what your change affects:

    caller-symbol ──impacts──▶ file(edited) ──uses──▶ dependency-symbol
    reverse-dep-file ──impacts──▶ file(edited)

Directional by construction: ``impacts`` edges point INTO the edited file
(these break if it changes — the blast radius), ``uses`` edges point OUT (what
the file depends on). Pure: zero I/O, stdlib only. Bounded by ``max_items`` so
a hot file can't flood the stream.
"""

from __future__ import annotations

from typing import Any


def _short(qn: str) -> str:
    return str(qn or "").split("::")[-1] or str(qn)


def impact_to_graph(
    file_path: str, impact: dict[str, Any], *, max_items: int = 40
) -> dict[str, list]:
    """Blast-radius ``{nodes, edges}`` for an edited file.

    ``file_path`` is the absolute path the edit action targets (so the center
    node id ``file:<file_path>`` matches the activity spine's target node and
    the blast radius attaches to it). ``impact`` is the dict from
    ``server.trace_impact.impact_for_path`` (``upstream`` = callers,
    ``downstream`` = dependencies, ``depended_on_by`` = file-level reverse
    deps). Symbol/file ids here are NOT yet unified with the AST layer's ids
    (that is P4 node-unification) — for now they render as the live blast
    radius hanging off the edited file.
    """
    fid = f"file:{file_path}"
    nodes: list[dict] = [
        {"id": fid, "kind": "file", "type": "file",
         "label": file_path.rsplit("/", 1)[-1] or file_path},
    ]
    edges: list[dict] = []
    seen: set[str] = {fid}

    def _symbol(item: dict, edge_kind: str, into_file: bool) -> None:
        qn = item.get("name") or item.get("qualified_name")
        if not qn:
            return
        nid = f"symbol:{qn}"
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "kind": "symbol", "type": "symbol",
                          "label": item.get("label") or _short(qn)})
        if into_file:  # caller ──impacts──▶ edited file
            edges.append({"id": f"{nid}->{fid}", "source": nid, "target": fid,
                          "kind": edge_kind, "type": edge_kind})
        else:  # edited file ──uses──▶ dependency
            edges.append({"id": f"{fid}->{nid}", "source": fid, "target": nid,
                          "kind": edge_kind, "type": edge_kind})

    # upstream = callers/importers — these break if the file changes.
    for it in (impact.get("upstream") or [])[:max_items]:
        _symbol(it, "impacts", into_file=True)
    # downstream = what this file calls/imports — its dependencies.
    for it in (impact.get("downstream") or [])[:max_items]:
        _symbol(it, "uses", into_file=False)

    # File-level reverse deps (depended_on_by) — other files that break.
    for it in (impact.get("depended_on_by") or [])[:max_items]:
        f = it.get("file")
        if not f:
            continue
        nid = f"file:{f}"
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "kind": "file", "type": "file",
                          "label": f.rsplit("/", 1)[-1] or f})
        edges.append({"id": f"{nid}->{fid}", "source": nid, "target": fid,
                      "kind": "impacts", "type": "impacts"})

    return {"nodes": nodes, "edges": edges}
