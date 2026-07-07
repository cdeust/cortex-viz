"""The background build body — baseline assembly + DrL bake + L6 dispatch.

Extracted verbatim from ``http_standalone_graph.py``'s ``_kick_background_build._run``
(behaviour-preserving split, 2026-06-14). ``graph_build._kick_background_build``
acquires ``state._graph_build_lock`` then spawns a thread targeting
``run_build``; this module owns the merge closure and the per-layer build, and
releases the lock in its ``finally`` exactly as the original ``_run`` did.

Shared cache state lives in ``graph_cache_state`` (the single owner): the
build closure mutates it via ``state.X = ...`` direct attribute assignment —
behaviour-identical to the prior in-module ``global`` declarations, and the
SAME state the server-process appliers (``graph_appliers``) mutate.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_build_helpers import (
    _mark_phase_ready,
    _persist_full_layout,
    _phase_deps_satisfied,
    _register_phase,
    _roster_fingerprint,
    _set_progress,
)
from cortex_viz.server.graph_build_l6 import run_l6
from cortex_viz.server.graph_build_merge import make_merge
from cortex_viz.server.graph_wire import _place_around, _slim_node


def run_build(store, domain_filter: str | None) -> None:
    """The build thread body. Pre: caller holds ``state._graph_build_lock``.
    Post: the lock is released (finally) and the build has run to full_ready,
    AP-disabled early-return, L6_CROSS-deps early-return, or error.

    The cumulative-cache ``_merge`` closure (with its per-build dedup state)
    is constructed by ``make_merge`` right after the ``graph_event_stream``
    module is imported below — the FIRST ``_merge`` call (skeleton) happens
    after that import, so construction order is behaviour-identical to the
    in-line original.
    """
    try:
        from cortex_viz.handlers.workflow_graph import (
            build_workflow_graph,
        )

        state._graph_roster_fingerprint = _roster_fingerprint()
        _set_progress(
            phase="starting",
            pct=0.01,
            message="loading layer definitions…",
            started_at=time.monotonic(),
            baseline_ready=False,
            full_ready=False,
            node_count=0,
            edge_count=0,
        )

        # Seed the cache fresh so the per-layer _merge writes land
        # on an empty graph. The incremental hub-id map is rebuilt
        # by _merge as domain nodes arrive — clear the previous
        # run's entries so a removed domain can't leak in.
        state._graph_cache = {
            "data": {"nodes": [], "edges": [], "links": [], "meta": {}},
            "domain_filter": domain_filter,
        }
        state._cached_domain_hub_ids.clear()
        state._node_index.clear()
        state._adjacency.clear()
        # Reset per-phase state so a rebuild starts clean — phases
        # flip ready→pending and buffers empty. Otherwise stale L6
        # nodes from a prior run leak into the new publish and the
        # client's dedup masks missing content.
        # NOTE: per-project L6 phases (added dynamically later)
        # will be registered fresh by _register_phase, which also
        # resets their ready flags — so we only need to flip the
        # FIXED phases here.
        for _k in list(state.PHASES):
            state.PHASES[_k]["ready"] = False
        for _k in list(state._phase_payloads):
            state._phase_payloads[_k]["nodes"].clear()
            state._phase_payloads[_k]["edges"].clear()
        # Drop dynamic L6 phases from the previous run — they'll
        # be re-added below after graph_paths is resolved.
        for _k in list(state.PHASES):
            if _k.startswith("L6:") or _k == "L6_CROSS":
                state.PHASES.pop(_k, None)
                state._phase_payloads.pop(_k, None)
        with state._build_progress_lock:
            state._build_progress["phase_seq"] = 0
            state._build_progress["phases"] = {k: False for k in state.PHASES}
        # ── Per-layer streaming build ──
        # Each layer is published the instant its data is ready:
        #   L0  domains (the hubs)
        #   L1  Claude-Code setup: skills, hooks, commands, agents
        #   L2  tools (tool_hub nodes)
        #   L3  files (+ tool→file, command→file, discussion→file)
        #   L4  discussions
        #   L5  memories
        #   L6  AST symbols — streamed per project, per batch of 200
        # Ordering matches the user's requested reveal.
        # L0 + L1 + L2 + L3 + L4 + L5 all come from one
        # ``build_workflow_graph`` call; we can't easily partition
        # those. So we run baseline first (fast — a few seconds)
        # and merge the whole thing, then tag the phase.
        # AST/AP (the slow L6 ring) is deferred from the baseline via
        # ``defer_native_ast=True`` on the build call below — that flag
        # already gates Phase 4 (see ``build_workflow_graph``), so the
        # AST loaders never run during the baseline regardless of AP
        # enablement. We deliberately do NOT mutate
        # ``CORTEX_MEMORY_AP_ENABLED`` here: that global env write
        # disabled AP process-wide for the whole multi-minute build, so
        # every interactive ``/api/trace/impact`` and AST query returned
        # ``ap_disabled`` while a build was in flight (the impact diagram
        # was broken exactly when the user was exploring). Global mutable
        # state — refused per coding-standards §7.2.

        # ── Baseline producer (2026-05-27, revised) ──
        #
        # build_workflow_graph runs the source loads (PG queries) and
        # builds the structural graph. We surface per-source progress
        # via on_source_loaded so /api/graph/progress shows the work
        # in flight ("loaded 107043 memories") instead of a silent
        # spinner, and we defer the native tree-sitter AST parse
        # (defer_native_ast=True) — that parse was 58.6 s of a 99 s
        # build; AST symbols arrive via the L6 AP loop below instead.
        #
        # We do NOT use the per-source on_batch push here: routing
        # every node/edge of the huge memories batch (107k nodes +
        # 107k edges) through the LayoutAuthority synchronously in the
        # build thread pinned a core for minutes with no SSE consumer
        # attached. Instead we take the returned dict and publish it
        # in ONE _merge into the cumulative cache, then flip the
        # baseline phases ready. The client renders from the cache
        # (the unified-viz phase poller's baseline-ready fallback
        # fetches /api/graph). The on_batch / LayoutAuthority SSE
        # path remains available for the streaming_canvas renderer
        # once its large-batch performance is addressed.
        _stream_pct = {"v": 0.02}  # progress monotone within 0.02–0.28

        # Live event stream — every per-source batch lands here as
        # chunked SSE events the moment the builder emits it. The
        # first visit's browser subscribes to /api/graph/events and
        # appendGraphDelta's each event, so the user watches the
        # graph grow instead of waiting for the full ingest to
        # finish. RESET on every kicked build so a previous build's
        # tail events don't leak into this run's subscribers.
        from cortex_viz.server import graph_event_stream as _events

        _events.reset()
        state._source_totals.clear()

        # Construct the cumulative-cache merge closure now that the SSE
        # event stream module is available. Fresh dedup state per build.
        _merge = make_merge(domain_filter, _events)

        from cortex_viz.handlers.workflow_graph import _node_to_dict

        def _on_source_loaded(label: str, count: int) -> None:
            state._source_totals[label] = count
            _stream_pct["v"] = min(0.28, _stream_pct["v"] + 0.02)
            _set_progress(
                phase=f"loading {label}",
                pct=_stream_pct["v"],
                message=f"loaded {count} {label}",
            )

        # The raw per-source streams repeat items heavily — one
        # discussion node was emitted 3,782 times by its source
        # (measured 2026-06-12); the cache dedups on merge, but
        # _on_batch bypasses the cache, so the wire paid for every
        # repeat. Each id/edge-key goes over the live wire ONCE
        # from _on_batch; the post-bake baseline _merge re-emission
        # (which carries the coordinates the live copies lack) is
        # NOT filtered by these sets, so every node still arrives
        # at most twice: once live, once coordinated.
        _wire_seen_n: set = set()

        def _on_batch(label: str, nodes_objs, edges_objs) -> None:
            """Push per-source batch onto the SSE event queue, in the
            slim wire projection, deduped at the wire level.

            Intentionally no _merge per item — the cumulative cache
            is populated by ONE _merge after build completion. These
            live batches carry null x/y (layout runs after the
            load); the post-bake baseline _merge re-emits the same
            ids WITH coordinates and the client backfills positions
            on the deduped nodes.
            """
            if not nodes_objs and not edges_objs:
                return
            n_slim = []
            for n in nodes_objs:
                d = _node_to_dict(n)
                nid = d.get("id")
                if nid and nid not in _wire_seen_n:
                    _wire_seen_n.add(nid)
                    n_slim.append(_slim_node(d))
            if n_slim:
                _events.emit(label, n_slim, [], chunk=1000)

        # ── Stage 1: skeleton (≪1 s) → first paint ──
        # stage="skeleton" loads only skills + hooks, no memories, no
        # tool_events, no AST. The builder still produces the domain
        # hubs (via _ensure_domain on every node's domain_id), so the
        # client immediately sees the structural backbone instead of
        # waiting ~1–3 min for the full ingest on a large DB.
        try:
            skeleton = build_workflow_graph(
                store,
                domain_filter=domain_filter,
                stage="skeleton",
                defer_native_ast=True,
            )
        except Exception as _exc:  # pragma: no cover - defensive
            print(
                f"[cortex] skeleton build failed: {_exc}",
                file=sys.stderr,
            )
            skeleton = {"nodes": [], "edges": [], "meta": {}}

        _merge(
            skeleton.get("nodes", []),
            skeleton.get("edges", []),
            stage="skeleton",
            pct=0.05,
            message=(
                f"skeleton: {len(skeleton.get('nodes', []))} nodes / "
                f"{len(skeleton.get('edges', []))} edges"
            ),
            phase_key=None,
            baseline_ready=True,
        )
        for _phase_key in ("L0", "L1"):
            _mark_phase_ready(_phase_key)

        # ── Stage 2: full baseline (load + ingest the heavy sources) ──
        # Replaces the cumulative cache with the full graph. The client
        # already painted the skeleton; this fills it in.
        # Default UNCAPPED (user direction 2026-06-12): the 25k
        # hottest-memory cap was a workaround for the fat-JSON wire
        # where every record shipped whole; with the slim wire,
        # on-demand detail, and slim-dict retention in the builder
        # (workflow_graph.py memory loop) the full corpus is tens
        # of MB, and a hard cap is a truncation of the record.
        # CORTEX_VIZ_MEMORY_LIMIT > 0 remains available as an
        # explicit opt-in subset for constrained machines.
        try:
            _mem_limit = int(os.environ.get("CORTEX_VIZ_MEMORY_LIMIT", "0"))
        except ValueError:
            _mem_limit = 0
        baseline = build_workflow_graph(
            store,
            domain_filter=domain_filter,
            stage="full",
            on_source_loaded=_on_source_loaded,
            on_batch=_on_batch,
            defer_native_ast=True,
            memory_limit=_mem_limit,
        )

        # ── Associative community detection (server-side) ──
        # Replaces the browser-side label-propagation that collapsed the
        # dense combined association substrate into one mega-community
        # (87-93% of memories under one label, measured 2026-07-07).
        # Leiden + CPM (resolution-limit-free) on the SPARSE co-entity
        # channel only; the brain view still renders all three additive
        # channels. Stamps community_id on each memory node dict here, so
        # it rides the cache → snapshot → /api/graph/full to the client,
        # which now just reads the field instead of computing anything.
        # Runs in this build child (own GIL) before the bake mutates the
        # same node dicts with x/y — the two fields coexist.
        try:
            from cortex_viz.server.graph_communities import attach_communities

            _comm = attach_communities(baseline)
            print(f"[cortex] associative communities: {_comm}", file=sys.stderr)
        except Exception as _exc:  # pragma: no cover - defensive
            print(
                f"[cortex] community detection skipped: {_exc}",
                file=sys.stderr,
            )

        # ── Bake layout coordinates BEFORE the baseline merge ──
        # The merge's SSE emission snapshots each node into the slim
        # wire tuple, so coordinates must exist at emission time.
        # (The bake used to run after the merge and mutate the dicts
        # the event buffer shared by reference — replay clients saw
        # coords, live clients never did. Bake-before-merge gives
        # both the same coordinated baseline.) One DrL pass (OpenOrd;
        # ~0.8 s for 34k nodes, measured 2026-05-31); on failure the
        # client still settles its own live layout, just slower.
        try:
            from cortex_viz.core import layout_engine

            _bl_nodes = baseline.get("nodes", [])
            _bl_edges = baseline.get("edges", [])
            # FULL DrL layout over ALL baseline nodes (genuine-scaling
            # decision, Mandelbrot+Thompson 2026-06-14). The previous
            # _BACKBONE_KINDS cap laid out only {domain,tool_hub,file}
            # (~15k) with DrL and ray-placed every other node by formula —
            # that cap is exactly the "fakes scaling" the user reported.
            # We now feed EVERY node + its edges to DrL. DrL (OpenOrd) is
            # ~O(N^1.3); this baseline bake runs in the build CHILD's own
            # process (own GIL), so even a multi-second pass does not freeze
            # the HTTP server. This bake covers the legacy/force view; the
            # AUTHORITATIVE full layout persisted to layout_pg_store (used
            # by the tile + quadtree path) is recomputed once at full_ready
            # over the complete post-AST graph (see _persist_full_layout).
            # source: superlinear DrL cost — OpenOrd (Martin et al.,
            # SPIE 2011); _BACKBONE_KINDS cap removed 2026-06-14.
            #
            # Honest progress during the bake: DrL over the full baseline
            # is the dominant wall-clock phase (hours at the 278k-node /
            # 5.5M-edge corpus, observed 2026-07-02) — without this update
            # /api/graph/progress kept showing the LAST source message
            # ("loading memories") for the whole bake.
            _set_progress(
                phase="layout bake (DrL)",
                pct=0.29,
                message=(
                    f"DrL layout over {len(_bl_nodes)} baseline nodes — "
                    "the long phase"
                ),
            )
            _ids = [n["id"] for n in _bl_nodes if n.get("id")]
            _id_set = set(_ids)
            _edge_pairs = []
            for _e in _bl_edges:
                _s = _e.get("source")
                _t = _e.get("target")
                if isinstance(_s, dict):
                    _s = _s.get("id")
                if isinstance(_t, dict):
                    _t = _t.get("id")
                if _s and _t and _s != _t and _s in _id_set and _t in _id_set:
                    _edge_pairs.append((_s, _t))
            _coords = layout_engine.layout(_ids, _edge_pairs) if _ids else []
            _pos = {nid: (x, y) for nid, x, y in _coords}
            # Bake the real DrL coordinate onto every node. Any node the
            # layout could not place (should be none) falls back to a
            # deterministic ray so the slim wire never emits a null coord.
            _ray_fallback = 0
            for _n in _bl_nodes:
                _xy = _pos.get(_n.get("id"))
                if _xy is not None:
                    _n["x"], _n["y"] = _xy[0], _xy[1]
                else:
                    _n["x"], _n["y"] = _place_around(0.0, 0.0, str(_n.get("id")))
                    _ray_fallback += 1
            print(
                f"[cortex] baseline layout baked: {len(_coords)} DrL coords"
                f" ({_ray_fallback} ray fallbacks) for {len(_bl_nodes)} nodes",
                file=sys.stderr,
            )
        except Exception as _exc:  # pragma: no cover - defensive
            print(
                f"[cortex] baseline layout bake skipped: {_exc}",
                file=sys.stderr,
            )

        _merge(
            baseline.get("nodes", []),
            baseline.get("edges", []),
            stage="baseline",
            pct=0.30,
            message=(
                f"baseline: {len(baseline.get('nodes', []))} nodes / "
                f"{len(baseline.get('edges', []))} edges"
            ),
            phase_key=None,
        )
        for _phase_key in ("L0", "L1", "L2", "L3", "L4", "L5"):
            _mark_phase_ready(_phase_key)
        _set_progress(
            phase="baseline_ready",
            pct=0.30,
            message=(
                f"baseline ready: {len(baseline.get('nodes', []))} nodes / "
                f"{len(baseline.get('edges', []))} edges"
            ),
            baseline_ready=True,
        )

        # NO _events.close() here. Closing the stream at baseline
        # made every SSE subscriber receive ``done`` and disconnect
        # BEFORE a single L6 symbol streamed — the galaxy froze at
        # the baseline scale forever (observed 2026-06-12). The
        # stream closes exactly once, in the finally block below,
        # after the FULL build (through L6_CROSS) or on error.

        from cortex_viz.infrastructure.ap_bridge import (
            is_enabled as _ap_enabled,
        )

        if not _ap_enabled():
            # Persist the authoritative full layout for the tile path
            # (no L6/AST nodes when AP is disabled, so the baseline IS the
            # full graph). Runs in this child process — own GIL.
            _set_progress(
                phase="layout",
                pct=0.95,
                message=(
                    f"laying out {len(baseline.get('nodes', []))} nodes "
                    "(full DrL)"
                ),
            )
            _persist_full_layout(store)
            _set_progress(
                phase="full_ready",
                pct=1.0,
                message=f"ready: {len(baseline.get('nodes', []))} nodes "
                "(AP disabled)",
                full_ready=True,
            )
            # finally block closes the SSE stream.
            return

        # File-path → file-id map for DEFINED_IN edge resolution.
        file_id_by_path: dict[str, str] = {}
        for n in baseline.get("nodes", []):
            if n.get("kind") == "file":
                p = n.get("path") or ""
                fid = n.get("id")
                if p and fid:
                    file_id_by_path[p] = fid
                    parts = p.split("/")
                    for i in range(1, len(parts)):
                        file_id_by_path.setdefault("/".join(parts[i:]), fid)

        # ── L6 AST sweep (per-project symbols + edges) ──
        reached_done = run_l6(
            store,
            baseline,
            file_id_by_path,
            merge=_merge,
            set_progress=_set_progress,
            register_phase=_register_phase,
            mark_phase_ready=_mark_phase_ready,
            phase_deps_satisfied=_phase_deps_satisfied,
            persist_full_layout=_persist_full_layout,
        )
        if not reached_done:
            return

        # Done.
        cur = state._graph_cache["data"]
        counts = cur["meta"].get("counts") or {}
        counts["symbols"] = sum(1 for n in cur["nodes"] if n.get("kind") == "symbol")
        counts["ast_edges"] = sum(
            1
            for e in cur["edges"]
            if (e.get("kind") or "")
            in ("defined_in", "calls", "imports", "member_of")
        )
        # Knowledge-graph entities + their MEMORY→ENTITY links
        # (ADR-0046 Gap 10 wiring). Counted at the finalisation step
        # so the stat panel's ``entities`` and
        # ``memory_entity_edges`` fields stay in sync with what the
        # renderer actually shows.
        counts["entities"] = sum(1 for n in cur["nodes"] if n.get("kind") == "entity")
        counts["memory_entity_edges"] = sum(
            1 for e in cur["edges"] if (e.get("kind") or "") == "about_entity"
        )
        cur["meta"]["counts"] = counts
        # Persist the authoritative full layout over the COMPLETE post-AST
        # graph (backbone + memories + symbols + entities). This is the
        # source the tile-pyramid + quadtree default renderer reads. Runs
        # in this child process — own GIL — so the O(N^1.3) DrL pass over
        # all ~150k nodes does not freeze the HTTP server. Decoupled as its
        # own progress phase so the client sees "layout" before "ready".
        _set_progress(
            phase="layout",
            pct=0.97,
            message=f"laying out {len(cur['nodes'])} nodes (full DrL)",
        )
        _persist_full_layout(store)
        _set_progress(
            phase="full_ready",
            pct=1.0,
            message=(
                f"ready: {len(cur['nodes'])} nodes ({counts['symbols']} symbols)"
            ),
            full_ready=True,
            node_count=len(cur["nodes"]),
            edge_count=len(cur["edges"]),
        )
    except Exception as exc:  # pragma: no cover
        print(f"[cortex] background build error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        _set_progress(
            phase="error",
            message=f"{type(exc).__name__}: {exc}",
        )
    finally:
        # Single end-of-stream terminator — success, AP-disabled
        # early return, or error. Subscribers drain whatever was
        # emitted, receive ``done``, and close. close() is
        # idempotent; the buffer survives for late-subscriber
        # replay until the next build's reset().
        try:
            from cortex_viz.server import graph_event_stream as _ev

            _ev.close()
        except Exception:
            pass
        state._graph_build_lock.release()
