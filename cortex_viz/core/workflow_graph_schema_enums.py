"""Enum vocabulary for the workflow graph — factored out so the palette
module (``workflow_graph_palette``) can key dicts by these types
without pulling the pydantic model definitions in.

Pure stdlib. No imports from the rest of the workflow-graph stack.
"""

from __future__ import annotations

from enum import Enum


class NodeKind(str, Enum):
    DOMAIN = "domain"
    SKILL = "skill"
    COMMAND = "command"
    HOOK = "hook"
    AGENT = "agent"
    TOOL_HUB = "tool_hub"
    FILE = "file"
    MEMORY = "memory"
    DISCUSSION = "discussion"
    # ENTITY — projects a knowledge-graph entity (entities table row)
    # into the workflow graph. Produced by
    # ``workflow_graph_source_pg.load_entities`` and ingested by
    # ``WorkflowGraphBuilder._ingest_entity``. Linked to memories via
    # the ``about_entity`` edge. Colour comes from the legacy palette
    # (ENTITY_COLORS) matched on ``entityType``.
    ENTITY = "entity"
    MCP = "mcp"
    # SYMBOL — function / class / module / import extracted from the
    # AST by the ``automatised-pipeline`` sibling plugin (ADR-0046).
    # ``symbol_type`` on the node body carries the sub-kind.
    SYMBOL = "symbol"
    # WIKI — a wiki.pages row (Cortex's durable documentation surface,
    # see ``infrastructure.wiki_pg``). Produced by
    # ``workflow_graph_source_pg.load_wiki_pages`` and ingested by
    # ``core.workflow_graph_wiki.ingest_wiki_page``. Single-domain (NOT
    # in ``_MULTI_DOMAIN_KINDS`` below) — one page belongs to one
    # domain, unlike a FILE which can be touched cross-project.
    WIKI = "wiki"


