"""Composition root for the workflow graph.

Wires ``WorkflowGraphSource`` (infrastructure) to ``WorkflowGraphBuilder``
(core) and validates via ``validate_graph``. Returns a JSON-serializable
payload shaped for the D3 renderer in ui/unified/js/workflow_graph.js.
"""

from __future__ import annotations

from typing import Any

from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder
from cortex_viz.core.workflow_graph_inputs import WorkflowBuildInputs
from cortex_viz.core.workflow_graph_schema import (
    GraphValidationError,
    validate_graph,
)
from cortex_viz.infrastructure.workflow_graph_source import WorkflowGraphSource
from cortex_viz.infrastructure.workflow_graph_source_ast import (
    WorkflowGraphASTSource,
)
from cortex_viz.infrastructure.workflow_graph_source_native_ast import (
    WorkflowGraphNativeASTSource,
)

# Serialization helpers + the interleaved streaming path were split into
# sibling modules (500-line limit). Re-exported here so historical import
# paths — chiefly ``from cortex_viz.handlers.workflow_graph import
# _node_to_dict`` (server/graph_build_run.py) — keep resolving.
from cortex_viz.handlers.workflow_graph_serialize import (  # noqa: F401
    _CAMEL_ALIASES,
    _GLOBAL_DOMAIN_TOKEN,
    _edge_to_dict,
    _node_to_dict,
    _plain_domain,
)
from cortex_viz.handlers.workflow_graph_streaming import _build_interleaved


