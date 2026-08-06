"""Wiki-scoped documentation graph — the wiki view's graph mode.

``GET /api/wiki/graph`` answers "how does this documentation hang together?",
where the galaxy graph answers "what happened in this project?". Both speak
``workflow_graph.v1`` and both are assembled by ``WorkflowGraphBuilder`` through
the SAME ingesters (``core.workflow_graph_wiki``), so a page renders with one
identity, colour and node id in either lens. That reuse is the point: a second,
parallel wiki-node projection would drift from the first.

What the two client toggles mean (``ui/unified/js/wiki.js`` sends them):

  * ``cross-lens`` (default ON) — include the MEMORY nodes the pages were
    written from, plus their ``documents`` edges. This is the lens crossing:
    documentation on one side, the memory it distils on the other.
  * ``co-occurrence`` (default OFF) — add page↔page ``associates_with`` edges
    for pages that share a tag. Off by default, and deliberately: a shared tag
    is weak evidence, and ``ui/dashboard/DESIGN.md`` already classes
    co-occurrence as "a weak relation is not itself a datum".

``wiki_links`` edges (a real authored link between two pages) are always
included — they are the documentation graph proper, not a toggle.

Pure: every input is passed in already loaded, and the output is deterministic
for a given input (see ``build_doc_graph``'s ordering contract).
"""

from __future__ import annotations

from typing import Any

from cortex_viz.core.workflow_graph_builder import WorkflowGraphBuilder
from cortex_viz.core.workflow_graph_builder_ingest import _ingest_memory
from cortex_viz.core.workflow_graph_schema import NodeIdFactory
from cortex_viz.core.workflow_graph_schema_enums import EdgeKind
from cortex_viz.core.workflow_graph_wiki import (
    ingest_wiki_link,
    ingest_wiki_memory,
    ingest_wiki_page,
)

SCHEMA = "workflow_graph.v1"


def _selected_pages(pages: list[dict], domain: str) -> list[dict]:
    """Pages in ``domain``, oldest id first. An empty domain selects all.

    Sorted by id so the node order — and therefore the payload — does not
    depend on the row order the database happened to return.
    """
    wanted = (domain or "").strip()
    chosen = [
        page
        for page in pages
        if page.get("id") is not None
        and (not wanted or str(page.get("domain") or "") == wanted)
    ]
    return sorted(chosen, key=lambda page: int(page["id"]))


def _tag_pairs(pages: list[dict], tags_by_path: dict[str, list[str]]) -> list[tuple]:
    """Page-id pairs sharing at least one tag, each pair once, sorted.

    ``tags_by_path`` is keyed by ``rel_path`` because tags live in a page's
    markdown frontmatter (``infrastructure.wiki_read``) while identity lives in
    ``wiki.pages`` — the two are joined on the path they agree about.
    """
    tagged: list[tuple[int, frozenset[str]]] = []
    for page in pages:
        # A page with no rel_path has nothing to join on. Falling back to "" and
        # looking that up would make every such page match an ""-keyed entry and
        # co-occur with each other for no reason.
        rel_path = str(page.get("rel_path") or "")
        if not rel_path:
            continue
        tags = tags_by_path.get(rel_path) or []
        if tags:
            tagged.append((int(page["id"]), frozenset(str(t) for t in tags)))
    pairs = []
    for index, (left_id, left_tags) in enumerate(tagged):
        for right_id, right_tags in tagged[index + 1 :]:
            shared = left_tags & right_tags
            if shared:
                pairs.append((left_id, right_id, sorted(shared)))
    return sorted(pairs)


def _add_co_occurrence(builder, pages: list[dict], tags_by_path: dict) -> None:
    """Add one undirected ``associates_with`` edge per tag-sharing page pair.

    Lower page id is always ``source``, matching ``EdgeKind.ASSOCIATES_WITH``'s
    documented orientation, so the same pair never yields two different edges.
    """
    for left_id, right_id, shared in _tag_pairs(pages, tags_by_path):
        builder._edges.append(  # noqa: SLF001 - the ingesters in this family
            _associates_edge(left_id, right_id, shared)  # all append directly
        )


def _associates_edge(left_id: int, right_id: int, shared: list[str]):
    from cortex_viz.core.workflow_graph_schema import WorkflowEdge

    return WorkflowEdge(
        source=NodeIdFactory.wiki_id(left_id),
        target=NodeIdFactory.wiki_id(right_id),
        kind=EdgeKind.ASSOCIATES_WITH,
        label=", ".join(shared[:3]),
        reason="wiki-shared-tag",
    )


def build_doc_graph(
    *,
    pages: list[dict],
    links: list[dict],
    memory_links: list[dict],
    memories: list[dict],
    tags_by_path: dict[str, list[str]],
    domain: str = "",
    cross_lens: bool = True,
    co_occurrence: bool = False,
) -> dict[str, Any]:
    """Build the wiki lens's ``workflow_graph.v1`` payload.

    Every argument is data, already loaded — this function performs no I/O, so
    the same inputs always produce the same output. Determinism is by
    construction, not by luck: pages are ingested in id order, and links,
    memory links and tag pairs are each sorted before ingestion, so the node and
    edge sequences are functions of the content alone.

    An empty ``pages`` yields a valid payload with zero nodes and
    ``meta.empty = True`` — the caller renders "this wiki has no pages", which
    is a different statement from "the graph could not be built".
    """
    builder = WorkflowGraphBuilder()
    selected = _selected_pages(pages, domain)
    for page in selected:
        ingest_wiki_page(builder, page)

    live_ids = {int(page["id"]) for page in selected}
    # No endpoint pre-filter: ``ingest_wiki_link`` already skips a link whose
    # source or target is not a node, so filtering here only duplicated that
    # rule in a second place where it could drift out of step with it. Sorting
    # is what this needs — for determinism, which the ingester cannot provide.
    for link in sorted(
        (
            link
            for link in links
            if link.get("src_page_id") is not None
            and link.get("dst_page_id") is not None
        ),
        key=lambda link: (int(link["src_page_id"]), int(link["dst_page_id"])),
    ):
        ingest_wiki_link(builder, link)

    if cross_lens:
        scoped_links = sorted(
            (row for row in memory_links if row.get("page_id") in live_ids),
            key=lambda row: (int(row["page_id"]), int(row["memory_id"])),
        )
        wanted = {int(row["memory_id"]) for row in scoped_links}
        for memory in sorted(
            (mem for mem in memories if mem.get("id") is not None),
            key=lambda mem: int(mem["id"]),
        ):
            if int(memory["id"]) in wanted:
                _ingest_memory(builder, memory)
        for row in scoped_links:
            ingest_wiki_memory(builder, row)

    if co_occurrence:
        _add_co_occurrence(builder, selected, tags_by_path)

    nodes = [node.model_dump() for node in builder._node_order]  # noqa: SLF001
    edges = [edge.model_dump() for edge in builder._edges]  # noqa: SLF001
    return {
        "nodes": nodes,
        "edges": edges,
        "clusters": [],
        "meta": {
            "schema": SCHEMA,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "lens": "wiki",
            "domain": (domain or "").strip(),
            "cross_lens": bool(cross_lens),
            "co_occurrence": bool(co_occurrence),
            "empty": not selected,
        },
    }


__all__ = ["SCHEMA", "build_doc_graph"]
