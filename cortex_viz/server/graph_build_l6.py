"""L6 AST sweep — per-project symbol/edge construction + disk cache.

Extracted verbatim from ``http_standalone_graph.py``'s ``_run`` body (the
section from "L6 — AST per project" through "L6_CROSS"). The cumulative-merge,
progress, and phase callbacks are passed in from ``graph_build`` so the closure
semantics are identical to the in-line original.

Shared cache state (``_node_index``) lives in ``graph_cache_state``; the
deterministic ray placement is ``graph_wire._place_around``.
"""

from __future__ import annotations

import math
import sys

from cortex_viz.shared.hash import simple_hash

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_wire import _place_around

# Per-project wall-clock ceiling for the L6 AST load (tree-sitter parse +
# AP bridge round-trip). A project that exceeds this is marked ready and
# skipped so the build always reaches "done" rather than hanging forever.
# source: measured — a healthy multi-thousand-file repo resolves in <60s via
# the AP bridge; 180s = 3× that headroom, generous for a large codebase while
# still bounding the build. Replaces the prior no-timeout path that could hang
# the child indefinitely on a wedged AP subprocess.
_L6_PROJECT_TIMEOUT_S = 180.0


def run_l6(
    store,
    baseline: dict,
    file_id_by_path: dict[str, str],
    *,
    merge,
    set_progress,
    register_phase,
    mark_phase_ready,
    phase_deps_satisfied,
    persist_full_layout,
) -> bool:
    """Run the L6 AST sweep. Returns True if the build reached L6_CROSS
    completion (caller finalises meta + full_ready), False on an early
    return (AP disabled — caller's _run also early-returned in the
    original; here the AP-disabled branch is handled BEFORE calling run_l6).

    All callbacks (merge/set_progress/register_phase/mark_phase_ready/
    phase_deps_satisfied/persist_full_layout) are the closure functions from
    ``graph_build._run`` so behaviour is identical to the monolithic body.
    """
    from cortex_viz.core.workflow_graph_palette import (
        SYMBOL_COLOR_DEFAULT,
        SYMBOL_COLORS,
    )
    from cortex_viz.core.workflow_graph_schema import (
        NodeIdFactory,
        edge_provenance_defaults,
    )
    from cortex_viz.infrastructure.ap_bridge import (
        resolve_graph_paths,
    )
    from cortex_viz.infrastructure.workflow_graph_source_ast import (
        WorkflowGraphASTSource,
    )

    ast_source = WorkflowGraphASTSource()
    graph_paths = resolve_graph_paths()
    total = max(len(graph_paths), 1)
    import hashlib
    import json as _json
    from pathlib import Path as _Path

    _BATCH = 200

    # ── Per-project AST cache ──
    # AP parses tree-sitter once per project and writes the
    # result into LadybugDB at ``~/.cortex/ap_graphs/<proj>/graph``.
    # Cortex then queries AP to pull the symbols + edges back out
    # for visualization. When nothing has changed in the underlying
    # graph files, the second-query result is identical — so we
    # cache it to disk and short-circuit the AP round-trip entirely.
    #
    # Key = SHA-256 of the graph directory's (path, size, mtime)
    # triples for every file inside. The instant any AP file
    # changes (re-index happened) the key differs and we refetch.
    _CACHE_DIR = _Path.home() / ".claude" / "methodology" / "ast_cache"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _graph_signature(gp_: str) -> str:
        root = _Path(gp_)
        if not root.exists():
            return ""
        h = hashlib.sha256()
        # Walk deterministically so the signature is stable.
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            rel = str(f.relative_to(root))
            h.update(rel.encode())
            h.update(str(st.st_size).encode())
            h.update(str(int(st.st_mtime)).encode())
        return h.hexdigest()[:16]

    def _cache_path(proj_name_: str) -> _Path:
        return _CACHE_DIR / f"{proj_name_}.json"

    def _cache_load(proj_name_: str, sig_: str):
        p = _cache_path(proj_name_)
        if not p.is_file() or not sig_:
            return None
        try:
            data = _json.loads(p.read_text())
        except Exception:
            return None
        if data.get("signature") != sig_:
            return None
        return data.get("symbols") or [], data.get("edges") or []

    def _cache_store(proj_name_: str, sig_: str, syms_: list, edgs_: list) -> None:
        if not sig_:
            return
        try:
            _cache_path(proj_name_).write_text(
                _json.dumps(
                    {
                        "signature": sig_,
                        "symbols": syms_,
                        "edges": edgs_,
                    }
                )
            )
        except Exception:
            pass

    async def _load_with_timeout(gp_):
        # Finite per-project ceiling so one wedged AP subprocess (or a
        # pathological repo) cannot hang the whole build. On timeout
        # asyncio raises TimeoutError, which the caller's except below
        # turns into "mark this project's phase ready + continue" — the
        # build always reaches "done" and the other projects still load.
        import asyncio as _asyncio

        async def _load():
            syms = await ast_source._load_symbols_async(gp_, [])
            edgs = await ast_source._load_edges_async(gp_, [])
            return syms, edgs

        return await _asyncio.wait_for(_load(), timeout=_L6_PROJECT_TIMEOUT_S)

    # L6 runs ONE PHASE PER PROJECT so the graph grows
    # project-by-project: finish indexing project A → publish
    # its symbol nodes + intra-project edges as phase
    # ``L6:A`` → client appends → next project. Cross-project
    # edges (rare: an ``imports`` pointing at a symbol that
    # lives in a different project's AST) are batched into
    # ``L6_CROSS`` at the very end when every project phase
    # is ready.
    _proj_names: list[str] = []
    for gp in graph_paths:
        pn = str(gp).rsplit("/", 3)[-2] if "/" in str(gp) else str(gp)
        _proj_names.append(pn)
        register_phase(
            f"L6:{pn}",
            deps=["L3"],
            label=f"L6 {pn} symbols",
        )
    register_phase(
        "L6_CROSS",
        deps=[f"L6:{pn}" for pn in _proj_names],
        label="L6 cross-project edges",
    )

    # Track which symbols exist per-project so we can route
    # each edge into the right phase. An edge is "intra" iff
    # both endpoints are symbols indexed in THIS project.
    proj_symbol_ids: dict[str, set] = {pn: set() for pn in _proj_names}
    cross_edges: list[dict] = []

    for i, gp in enumerate(graph_paths):
        proj_name = _proj_names[i]
        phase_key = f"L6:{proj_name}"
        if not phase_deps_satisfied(phase_key):
            continue  # waiting for L3 — shouldn't happen here

        # Tight coupling with AP: if the underlying LadybugDB
        # graph hasn't changed (signature match), we already
        # know the answer — load from disk, skip the AP call.
        sig = _graph_signature(gp)
        cached = _cache_load(proj_name, sig)
        if cached is not None:
            syms, edgs = cached
            set_progress(
                phase=f"L6 {i + 1}/{total} {proj_name}",
                pct=0.30 + 0.65 * ((i + 1) / total),
                message=f"{proj_name}: cached ({len(syms)} symbols)",
            )
        else:
            try:
                syms, edgs = ast_source._loop_owner.run(_load_with_timeout(gp))
            except Exception as exc:
                print(
                    f"[cortex] L6 project {proj_name} skipped: "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                set_progress(
                    phase=f"L6 {i + 1}/{total} {proj_name}",
                    pct=0.30 + 0.65 * ((i + 1) / total),
                    message=f"{proj_name}: error — {type(exc).__name__}",
                )
                mark_phase_ready(phase_key)
                continue
            # Persist for the next run.
            _cache_store(proj_name, sig, list(syms), list(edgs))

        # Each symbol belongs to ITS PROJECT's domain — not the
        # global hub. The L0 phase emits domain ids as
        # ``domain:<kebab-case-label>`` (see
        # ``shared.project_ids.domain_id_from_label``); we match
        # that slugging here so symbol→domain routing lines up
        # with the existing domain nodes in the cache.
        from cortex_viz.shared.project_ids import (
            domain_id_from_label,
        )

        proj_slug = domain_id_from_label(proj_name) or proj_name
        proj_domain_id = f"domain:{proj_slug}"

        proj_nodes: list[dict] = []
        proj_edges: list[dict] = []

        # Every AST-indexed file is also a REAL file that can
        # be read/edited by Claude tools — same entity as an
        # L3 file. If L3 didn't see this file (never touched
        # during a tool call), emit it as a project-scoped
        # file node here so the symbol has a parent to attach
        # to and the file appears in the domain's file ring.
        ap_file_paths: set[str] = set()
        for sym in syms:
            fp_ = sym.get("file_path") or ""
            if fp_:
                ap_file_paths.add(fp_)
        # Anchor for this project's coordinate placement: the
        # domain hub's baked coordinate (the DrL pass covered
        # the baseline, which includes every SESSION domain).
        _hub = state._node_index.get(proj_domain_id) or {}
        _hub_xy = (
            (_hub.get("x"), _hub.get("y"))
            if _hub.get("x") is not None and _hub.get("y") is not None
            else None
        )
        if _hub_xy is None:
            # AP-only project (indexed code with no session
            # history): no baseline domain node exists, so the
            # placement chain (hub -> files -> symbols) dead-
            # ended and 90,225 symbols shipped with NO
            # coordinates (measured on the wire 2026-06-13) —
            # the client fell back to simulation mode. Place
            # the project hub deterministically on the outer
            # ring: the DrL bake normalises to <=~0.91
            # (layout_engine 0.55-span padding), so radius 0.9
            # sits at the layout's edge; DJB2(domain id) sets
            # the angle so projects spread.
            _h = int(simple_hash(proj_domain_id), 16)
            _ang = (_h % 3600) / 3600.0 * 2.0 * math.pi
            _hub_xy = (
                round(0.9 * math.cos(_ang), 4),
                round(0.9 * math.sin(_ang), 4),
            )
            if not _hub:
                proj_nodes.append(
                    {
                        "id": proj_domain_id,
                        "kind": "domain",
                        "type": "domain",
                        "label": proj_slug or proj_name,
                        "domain_id": proj_domain_id,
                        "domain": proj_slug,
                        "x": _hub_xy[0],
                        "y": _hub_xy[1],
                    }
                )
            else:
                # Exists but never placed — set coordinates on
                # the cached record so the chain below resolves.
                _hub["x"], _hub["y"] = _hub_xy

        for fp_ in ap_file_paths:
            if file_id_by_path.get(fp_):
                continue
            fid = NodeIdFactory.file_id(fp_)
            file_id_by_path[fp_] = fid
            # Also register every path-tail variant so the
            # later symbol → file lookup still works when AP
            # and L3 disagree on absolute vs relative paths.
            parts = fp_.split("/")
            for i in range(1, len(parts)):
                file_id_by_path.setdefault("/".join(parts[i:]), fid)
            _fnode = {
                "id": fid,
                "kind": "file",
                "type": "file",
                "label": fp_.rsplit("/", 1)[-1],
                "path": fp_,
                "domain_id": proj_domain_id,
                "domain": proj_slug,
            }
            if _hub_xy is not None:
                _fnode["x"], _fnode["y"] = _place_around(_hub_xy[0], _hub_xy[1], fid)
            proj_nodes.append(_fnode)
            # Bind the file to its domain so L3-layout places
            # it in the domain's file ring.
            proj_edges.append(
                {
                    "source": fid,
                    "target": proj_domain_id,
                    "kind": "in_domain",
                    "type": "in_domain",
                    "weight": 1.0,
                }
            )

        # Coordinates of the files created in THIS loop — they
        # are not in _node_index until the merge, but their
        # symbols are placed right below.
        _local_file_xy = {
            n["id"]: (n["x"], n["y"])
            for n in proj_nodes
            if n.get("kind") == "file" and n.get("x") is not None
        }

        def _file_xy(fid_: str | None) -> tuple[float, float] | None:
            if not fid_:
                return None
            cached = state._node_index.get(fid_)
            if (
                cached
                and cached.get("x") is not None
                and cached.get("y") is not None
            ):
                return (cached["x"], cached["y"])
            return _local_file_xy.get(fid_)

        for sym in syms:
            qn = sym.get("qualified_name") or ""
            fp = sym.get("file_path") or ""
            if not qn:
                continue
            sid = NodeIdFactory.symbol_id(fp, qn)
            proj_symbol_ids[proj_name].add(sid)
            stype = str(sym.get("symbol_type") or "function")
            _snode = {
                "id": sid,
                "kind": "symbol",
                "type": "symbol",
                "label": qn.rsplit("::", 1)[-1] or qn,
                "color": SYMBOL_COLORS.get(stype, SYMBOL_COLOR_DEFAULT),
                "path": fp,
                "symbol_type": stype,
                "domain_id": proj_domain_id,
                "domain": proj_slug,
            }
            # Server-side position: ray around the parent file's
            # coordinate (baked L3 file or just-placed L6 file),
            # falling back to the domain hub. Every node on the
            # wire carries a position — the client draws, it
            # does not simulate.
            _axy = _file_xy(file_id_by_path.get(fp)) or _hub_xy
            if _axy is not None:
                _snode["x"], _snode["y"] = _place_around(_axy[0], _axy[1], sid)
            proj_nodes.append(_snode)
            parent = file_id_by_path.get(fp)
            if parent:
                # Gap 6: shared provenance defaults.
                di_conf, di_reason = edge_provenance_defaults("defined_in")
                proj_edges.append(
                    {
                        "source": sid,
                        "target": parent,
                        "kind": "defined_in",
                        "type": "defined_in",
                        "weight": 1.0,
                        "confidence": di_conf,
                        "reason": di_reason,
                    }
                )
        for e in edgs:
            sf = e.get("src_file") or ""
            sn = e.get("src_name") or ""
            df = e.get("dst_file") or ""
            dn = e.get("dst_name") or ""
            if not df or not dn:
                continue
            did = NodeIdFactory.symbol_id(df, dn)
            kind = e.get("kind") or "calls"
            if kind == "imports":
                sid = file_id_by_path.get(sf)
                if not sid:
                    continue
            else:
                if not sf or not sn:
                    continue
                sid = NodeIdFactory.symbol_id(sf, sn)
            # Gap 6: single source-of-truth defaults.
            conf, reason_v = edge_provenance_defaults(
                kind,
                ap_confidence=e.get("confidence"),
                ap_reason=e.get("reason"),
            )
            edge = {
                "source": sid,
                "target": did,
                "kind": kind,
                "type": kind,
                "weight": 1.0,
                "confidence": conf,
                "reason": reason_v,
            }
            # Intra-project iff both endpoints (where they are
            # symbols) belong to THIS project. For `imports`
            # the source is a file id, always "intra" once we
            # see it here.
            src_ok = kind == "imports" or sid in proj_symbol_ids[proj_name]
            tgt_ok = did in proj_symbol_ids[proj_name]
            if src_ok and tgt_ok:
                proj_edges.append(edge)
            else:
                cross_edges.append(edge)

        # Stream this project's nodes in batches (smooth fade-in),
        # then its intra-project edges at the end.
        # No pacing between batches. The wait_for_clear(1.0)
        # consult that used to sit here throttled against the
        # LayoutAuthority's overload flag — but the authority
        # has NO consumer (/api/graph/stream is not routed), so
        # once tripped the flag never cleared and EVERY batch
        # burned the full 1 s timeout: ~3,350 batches ≈ an hour
        # of pure sleep, indistinguishable from a deadlock
        # (observed 2026-06-12). SSE chunking (emit chunk=1000)
        # is the pacing now.
        for bstart in range(0, len(proj_nodes), _BATCH):
            chunk_nodes = proj_nodes[bstart : bstart + _BATCH]
            merge(
                chunk_nodes,
                [],
                stage=f"L6 {i + 1}/{total} {proj_name}",
                pct=0.30 + 0.65 * ((i + 1) / total),
                message=(f"{proj_name}: +{len(chunk_nodes)} symbols"),
                phase_key=phase_key,
            )
        # Intra-project edges land in the same project phase,
        # but only AFTER all its nodes — the client's dangling-
        # edge filter handles any slack.
        if proj_edges:
            merge(
                [],
                proj_edges,
                stage=f"L6 {i + 1}/{total} {proj_name}",
                pct=0.30 + 0.65 * ((i + 1) / total),
                message=(f"{proj_name}: +{len(proj_edges)} AST edges"),
                phase_key=phase_key,
            )
        mark_phase_ready(phase_key)

    # Cross-project edges — deps on every L6:<proj> phase.
    if not phase_deps_satisfied("L6_CROSS"):
        return False
    for bstart in range(0, len(cross_edges), 2000):
        chunk = cross_edges[bstart : bstart + 2000]
        merge(
            [],
            chunk,
            stage="L6 cross-edges",
            pct=min(0.99, 0.95 + 0.04 * (bstart / max(len(cross_edges), 1))),
            message=(
                f"cross-project edges: +{len(chunk)} "
                f"({bstart + len(chunk)}/{len(cross_edges)})"
            ),
            phase_key="L6_CROSS",
        )
    mark_phase_ready("L6_CROSS")
    return True