class EdgeKind(str, Enum):
    IN_DOMAIN = "in_domain"
    TOOL_USED_FILE = "tool_used_file"
    # Bash hub → command node containment edge. Distinct from
    # TOOL_USED_FILE so that the panel's "Files touched" counter does
    # not mistakenly include commands.
    COMMAND_IN_HUB = "command_in_hub"
    INVOKED_SKILL = "invoked_skill"
    TRIGGERED_HOOK = "triggered_hook"
    SPAWNED_AGENT = "spawned_agent"
    # ABOUT_ENTITY — MEMORY → ENTITY link. Produced by
    # ``WorkflowGraphBuilder._ingest_memory_entity_edge`` which reads
    # the ``memory_entities`` join table. Styled in
    # ``ui/unified/workflow_graph.css`` (``.wfg-link--about_entity``).
    ABOUT_ENTITY = "about_entity"
    # ASSOCIATES_WITH — MEMORY → MEMORY link (v1 "brain associations").
    # Produced by ``infrastructure.memory_associations.
    # load_co_entity_associations`` (co-entity TF-IDF weighting over the
    # ``memory_entities`` join table) and ingested by
    # ``core.workflow_graph_association.ingest_association``. Undirected
    # by construction (source < target); the smaller pg id is always
    # ``source``.
    ASSOCIATES_WITH = "associates_with"
    # SUPERSEDES — MEMORY → MEMORY versioning edge, DIRECTIONAL (the
    # newer memory points at the older fact it replaces). Produced by
    # ``infrastructure.memory_supersede.load_supersede_edges`` (reads
    # the recorded ``memories.supersedes_id`` column — Cortex's
    # supersede write path owns the lineage, cortex-viz never
    # re-derives it) and ingested by
    # ``core.workflow_graph_supersede.ingest_supersede``. Distinct from
    # ASSOCIATES_WITH: an association is undirected co-evidence, a
    # supersession is a directed replacement — conflating them would
    # feed versioning lineage into the community/layout substrate.
    SUPERSEDES = "supersedes"
    DISCUSSION_TOUCHED_FILE = "discussion_touched_file"
    DISCUSSION_USED_TOOL = "discussion_used_tool"
    DISCUSSION_SPAWNED_AGENT = "discussion_spawned_agent"
    DISCUSSION_RAN_COMMAND = "discussion_ran_command"
    COMMAND_TOUCHED_FILE = "command_touched_file"
    INVOKED_MCP = "invoked_mcp"
    # AST edges produced by the ``automatised-pipeline`` bridge
    # (ADR-0046). All source-symbols must resolve to a SYMBOL node;
    # targets resolve to a SYMBOL (CALLS, MEMBER_OF) or a FILE
    # (DEFINED_IN) or a SYMBOL import (IMPORTS).
    DEFINED_IN = "defined_in"  # symbol → file
    CALLS = "calls"  # caller symbol → callee symbol
    IMPORTS = "imports"  # file → imported symbol or file
    MEMBER_OF = "member_of"  # function → class / class → module
    # WIKI_LINKS — wiki → wiki page-to-page link (``wiki.links`` row).
    # Produced by ``infrastructure.wiki_graph.load_wiki_links`` and
    # ingested by ``core.workflow_graph_wiki.ingest_wiki_link``.
    WIKI_LINKS = "wiki_links"
    # DOCUMENTS — wiki → memory link: the page documents/cites a
    # memory. Union of ``wiki.pages.memory_id`` (the page's anchor
    # memory) and ``wiki.citations`` (memories cited while writing the
    # page). Produced by ``infrastructure.wiki_graph.
    # load_wiki_memory_links`` and ingested by
    # ``core.workflow_graph_wiki.ingest_wiki_memory``.
    DOCUMENTS = "documents"
    # WIKI_SOURCE — wiki → file link: the page documents/references/
    # derives-from a source file (``wiki.page_sources`` row, ADR-0051).
    # ``label`` carries the row's ``link_kind`` ('documents' / 'references'
    # / 'derived') and ``confidence`` its recorded confidence, so the
    # frontend can style the three provenance kinds distinctly without a
    # separate EdgeKind per kind. Produced by ``infrastructure.wiki_graph.
    # load_wiki_page_sources`` and resolved at build finalisation by
    # ``server.graph_build_wiki_source.resolve_wiki_source_over_cache`` —
    # AFTER the L6 AST sweep, so the FILE endpoint set is complete (VOLET ①,
    # mem 4262203). Endpoint resolution
    # (``core.wiki_source_resolve.resolve_file_node_id``) is best-effort —
    # a row whose ``source_path`` doesn't resolve to a live FILE node is
    # silently skipped (no fabricated node), same contract as WIKI_LINKS.
    WIKI_SOURCE = "wiki_source"
    # CITED_IN — wiki -> discussion link: the page was cited while a
    # Claude Code session was in progress (``wiki.citations.session_id``,
    # already used for ``DOCUMENTS`` via the ``memory_id`` column on the
    # same table — this edge projects the sibling ``session_id`` column,
    # never previously projected). Produced by
    # ``infrastructure.wiki_graph.load_wiki_session_links`` and ingested
    # by ``core.workflow_graph_wiki.ingest_wiki_citation``. Cheapest
    # documents->actions/decisions bridge available: it does not depend
    # on ``wiki_source_resolve``'s ~5% file-resolution ceiling (mem
    # 4262064/4262320) because its target is a DISCUSSION node keyed on
    # session id, not a FILE node keyed on a resolved path.
    CITED_IN = "cited_in"


class ToolKind(str, Enum):
    EDIT = "Edit"
    READ = "Read"
    GREP = "Grep"
    BASH = "Bash"
    GLOB = "Glob"
    WRITE = "Write"
    TASK = "Task"


class PrimaryToolCluster(str, Enum):
    EDIT_WRITE = "edit_write"
    READ = "read"
    GREP_GLOB = "grep_glob"
    BASH = "bash"
