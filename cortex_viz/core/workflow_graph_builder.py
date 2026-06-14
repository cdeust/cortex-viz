"""Workflow graph builder — reduces heterogeneous Claude-surface events to
the canonical node/edge vocabulary in ``workflow_graph_schema``. Pure core,
no I/O; malformed inputs raise ``ValueError``.

Canonical input forms (see infrastructure.workflow_graph_source):
  tool_events:    {"tool", "file_path"|None, "domain", "count"}
  skill_paths:    {"name", "path", "domains": list[str]}  (empty -> global)
  hook_defs:      {"event", "matcher"|"", "command", "domain"|None}
  agent_events:   {"subagent_type", "domain", "count"}
  command_events: {"cmd", "cmd_hash", "domain", "count"}
  memories:       PG rows (id, domain, consolidation_stage, heat_base, content)
  discussions:    {"session_id", "domain", "title", "message_count"}

Post-file-finalisation relational ingest (discussion → file / tool_hub /
agent / command, skill / mcp usage) lives in
``workflow_graph_builder_relational`` so this module stays inside the
300-line ceiling.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Callable, Iterable, List, Optional, Tuple

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
from cortex_viz.core.workflow_graph_inputs import WorkflowBuildInputs
from cortex_viz.core.workflow_graph_palette import (
    DOMAIN_COLOR,
    TOOL_HUB_COLORS,
)
from cortex_viz.core.workflow_graph_schema import (
    GLOBAL_DOMAIN_ID,
    EdgeKind,
    NodeIdFactory,
    NodeKind,
    ToolKind,
    WorkflowEdge,
    WorkflowNode,
)

# Per-source node-ingest helpers were split into
# ``workflow_graph_builder_ingest`` (500-line limit). Imported here; the
# class methods below delegate to them so the streaming-build dispatch
# table (``self._ingest_*``) is unchanged. ``_require`` / ``_as_tool`` /
# ``_MEMORY_SCIENTIFIC_KEYS`` are re-exported for any historical importer.
from cortex_viz.core.workflow_graph_builder_ingest import (  # noqa: F401
    _MEMORY_SCIENTIFIC_KEYS,
    _as_tool,
    _finalize_files,
    _ingest_agent,
    _ingest_command,
    _ingest_discussion,
    _ingest_hook,
    _ingest_memory,
    _ingest_skill,
    _ingest_tool_event,
    _require,
    _track_file_timestamp,
)


class WorkflowGraphBuilder:
    """Reduce seven data sources to canonical (nodes, edges)."""

    def __init__(self) -> None:
        self._nodes: dict[str, WorkflowNode] = {}
        # Insertion-ordered mirror of ``_nodes.values()``. Appended in
        # lock-step at every NEW insert so per-batch deltas slice
        # ``_node_order[prev_n:]`` in O(new) instead of materialising
        # ``list(_nodes.values())[prev_n:]`` in O(total) every batch — the
        # latter was O(N²) over a streaming build and pegged a core for
        # ~1 h on the memory phase (measured 2026-05-31). len(_node_order)
        # == len(_nodes) always; prev_n cursors index both identically.
        self._node_order: list[WorkflowNode] = []
        self._edges: list[WorkflowEdge] = []
        self._file_tool_counts: dict[str, Counter[ToolKind]] = defaultdict(Counter)
        self._file_domains: dict[str, set[str]] = defaultdict(set)
        self._file_timestamps: dict[str, dict[str, str | None]] = {}

    def build(self, inputs: WorkflowBuildInputs):
        """Ingest every stream in ``inputs`` and return (nodes, edges).

        Backwards-compatible wrapper around ``streaming_build`` that
        drains all batches and returns the final accumulated graph.
        Callers wanting per-batch emission should use ``streaming_build``
        directly.
        """
        for _ in self.streaming_build(inputs, on_batch=None):
            pass
        return self._dedupe_and_link(self._nodes.values(), self._edges)

    def streaming_build(
        self,
        inputs: WorkflowBuildInputs,
        on_batch: Optional[
            Callable[[str, List[WorkflowNode], List[WorkflowEdge]], None]
        ] = None,
    ):
        """Generator variant: yield ``(label, new_nodes, new_edges)`` per
        source-ingest step.

        Why this exists: a synchronous ``build()`` call against the
        ``stage="full"`` inputs runs ~13 PG queries and ~3 ingest phases
        before returning anything to the caller. Measured baseline on
        the dev DB: ~150 s before the first node reaches the SSE
        producer, even though the layout authority and SSE transport
        are designed to stream. Cochrane Finding A's Act-channel never
        fires in that window because the producer never reaches the
        inter-batch seams. See ``tasks/layout-authority/audits/cochrane.md``
        §12 and the run-time measurement on 2026-05-27.

        Streaming order respects the builder's three-phase contract:
            phase 1 — node-bearing sources (one batch per source)
            files   — file finalisation (synthetic batch)
            phase 2 — relational sources (one batch per source)
            phase 3 — AST symbols + AST edges (two batches)

        The yielded ``new_nodes`` / ``new_edges`` are the deltas added
        by THAT source — already deduped within the batch via
        ``_dedupe_and_link`` on just the new edges, so cross-source
        weight summing is preserved for the final ``build()`` return
        (different sources emit different ``EdgeKind`` values, so
        cross-source key collisions are impossible by construction).

        ``on_batch`` is invoked with the same triple just before each
        yield, for callers that prefer push semantics (e.g. wiring
        into ``LayoutAuthority.add_node``). When ``None`` the generator
        still yields — drain it with ``for _ in ...: pass`` to run the
        ingest without emission.
        """
        # Capture the offsets BEFORE _ensure_domain so the synthetic
        # ``domain:__global__`` node is included in the first batch's
        # delta. Otherwise it stays at index 0, every batch slices
        # ``[prev_n:]`` with prev_n>=1, the global node is never
        # emitted, and validate_graph rejects the in_domain edges that
        # target it ("edge target missing: domain:__global__").
        prev_n = len(self._nodes)
        prev_e = len(self._edges)
        self._ensure_domain(GLOBAL_DOMAIN_ID, "global")

        def _emit(label: str):
            nonlocal prev_n, prev_e
            # O(new) slice off the insertion-ordered mirror — NOT
            # ``list(self._nodes.values())[prev_n:]`` which is O(total)
            # every batch (the O(N²) streaming hang).
            new_nodes = self._node_order[prev_n:]
            new_edges_raw = self._edges[prev_e:]
            # Intra-batch dedup-and-link: collapses repeat (src,tgt,kind)
            # edges within this source and sums their weights. Cheap
            # because the batch is the size of one source's output, not
            # the whole graph.
            _, new_edges = self._dedupe_and_link(new_nodes, new_edges_raw)
            prev_n = len(self._nodes)
            prev_e = len(self._edges)
            if on_batch is not None:
                on_batch(label, new_nodes, new_edges)
            return label, new_nodes, new_edges

        # Phase 1: node ingestion. Mix of self-bound builder methods
        # (for kinds the builder owns) and free functions that take
        # the builder as first arg (for externalised kinds like
        # ENTITY). The dispatch shape is the same for both.
        phase1: Tuple[Tuple[str, list, object], ...] = (
            ("tool_events", inputs.tool_events, self._ingest_tool_event),
            ("skills", inputs.skill_paths, self._ingest_skill),
            ("hooks", inputs.hook_defs, self._ingest_hook),
            ("agents", inputs.agent_events, self._ingest_agent),
            ("commands", inputs.command_events, self._ingest_command),
            ("memories", inputs.memories, self._ingest_memory),
            ("discussions", inputs.discussions, self._ingest_discussion),
        )
        for label, events, fn in phase1:
            for ev in events or []:
                fn(ev)
            yield _emit(label)
        for ev in inputs.entities or []:
            ingest_entity(self, ev)
        yield _emit("entities")
        # File finalisation depends on the cumulative tool/discussion
        # ingestion above — synthesised as its own batch so the SSE
        # producer sees file nodes before any phase-2 edge references
        # them. The LayoutAuthority's I3 invariant tolerates late
        # arrivals via the pending-symbols buffer, but emitting in
        # dependency order minimises buffering pressure.
        self._finalize_files()
        yield _emit("files")
        # Phase 2: relational edges. Every helper takes the builder
        # as first arg, assumes file nodes exist.
        phase2: Tuple[Tuple[str, list, object], ...] = (
            ("discussion_files", inputs.discussion_file_events, ingest_discussion_file),
            ("command_files", inputs.command_file_events, ingest_command_file),
            ("skill_usage", inputs.skill_usage_events, ingest_skill_usage),
            ("mcp_usage", inputs.mcp_usage_events, ingest_mcp_usage),
            ("discussion_tools", inputs.discussion_tool_events, ingest_discussion_tool),
            (
                "discussion_agents",
                inputs.discussion_agent_events,
                ingest_discussion_agent,
            ),
            (
                "discussion_commands",
                inputs.discussion_command_events,
                ingest_discussion_command,
            ),
            ("memory_entity_edges", inputs.memory_entity_edges, ingest_about_entity),
        )
        for label, events, fn in phase2:
            for ev in events or []:
                fn(self, ev)
            yield _emit(label)
        # Phase 3 (ADR-0046): AST enrichment. Symbols attach to files,
        # AST edges attach to symbols — silently skip when their parent
        # is missing. Empty lists when AP isn't configured.
        for sym in inputs.ast_symbols or []:
            ingest_symbol(self, sym)
        yield _emit("ast_symbols")
        for edge in inputs.ast_edges or []:
            ingest_ast_edge(self, edge)
        yield _emit("ast_edges")

    # ── al-jabr: fill missing domain / classify file tool mix ─────────

    def _assign_domain(self, domain_id, known_project_roots=()):
        _ = known_project_roots
        if not domain_id:
            return GLOBAL_DOMAIN_ID
        if domain_id.startswith("domain:"):
            return domain_id
        # Canonicalise via the git-derived registry. This collapses
        # worktree-path slugs (e.g. "…-worktrees-pipeline-…-body") and
        # known aliases (subagents → zetetic-team-subagents,
        # cowork → cortex) so the viz never emits a hub for free-text
        # noise. Pure-noise leftovers from legacy backfills — single-word
        # slug tails like "voice", "for", "via" — round-trip through
        # ``resolve_domain`` unchanged, signalling "no canonical match";
        # those are bucketed to GLOBAL_DOMAIN_ID rather than allowed to
        # spawn an orphan hub with no real meaning.
        from cortex_viz.shared.domain_mapping import _build_registry, resolve_domain

        resolved = resolve_domain(domain_id) or domain_id
        if resolved.startswith("-"):
            return GLOBAL_DOMAIN_ID
        canonicals = set(_build_registry().name_to_canonical.values())
        if resolved not in canonicals and "-" not in resolved:
            return GLOBAL_DOMAIN_ID
        return NodeIdFactory.domain_id(resolved)

    # ── Node constructors ─────────────────────────────────────────────

    def _ensure_domain(self, domain_id, label=None):
        if domain_id not in self._nodes:
            node = WorkflowNode(
                id=domain_id,
                kind=NodeKind.DOMAIN,
                label=label or domain_id.replace("domain:", ""),
                color=DOMAIN_COLOR,
                domain_id=domain_id,
                size=5.0,
            )
            self._nodes[domain_id] = node
            self._node_order.append(node)
        return domain_id

    def _build_tool_hubs(self, domain_id, active_tools):
        created = []
        for tool in active_tools:
            hub_id = NodeIdFactory.tool_hub_id(domain_id, tool)
            if hub_id in self._nodes:
                continue
            node = WorkflowNode(
                id=hub_id,
                kind=NodeKind.TOOL_HUB,
                label=tool.value,
                color=TOOL_HUB_COLORS[tool],
                domain_id=domain_id,
                size=2.5,
                tool=tool,
            )
            self._nodes[hub_id] = node
            self._node_order.append(node)
            self._edges.append(self._in_domain(hub_id, domain_id))
            created.append(node)
        return created

    @staticmethod
    def _in_domain(source, domain_id):
        return WorkflowEdge(source=source, target=domain_id, kind=EdgeKind.IN_DOMAIN)

    def _add_child(self, node_id, kind, label, color, domain_id, size, **extra):
        """Idempotent non-domain node + in_domain edge. Returns True if new."""
        if node_id in self._nodes:
            return False
        node = WorkflowNode(
            id=node_id,
            kind=kind,
            label=label,
            color=color,
            domain_id=domain_id,
            size=size,
            **extra,
        )
        self._nodes[node_id] = node
        self._node_order.append(node)
        self._edges.append(self._in_domain(node_id, domain_id))
        return True

    # ── Ingest ────────────────────────────────────────────────────────
    # Per-source node-ingest bodies live in
    # ``workflow_graph_builder_ingest``; these methods delegate so the
    # streaming-build dispatch table (``self._ingest_*``) is unchanged.

    def _ingest_tool_event(self, ev):
        return _ingest_tool_event(self, ev)

    def _track_file_timestamp(self, path, tool, ev):
        return _track_file_timestamp(self, path, tool, ev)

    def _finalize_files(self):
        return _finalize_files(self)

    def _ingest_memory(self, mem):
        return _ingest_memory(self, mem)

    def _ingest_discussion(self, dc):
        return _ingest_discussion(self, dc)

    def _ingest_skill(self, sk):
        return _ingest_skill(self, sk)

    def _ingest_hook(self, hk):
        return _ingest_hook(self, hk)

    def _ingest_agent(self, ag):
        return _ingest_agent(self, ag)

    def _ingest_command(self, cm):
        return _ingest_command(self, cm)

    # ── al-muqabala: dedupe by (src, tgt, kind); sum weights ──────────

    def _dedupe_and_link(
        self, nodes: Iterable[WorkflowNode], edges: Iterable[WorkflowEdge]
    ):
        node_list = list(nodes)
        seen: dict[tuple[str, str, str], WorkflowEdge] = {}
        for e in edges:
            kv = e.kind.value if hasattr(e.kind, "value") else str(e.kind)
            key = (e.source, e.target, kv)
            if key in seen:
                seen[key] = seen[key].model_copy(
                    update={"weight": seen[key].weight + e.weight}
                )
                continue
            seen[key] = e
        return node_list, list(seen.values())


__all__ = ["WorkflowGraphBuilder"]
