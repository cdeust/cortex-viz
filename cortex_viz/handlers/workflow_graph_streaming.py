"""Interleaved load+ingest+emit streaming path for the workflow graph.

Split out of ``handlers/workflow_graph.py`` (was 817 lines). Used when
``on_batch`` is set — small/structural sources stream first so the user
sees a meaningful graph within seconds, with heavy memory/entity/AST
sources streaming in behind. Composition-root layer (wires core builder
+ infrastructure sources). See ``build_workflow_graph`` for the
non-streaming counterpart.
"""

from __future__ import annotations


from cortex_viz.core.workflow_graph_schema import validate_graph
from cortex_viz.handlers.workflow_graph_serialize import (
    _edge_to_dict,
    _node_to_dict,
)


def _build_interleaved(
    *,
    store,
    source,
    domain_filter: str | None,
    min_memory_heat: float,
    memory_limit: int,
    stage: str,
    defer_native_ast: bool,
    on_source_loaded,
    on_batch,
    notify_loaded,
):
    """Interleaved load+ingest+emit path used when on_batch is set.

    Order is deliberate — small / structural sources first so the user
    sees a meaningful graph (domains + skills + hooks + tool_hubs +
    files + discussions) within seconds, with the heavy memory /
    entity / AST sources streaming in behind.
    """
    from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder
    from cortex_viz.core.workflow_graph_builder_relational import (
        ingest_ast_edge,
        ingest_command_file,
        ingest_discussion_agent,
        ingest_discussion_command,
        ingest_discussion_file,
        ingest_discussion_tool,
        ingest_mcp_usage,
        ingest_skill_usage,
        ingest_symbol,
    )
    from cortex_viz.core.workflow_graph_entity import (
        ingest_about_entity,
        ingest_entity,
    )
    from cortex_viz.core.workflow_graph_schema import GLOBAL_DOMAIN_ID

    builder = WorkflowGraphBuilder()
    builder._ensure_domain(GLOBAL_DOMAIN_ID, "global")  # noqa: SLF001

    def _filter(items, key="domain"):
        if not domain_filter:
            return items or []
        return [ev for ev in (items or []) if (ev.get(key) or "") == domain_filter]

    def _emit_delta(label: str, prev_n: int, prev_e: int) -> None:
        if on_batch is None:
            return
        new_edges_raw = builder._edges[prev_e:]  # noqa: SLF001  (list slice — O(new))
        # Only materialise the node-values list when nodes were actually
        # added since the cursor. Edge-only sources (e.g.
        # memory_entity_edges via ingest_about_entity, 110k rows / 221
        # chunks) add zero nodes, so the old unconditional
        # ``list(builder._nodes.values())[prev_n:]`` rebuilt the whole
        # ~29k-node list on every chunk just to slice off an empty tail
        # — O(edges x node_count) churn that pegged a core for ~1 h.
        # source: measured 2026-05-31, /api/graph/progress stuck at
        # "loaded 110436 memory_entity_edges" pct=0.28 for 66 min.
        cur_n = len(builder._nodes)  # noqa: SLF001
        # O(new) slice off the insertion-ordered mirror, not
        # ``list(builder._nodes.values())[prev_n:]`` (O(total) per batch).
        new_nodes = (
            builder._node_order[prev_n:]  # noqa: SLF001
            if cur_n > prev_n
            else []
        )
        if not new_nodes and not new_edges_raw:
            return
        # Intra-batch dedupe so the per-source emission still collapses
        # repeated (src, tgt, kind) edges; cross-source weight summing
        # is preserved by the final _dedupe_and_link below.
        _, new_edges = builder._dedupe_and_link(new_nodes, new_edges_raw)  # noqa: SLF001
        on_batch(label, new_nodes, new_edges)

    # Streaming ingest threshold — emit a partial batch every N items
    # so the user watches the source FILL in (instead of one big burst
    # after the entire source finishes ingesting). With 107 k memories
    # taking ~5 s of pydantic-bound CPU, 500-item chunks at ~40 chunks/s
    # = the browser repaints ~40 times during memories ingest. Small
    # enough sources (<=_INGEST_CHUNK items) still get a single delta.
    _INGEST_CHUNK = 500

    def _ingest_loop(
        label: str,
        items: list,
        fn,
        fn_takes_builder: bool = False,
        stream: bool = True,
    ):
        """Ingest items, emitting a partial delta every _INGEST_CHUNK so
        the SSE subscribers see progress WITHIN the source — not just
        after the whole source finishes ingesting.

        ``stream=False`` ingests the items into the builder (so they ride
        into the final baseline graph) but does NOT emit
        per-chunk batches over the live SSE/layout-authority path. Use it
        for dense edge-only sources whose live streaming dominates build
        time without adding visible structure — chiefly
        ``memory_entity_edges`` (110k MEMORY→ENTITY edges): streaming them
        pushed 110k items through ``_emit_to_authority`` in ~221 batches
        and pegged a core for ~1 h (measured 2026-05-31), and they render
        as a dense hairball anyway (see commit "skip memory_entity_edges
        phase — 110K green cloud was covering galaxy"). They still load
        from the snapshot / on demand.
        """
        items = items or []
        prev_n = len(builder._nodes)  # noqa: SLF001
        prev_e = len(builder._edges)  # noqa: SLF001
        ingested = 0
        for ev in items:
            if fn_takes_builder:
                fn(builder, ev)
            else:
                fn(ev)
            ingested += 1
            if stream and ingested % _INGEST_CHUNK == 0:
                _emit_delta(label, prev_n, prev_e)
                prev_n = len(builder._nodes)  # noqa: SLF001
                prev_e = len(builder._edges)  # noqa: SLF001
        # Final partial chunk (or single emit for small sources).
        if stream:
            _emit_delta(label, prev_n, prev_e)

    # ── Phase 1a: SMALL structural sources first (visible in seconds) ──
    skills = source.load_skills()
    notify_loaded("skills", skills)
    _ingest_loop("skills", skills, builder._ingest_skill)

    hooks = source.load_hooks()
    notify_loaded("hooks", hooks)
    _ingest_loop("hooks", hooks, builder._ingest_hook)

    agents = _filter(source.load_agent_events())
    notify_loaded("agents", agents)
    _ingest_loop("agents", agents, builder._ingest_agent)

    commands = _filter(source.load_command_events(store))
    notify_loaded("commands", commands)
    _ingest_loop("commands", commands, builder._ingest_command)

    discussions = _filter(source.load_discussions())
    notify_loaded("discussions", discussions)
    _ingest_loop("discussions", discussions, builder._ingest_discussion)

    # ── Phase 1b: tool events + file finalisation ──
    # tool_events ingestion accumulates per-file tool counts; the file
    # nodes are materialised when _finalize_files runs after.
    tool_events = _filter(source.load_tool_events(store))
    notify_loaded("tool_events", tool_events)
    _ingest_loop("tool_events", tool_events, builder._ingest_tool_event)

    known_paths = {e.get("file_path") for e in tool_events if e.get("file_path")}
    # file nodes synthesised here — emit as their own batch so the
    # browser can apply them in dependency order before phase 2 edges
    # reference them.
    prev_n = len(builder._nodes)  # noqa: SLF001
    prev_e = len(builder._edges)  # noqa: SLF001
    builder._finalize_files()  # noqa: SLF001
    _emit_delta("files", prev_n, prev_e)

    # ── Phase 1c: entities (medium ~22 k) ──
    entities = _filter(source.load_entities(store))
    notify_loaded("entities", entities)
    _ingest_loop("entities", entities, ingest_entity, fn_takes_builder=True)

    # ── Phase 2: relational edges (need phase 1 nodes) ──
    discussion_files = _filter(source.load_discussion_files())
    notify_loaded("discussion_files", discussion_files)
    _ingest_loop("discussion_files", discussion_files, ingest_discussion_file, True)

    command_files = source.load_command_files(store, known_paths)
    notify_loaded("command_files", command_files)
    _ingest_loop("command_files", command_files, ingest_command_file, True)

    skill_usage = _filter(source.load_skill_usage())
    notify_loaded("skill_usage", skill_usage)
    _ingest_loop("skill_usage", skill_usage, ingest_skill_usage, True)

    mcp_usage = _filter(source.load_mcp_usage())
    notify_loaded("mcp_usage", mcp_usage)
    _ingest_loop("mcp_usage", mcp_usage, ingest_mcp_usage, True)

    discussion_tools = _filter(source.load_discussion_tool_uses())
    notify_loaded("discussion_tools", discussion_tools)
    _ingest_loop("discussion_tools", discussion_tools, ingest_discussion_tool, True)

    discussion_agents = _filter(source.load_discussion_agents())
    notify_loaded("discussion_agents", discussion_agents)
    _ingest_loop("discussion_agents", discussion_agents, ingest_discussion_agent, True)

    discussion_commands = _filter(source.load_discussion_commands())
    notify_loaded("discussion_commands", discussion_commands)
    _ingest_loop(
        "discussion_commands", discussion_commands, ingest_discussion_command, True
    )

    # ── Phase 3: HEAVY sources last (memories + memory_entity_edges) ──
    # Memories are the biggest PG query AND the biggest ingest pass on
    # the user's dev DB (107 k rows). Use a SERVER-SIDE CURSOR so rows
    # arrive in chunks during the query, and ingest + emit per chunk —
    # the SSE subscriber sees memory nodes growing WHILE the query is
    # still running, not after a ~10 s blocking .fetchall().
    # Cap the memory nodes at the top-``memory_limit`` HOTTEST
    # (iter_memories_chunked is ORDER BY heat_base DESC). The DB holds
    # 400k+ memory rows; loading all of them produced a ~484k-node graph
    # that took ~50 s + 6 GB to build and could never render in <=200 ms.
    # The cold long-tail stays reachable via recall / search / on-demand
    # drill. memory_limit=0 means "no cap" (the legacy hard_cap applies).
    # source: measured 2026-05-31 — build log "loaded ... 412000 memories"
    # climbing; user decision = top ~25k hottest in the base galaxy.
    # SUPERSEDED 2026-06-12 (user direction): the 25k cap compensated
    # for the fat-JSON wire where every record shipped whole. With the
    # slim wire + on-demand detail the unbounded path RETAINS slim
    # dicts (lists below) instead of losing memories from the cache.
    memories_total = 0
    retained_memory_nodes: list[dict] = []
    retained_memory_edges: list[dict] = []
    # Structural baseline — every node/edge ingested before the memory
    # phase (domains, hubs, files, discussions, entities ≈ 30k). The
    # memory phase is pruned back to this size after every batch.
    #
    # IMPORTANT: use len(_node_order), NOT len(_nodes). Some relational
    # ingest helpers (ingest_skill_usage, ingest_mcp_usage, ingest_symbol)
    # write nodes directly into b._nodes without appending to b._node_order.
    # This makes len(_nodes) > len(_node_order) by N_missing. If _struct_n
    # were set to len(_nodes), then _node_order[_struct_n:] would be empty
    # (or under-sized), the per-batch discard would miss the first N_missing
    # memory nodes, those nodes would accumulate in _nodes without their
    # in_domain edges (which the discard DID remove), and validate_graph
    # would raise GraphValidationError("memory:X … got 0").
    # source: root-cause traced 2026-06-09.
    _struct_n = len(builder._node_order)  # noqa: SLF001
    _struct_e = len(builder._edges)  # noqa: SLF001
    # memory_limit=0 → stream the FULL corpus. Bounding is by per-batch
    # DISCARD below, not a row cap: the embedding-free projection makes
    # each row ~227 B and pruning keeps the builder at skeleton size, so
    # the whole corpus warms up progressively without a big-bang. A
    # non-zero memory_limit still works as an explicit hottest-N subset.
    _mem_cap = memory_limit if memory_limit and memory_limit > 0 else 0
    # MEMORY→ENTITY links, ingested PER CHUNK while the chunk's memory
    # nodes are still in the builder (the uncapped path discards them
    # right after, and ingest_about_entity skips absent endpoints).
    # Restored 2026-06-13 (user direction: no restraints): the previous
    # build skipped this whole table, so entity panels listed none of
    # their memories. The old hairball/CPU concerns no longer apply —
    # the SSE wire carries ZERO edges (nodes only), and edge cost is
    # spread across the chunk loop instead of one 110k-row burst.
    _links_by_mem: dict[int, list[int]] = {}
    for _lnk in source.load_memory_entity_edges(store):
        _mid = _lnk.get("memory_id")
        _eid = _lnk.get("entity_id")
        if _mid is not None and _eid is not None:
            _links_by_mem.setdefault(_mid, []).append(_eid)
    if on_source_loaded is not None:
        on_source_loaded(
            "memory_entity_edges", sum(len(v) for v in _links_by_mem.values())
        )
    for chunk in source.iter_memories_chunked(
        store, min_heat=min_memory_heat, chunk_size=1000, limit=_mem_cap
    ):
        if domain_filter:
            chunk = [m for m in chunk if (m.get("domain") or "") == domain_filter]
        prev_n = len(builder._nodes)  # noqa: SLF001
        prev_e = len(builder._edges)  # noqa: SLF001
        for ev in chunk:
            builder._ingest_memory(ev)  # noqa: SLF001
        # ABOUT_ENTITY edges for THIS chunk's memories — endpoints are
        # live right now (entities since Phase 1c, memories just above).
        for ev in chunk:
            for _eid in _links_by_mem.get(ev.get("id"), ()):
                ingest_about_entity(
                    builder, {"memory_id": ev.get("id"), "entity_id": _eid}
                )
        memories_total += len(chunk)
        _emit_delta("memories", prev_n, prev_e)
        # ── Bounded retention vs unbounded discard ──
        # When ``_mem_cap > 0`` the memory stream is bounded to the top-N
        # HOTTEST rows (iter_memories_chunked ORDER BY heat_base DESC,
        # ``limit=_mem_cap``). The retained memory set is therefore
        # ≤ ``_mem_cap`` BY CONSTRUCTION, so KEEPING the nodes in the
        # builder/cumulative graph is bounded — the L5 galaxy layer needs
        # them (per-domain memory balls, semantic-stage colors). They flow
        # into _graph_cache via the post-build _merge, and the L5 phase
        # payload (get_phase_payload → _PHASE_KINDS["L5"] = {"memory"})
        # slices them out of that cache.
        # source: cap = CORTEX_VIZ_MEMORY_LIMIT default 25000, measured
        #   2026-05-31; retention bound = retained set ≤ memory_limit.
        #
        # When ``_mem_cap == 0`` the stream is the FULL corpus. The
        # per-batch discard stays (it bounds the BUILDER's pydantic
        # object population — the 4 GB peak measured 2026-06-03 was
        # builder objects, pre-dating the ~227 B/row projection), but
        # each batch is RETAINED as plain slim dicts before the
        # discard, so every memory reaches the cumulative cache, the
        # node index, and the MCP live-cache path. Retention cost is
        # ~0.4 KB/dict — tens of MB for the full corpus, not GB.
        if _mem_cap <= 0:
            for _nd in builder._node_order[_struct_n:]:  # noqa: SLF001
                retained_memory_nodes.append(_node_to_dict(_nd))
                builder._nodes.pop(_nd.id, None)  # noqa: SLF001
            retained_memory_edges.extend(
                _edge_to_dict(_e)
                for _e in builder._edges[_struct_e:]  # noqa: SLF001
            )
            del builder._node_order[_struct_n:]  # noqa: SLF001
            del builder._edges[_struct_e:]  # noqa: SLF001
        # Surface progress every chunk so /api/graph/progress shows the
        # running total — the bottom-of-page poller picks this up.
        if on_source_loaded is not None:
            on_source_loaded("memories", memories_total)

    # memory_entity_edges are now ingested inside the chunk loop above
    # (per-chunk, while endpoints are live) — see _links_by_mem.

    # ── Phase 4: AST symbols (deferred by default in streaming mode) ──
    if stage == "full" and not defer_native_ast:
        from cortex_viz.infrastructure.workflow_graph_source_ast import (
            WorkflowGraphASTSource,
        )
        from cortex_viz.infrastructure.workflow_graph_source_native_ast import (
            WorkflowGraphNativeASTSource,
        )

        ast_source = WorkflowGraphASTSource()
        ast_symbols = ast_source.load_symbols([]) if ast_source.enabled() else []
        ast_edges = ast_source.load_ast_edges([]) if ast_source.enabled() else []
        native_source = WorkflowGraphNativeASTSource()
        if known_paths:
            ast_symbols.extend(native_source.load_symbols(list(known_paths)))
            ast_edges.extend(native_source.load_ast_edges(list(known_paths)))
        notify_loaded("ast_symbols", ast_symbols)
        _ingest_loop("ast_symbols", ast_symbols, ingest_symbol, True)
        notify_loaded("ast_edges", ast_edges)
        _ingest_loop("ast_edges", ast_edges, ingest_ast_edge, True)

    # Final pass: cross-source dedup (same contract as builder.build()).
    nodes, edges = builder._dedupe_and_link(  # noqa: SLF001
        builder._nodes.values(),  # noqa: SLF001
        builder._edges,  # noqa: SLF001
    )
    validate_graph(nodes, edges)

    domain_count = sum(1 for n in nodes if n.kind == "domain")
    memory_count = sum(1 for n in nodes if n.kind == "memory") + len(
        retained_memory_nodes
    )
    file_count = sum(1 for n in nodes if n.kind == "file")
    discussion_count = sum(1 for n in nodes if n.kind == "discussion")
    symbol_count = sum(1 for n in nodes if n.kind == "symbol")
    entity_node_count = sum(1 for n in nodes if n.kind == "entity")
    # Retained memory dicts join AFTER validate_graph: their edges are
    # memory→domain in_domain links plus memory→entity about_entity
    # links whose non-memory endpoint is in the validated structural
    # set and whose memory endpoint is in the retained set — consistent
    # by construction (each batch was ingested by the same builder
    # before the slim-dict capture).
    node_dicts = [_node_to_dict(n) for n in nodes] + retained_memory_nodes
    edge_dicts = [_edge_to_dict(e) for e in edges] + retained_memory_edges
    return {
        "nodes": node_dicts,
        "edges": edge_dicts,
        "links": edge_dicts,
        "meta": {
            "schema": "workflow_graph.v1",
            "domain_filter": domain_filter,
            "node_count": len(node_dicts),
            "edge_count": len(edge_dicts),
            "domain_count": domain_count,
            "memory_count": memory_count,
            "entity_count": entity_node_count,
            "file_count": file_count,
            "discussion_count": discussion_count,
            "counts": {
                "nodes": len(node_dicts),
                "edges": len(edge_dicts),
                "tool_events": len(tool_events),
                "skills": len(skills),
                "hooks": len(hooks),
                "agents": len(agents),
                "commands": len(commands),
                "memories": memories_total,
                "discussions": len(discussions),
                "files": file_count,
                "symbols": symbol_count,
                "entities": entity_node_count,
            },
            "ast_enabled": (stage == "full" and not defer_native_ast),
            "streaming": "interleaved",
        },
    }
