"""The cumulative-cache merge closure for the background build.

Extracted verbatim from ``graph_build_run._run``'s inner ``_merge`` closure
(behaviour-preserving split, 2026-06-14). ``make_merge`` reconstructs the exact
same closure: the per-build dedup sets (``seen_n``/``seen_e``) and kind tallies
(``kind_counts``) are captured locally — one fresh set per build, identical to
the in-line original — and ``domain_filter`` + the ``graph_event_stream``
module are bound from the caller.

Shared cache state lives in ``graph_cache_state`` (the single owner): the
merge mutates it via ``state.X = ...`` direct attribute assignment.
"""

from __future__ import annotations

import sys
import time

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_build_helpers import _set_progress
from cortex_viz.server.graph_wire import _slim_node


def make_merge(domain_filter: str | None, events):
    """Return the build's ``_merge`` callback with fresh dedup state.

    ``events`` is the ``graph_event_stream`` module (the live SSE delivery
    path). The returned closure has the SAME signature and behaviour as the
    in-line ``_run._merge``.
    """
    # ── Incremental merge state ──
    # Dedup sets + kind tallies persist across _merge calls instead
    # of being rebuilt from the whole cumulative cache per batch.
    # The rebuild made every 200-node L6 batch O(cache): at ~200k
    # nodes / 480k edges × ~3,350 batches the build thread pinned
    # the GIL for hours and starved every HTTP request — the page
    # froze and node clicks timed out (observed 2026-06-12).
    seen_n: set = set()
    seen_e: set = set()
    kind_counts: dict[str, int] = {}

    def _merge(new_nodes, new_edges, stage, pct, message, phase_key=None, **flags):
        """Append ``new_nodes`` + ``new_edges`` into the cumulative
        cache AND into the phase-scoped buffer, then emit the
        actually-added delta on the live SSE stream
        (``/api/graph/events``) — the single real-time delivery
        path the browser renders from.

        Incremental: O(batch) per call via the closure dedup state
        above. Emitting only the added items keeps the SSE replay
        buffer free of duplicates (the client dedups anyway, but
        re-streaming the whole baseline doubled the wire traffic).
        """
        cur = (
            state._graph_cache["data"]
            if state._graph_cache
            else {"nodes": [], "edges": [], "links": [], "meta": {}}
        )
        added_nodes: list[dict] = []
        added_edges: list[dict] = []
        for n in new_nodes:
            nid = n.get("id")
            if nid and nid not in seen_n:
                cur["nodes"].append(n)
                seen_n.add(nid)
                added_nodes.append(n)
                state._node_index[nid] = n
                k = n.get("kind") or n.get("type") or ""
                kind_counts[k] = kind_counts.get(k, 0) + 1
                if k == "domain":
                    dk = n.get("label") or n.get("domain") or ""
                    if dk:
                        state._cached_domain_hub_ids[dk] = nid
        for e in new_edges:
            key = (e.get("source"), e.get("target"), e.get("kind"))
            if key not in seen_e:
                cur["edges"].append(e)
                seen_e.add(key)
                added_edges.append(e)
                s, t, ek = key
                if s and t:
                    state._adjacency.setdefault(s, []).append((t, ek, "out"))
                    state._adjacency.setdefault(t, []).append((s, ek, "in"))
        cur["links"] = cur["edges"]
        cur.setdefault("meta", {})
        cur["meta"]["stage"] = stage
        cur["meta"]["node_count"] = len(cur["nodes"])
        cur["meta"]["edge_count"] = len(cur["edges"])
        cur["meta"]["schema"] = "workflow_graph.v1"
        cur["meta"]["domain_count"] = kind_counts.get("domain", 0)
        # memory_count: when CORTEX_VIZ_MEMORY_LIMIT > 0 the builder now
        # RETAINS the capped memory nodes, so kind_counts["memory"] is
        # the true in-galaxy count (== _source_totals["memories"] when
        # bounded). On the unbounded path the nodes are still discarded
        # and only _source_totals carries the built total. max() is
        # correct in both cases. source: bounded retention (workflow_graph.py).
        cur["meta"]["memory_count"] = max(
            kind_counts.get("memory", 0),
            state._source_totals.get("memories", 0),
        )
        cur["meta"]["discussion_count"] = kind_counts.get("discussion", 0)
        # "Entity" in the legend covers every non-domain, non-memory
        # knowledge node (files, symbols, tools, commands, agents,
        # skills, hooks, discussions, MCPs). Compute as the sum.
        cur["meta"]["entity_count"] = (
            len(cur["nodes"])
            - kind_counts.get("domain", 0)
            - kind_counts.get("memory", 0)
        )
        # Copy — the finalisation step adds derived keys (symbols,
        # ast_edges, …) to meta["counts"]; they must not leak back
        # into the incremental per-kind tallies.
        cur["meta"]["counts"] = dict(kind_counts)
        # Per-phase delta buffer for ``GET /api/graph/phase?name=…``
        # (diagnostic endpoint). added_* are globally deduped above,
        # so a plain extend keeps the buffer duplicate-free.
        if phase_key and phase_key in state._phase_payloads:
            buf = state._phase_payloads[phase_key]
            buf["nodes"].extend(added_nodes)
            buf["edges"].extend(added_edges)
        state._graph_cache = {"data": cur, "domain_filter": domain_filter}
        state._graph_cache_ts = time.monotonic()
        # ── Live SSE emission — the delivery path ──
        # Every _merge (skeleton, baseline, every L6 batch) pushes
        # its added delta onto the event stream the moment it lands
        # in the cache, projected to the slim wire format (see
        # _slim_node) — the full records stay in the
        # cache for /api/graph/node and /api/graph/slice.
        _slim_added = [_slim_node(n) for n in added_nodes] if added_nodes else []
        # In-process SSE emit — the real-time delivery path WHEN the build
        # runs in the server process. In the build CHILD (_SINK_Q set) this
        # stream has no subscribers (the SSE handler lives in the server),
        # so emitting here is pure wasted CPU; the server re-emits via
        # apply_delta when it drains the forwarded delta. Guard it off.
        if state._SINK_Q is None:
            try:
                if _slim_added:
                    events.emit(stage, _slim_added, [], chunk=1000)
            except Exception as _exc:  # pragma: no cover - defensive
                print(
                    f"[cortex] sse stream emission error: {_exc}",
                    file=sys.stderr,
                )
        # Forward NATURALLY-BOUNDED deltas to the server process for live
        # SSE. The baseline merge is one giant O(N) blob (~10^5 nodes) — it
        # must NOT stream over the queue (it would choke the feeder and
        # re-pin a core); it rides the final graph_file out-of-band, like
        # the full graph. Only the L6 per-batch deltas (~200 nodes, _BATCH)
        # stream, so no message on the queue is ever O(N) — no cap needed.
        if (
            state._SINK_Q is not None
            and stage != "baseline"
            and (added_nodes or added_edges)
        ):
            # INV-NODE: forward FULL dicts (added_nodes), not the slim
            # projection — the server cache is always dicts; the server
            # slims only on SSE emit. The REAL phase_key rides as a field
            # so the server reconstructs _phase_payloads from the stream.
            state._forward(("delta", phase_key, stage, added_nodes, added_edges))
        _set_progress(
            phase=stage,
            pct=pct,
            message=message,
            node_count=len(cur["nodes"]),
            edge_count=len(cur["edges"]),
            **flags,
        )

    return _merge