def build_workflow_graph(
    store,
    *,
    domain_filter: str | None = None,
    min_memory_heat: float = 0.0,
    memory_limit: int = 0,  # 0 = unbounded (pg_store convention)
    stage: str = "full",
    on_source_loaded: Any = None,
    on_batch: Any = None,
    defer_native_ast: bool = False,
) -> dict[str, Any]:
    """Load sources, build the graph, validate, and return JSON payload.

    The output shape mirrors the legacy ``/api/graph`` response
    (``{nodes, edges, meta}``) so the existing bridge in
    workflow_graph_bridge.js can auto-detect it and route to the new
    renderer.

    Progressive-reveal stages so the first response comes back
    instantly and heavy data streams in as it becomes available:

      * ``skeleton`` — domains, skills, hooks, agents, tool hubs, MCPs,
        memories, discussions, commands. **No file nodes, no AST.**
        This is what the client sees on the very first request.
      * ``files``    — skeleton plus the file nodes derived from
        Claude-session tool events and command-file attribution.
      * ``full``     — everything including AST symbols + edges from
        every indexed AP graph. Used for the final steady-state cache.

    The background enricher in ``http_standalone_graph`` drives this
    sequence: it publishes a skeleton within ~500ms, then republishes
    files, then republishes with AST. The client polls every 4s and
    renders the deltas so projects / files / symbols fade in instead of
    popping in all at once.

    Streaming hooks (added 2026-05-27 to address the synchronous-blob
    measurement on ``wip/layout-authority-sse-streaming``):

      * ``on_source_loaded(label, count)`` — invoked after every PG
        query returns, before any ingestion. Lets the caller post
        progress messages ("loaded 6,315 memories") so the client
        sees the work *in flight* rather than only the result. Pure
        observability, no behavioural effect.
      * ``on_batch(label, new_nodes, new_edges)`` — invoked after the
        builder finishes ingesting one source. Lets the caller push
        the delta into the LayoutAuthority / SSE producer immediately
        instead of waiting for the whole graph to be built. The yielded
        edges are already intra-batch deduped (see
        ``WorkflowGraphBuilder.streaming_build`` docstring); the final
        return dict is unchanged from the non-streaming path.
    """
    source = WorkflowGraphSource()

    def _notify_loaded(label: str, payload) -> None:
        """Report that source ``label`` finished loading."""
        if on_source_loaded is not None:
            on_source_loaded(label, len(payload) if payload is not None else 0)

    # ── Interleaved load + ingest + emit (streaming only) ──
    # When on_batch is set the browser is watching a live SSE stream of
    # batches — the user EXPECTS to see nodes appear progressively. The
    # default path (load every PG source up-front, then call
    # builder.build()) makes streaming meaningless: the first batch
    # only fires after every PG query has finished, which on the dev
    # DB is ~100 s of silence. The interleaved path below loads each
    # PG source, immediately ingests it into a long-lived builder, and
    # emits the per-source delta — so first paint lands ~1 s after the
    # first small source query returns. Small sources are ordered first
    # so the user sees a meaningful structural graph (domains + skills
    # + hooks + tool_hubs + files + discussions) within ~5 s, with
    # heavy sources (memories, memory_entity_edges, AST) streaming in
    # behind. domain_filter is applied per-source rather than over the
    # combined input list.
    if on_batch is not None and stage in ("files", "full"):
        return _build_interleaved(
            store=store,
            source=source,
            domain_filter=domain_filter,
            min_memory_heat=min_memory_heat,
            memory_limit=memory_limit,
            stage=stage,
            defer_native_ast=defer_native_ast,
            on_source_loaded=on_source_loaded,
            on_batch=on_batch,
            notify_loaded=_notify_loaded,
        )
    # Skeleton stage is the first paint — it must be lightweight. Only
    # load the L1 structural skeleton (domains + skills + hooks, at
    # most a few dozen nodes). Tool events, agents, commands, memories,
    # discussions, skill / MCP usage, and discussion-scoped relations
    # all stream in as live-tail deltas (``_emit_memory_deltas``,
    # ``_emit_file_deltas``, ``_emit_roster_deltas``) so the first
    # paint lands before the browser ever asks for data.
    if stage == "skeleton":
        skills = source.load_skills()
        hooks = source.load_hooks()
        agents = []
        commands = []
        memories = []
        discussions = []
        skill_usage = []
        mcp_usage = []
        discussion_tools = []
        discussion_agents = []
        discussion_commands = []
        entities = []
        memory_entity_edges = []
    else:
        skills = source.load_skills()
        _notify_loaded("skills", skills)
        hooks = source.load_hooks()
        _notify_loaded("hooks", hooks)
        agents = source.load_agent_events()
        _notify_loaded("agents", agents)
        commands = source.load_command_events(store)
        _notify_loaded("commands", commands)
        memories = source.load_memories(
            store, min_heat=min_memory_heat, limit=memory_limit
        )
        _notify_loaded("memories", memories)
        discussions = source.load_discussions()
        _notify_loaded("discussions", discussions)
        skill_usage = source.load_skill_usage()
        _notify_loaded("skill_usage", skill_usage)
        mcp_usage = source.load_mcp_usage()
        _notify_loaded("mcp_usage", mcp_usage)
        discussion_tools = source.load_discussion_tool_uses()
        _notify_loaded("discussion_tools", discussion_tools)
        discussion_agents = source.load_discussion_agents()
        _notify_loaded("discussion_agents", discussion_agents)
        discussion_commands = source.load_discussion_commands()
        _notify_loaded("discussion_commands", discussion_commands)
        # Knowledge-graph entities + their memory-link table. Both are
        # bounded by memory-heat (archived / cold memories don't land
        # in the graph, so their links silently drop in
        # ``ingest_about_entity``).
        entities = source.load_entities(store)
        _notify_loaded("entities", entities)
        memory_entity_edges = source.load_memory_entity_edges(store)
        _notify_loaded("memory_entity_edges", memory_entity_edges)

    # File-derived sources are deferred until ``stage`` reaches files.
    if stage in ("files", "full"):
        tool_events = source.load_tool_events(store)
        discussion_files = source.load_discussion_files()
    else:
        tool_events = []
        discussion_files = []

    known_paths = {e.get("file_path") for e in tool_events if e.get("file_path")}
    command_files = (
        source.load_command_files(store, known_paths)
        if stage in ("files", "full")
        else []
    )

    # AST enrichment — loaded synchronously at the ``full`` stage so
    # the WebGL renderer sees every indexed symbol on the first
    # ``/api/graph`` fetch. Two sources feed the L6 ring:
    #
    #   1. AP (automatised-pipeline, when enabled) — 5-layer resolver
    #      with LSP, macro expansion, stdlib indexing. Broad and deep
    #      but requires a prior re-index.
    #   2. Native in-house tree-sitter (always available) — parses the
    #      exact files Claude touched this session. Narrower scope but
    #      zero setup and zero staleness.
    #
    # We UNION the two (Von Neumann §4 transfer: one structural graph
    # with multiple providers). `_dedupe_and_link` in the builder sums
    # weights on (src, tgt, kind) collisions so overlap is idempotent.
    # Files AP hasn't indexed still show symbol depth via native AST.
    if stage == "full":
        ast_source = WorkflowGraphASTSource()
        ast_symbols = ast_source.load_symbols([]) if ast_source.enabled() else []
        ast_edges = ast_source.load_ast_edges([]) if ast_source.enabled() else []
        # Native fallback / complement: parses files Claude touched this
        # session. De-duplicates against AP output via NodeIdFactory in
        # `ingest_symbol`; AP's richer symbols win because they are
        # loaded first and `ingest_symbol` returns early on existing id.
        #
        # DEFERRED when defer_native_ast=True: tree-sitter parsing every
        # file in known_paths is the dominant baseline cost — measured
        # 58.6 s of a 99 s build on 2026-05-27, blocking first paint with
        # no progress feedback. The http_standalone_graph baseline build
        # passes defer_native_ast=True so the structural graph
        # (domains/files/memories/entities) lands fast; AST symbols still
        # arrive via the L6 AP loop in _run, which streams per-project.
        # Callers wanting a complete single-shot result (legacy /api/graph
        # fetch, tests) leave the flag False and keep the native parse.
        native_source = WorkflowGraphNativeASTSource()
        if known_paths and not defer_native_ast:
            native_symbols = native_source.load_symbols(list(known_paths))
            native_edges = native_source.load_ast_edges(list(known_paths))
            ast_symbols.extend(native_symbols)
            ast_edges.extend(native_edges)
    else:
        ast_symbols = []
        ast_edges = []

    if domain_filter:

        def _matches(ev):
            return (ev.get("domain") or "") == domain_filter

        tool_events = [e for e in tool_events if _matches(e)]
        agents = [e for e in agents if _matches(e)]
        commands = [e for e in commands if _matches(e)]
        memories = [m for m in memories if (m.get("domain") or "") == domain_filter]
        discussions = [d for d in discussions if _matches(d)]
        skill_usage = [s for s in skill_usage if _matches(s)]
        mcp_usage = [m for m in mcp_usage if _matches(m)]
        discussion_tools = [e for e in discussion_tools if _matches(e)]
        discussion_agents = [e for e in discussion_agents if _matches(e)]
        entities = [e for e in entities if _matches(e)]

    builder = WorkflowGraphBuilder()
    build_inputs = WorkflowBuildInputs(
        tool_events=tool_events,
        skill_paths=skills,
        hook_defs=hooks,
        agent_events=agents,
        command_events=commands,
        memories=memories,
        discussions=discussions,
        entities=entities,
        discussion_file_events=discussion_files,
        skill_usage_events=skill_usage,
        command_file_events=command_files,
        mcp_usage_events=mcp_usage,
        discussion_tool_events=discussion_tools,
        discussion_agent_events=discussion_agents,
        discussion_command_events=discussion_commands,
        memory_entity_edges=memory_entity_edges,
        ast_symbols=ast_symbols,
        ast_edges=ast_edges,
    )
    if on_batch is None:
        # Non-streaming path — preserve historical behaviour exactly:
        # one synchronous build, dedup-and-link applied at the end
        # across the whole accumulated edge set.
        nodes, edges = builder.build(build_inputs)
    else:
        # Streaming path — drain the per-source generator, accumulate
        # the deltas, and run a final cross-source dedup so the return
        # value matches the non-streaming contract bit-for-bit.
        # Different sources emit different ``EdgeKind`` values so
        # cross-source key collisions are impossible by construction,
        # but the final pass keeps us honest if that ever changes.
        nodes_all: list = []
        edges_all: list = []
        for _label, new_nodes, new_edges in builder.streaming_build(
            build_inputs, on_batch=on_batch
        ):
            nodes_all.extend(new_nodes)
            edges_all.extend(new_edges)
        nodes, edges = builder._dedupe_and_link(nodes_all, edges_all)  # noqa: SLF001

    validate_graph(nodes, edges)

    domain_count = sum(1 for n in nodes if n.kind == "domain")
    memory_count = sum(1 for n in nodes if n.kind == "memory")
    file_count = sum(1 for n in nodes if n.kind == "file")
    discussion_count = sum(1 for n in nodes if n.kind == "discussion")
    symbol_count = sum(1 for n in nodes if n.kind == "symbol")
    entity_node_count = sum(1 for n in nodes if n.kind == "entity")

    return {
        "nodes": [_node_to_dict(n) for n in nodes],
        "edges": [_edge_to_dict(e) for e in edges],
        "links": [_edge_to_dict(e) for e in edges],
        "meta": {
            "schema": "workflow_graph.v1",
            "domain_filter": domain_filter,
            # Legacy stat-panel keys (polling.js.updateStats reads these).
            "node_count": len(nodes),
            "edge_count": len(edges),
            "domain_count": domain_count,
            "memory_count": memory_count,
            "entity_count": entity_node_count,
            "file_count": file_count,
            "discussion_count": discussion_count,
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "tool_events": len(tool_events),
                "skills": len(skills),
                "hooks": len(hooks),
                "agents": len(agents),
                "commands": len(commands),
                "memories": len(memories),
                "discussions": len(discussions),
                "files": file_count,
                "symbols": symbol_count,
                "ast_edges": len(ast_edges),
                "entities": entity_node_count,
                "memory_entity_edges": len(memory_entity_edges),
            },
            # ``ast_source`` is only constructed at the ``full`` stage;
            # earlier stages report ast_enabled based on the env flag
            # so the client can show "enabled, not yet loaded" state.
            "ast_enabled": (
                WorkflowGraphASTSource().enabled()
                if stage == "full"
                else (stage == "full")
            ),
        },
    }



__all__ = ["build_workflow_graph", "GraphValidationError"]
