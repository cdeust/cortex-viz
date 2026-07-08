"""Resolves a ``wiki.page_sources.source_path`` to the deterministic FILE
node id the workflow graph would have minted for the same file (ADR-0051
downstream consumer — see Cortex's ``pg_schema.py`` comment on
``wiki.page_sources``: "Downstream consumer: cortex-viz wiki-page ->
source-file edges").

Problem this module solves
==========================
``source_path`` is POSIX, project-source-root-relative (Cortex's
``mcp_server.shared.wiki_source_paths.normalize_source_path`` convention —
no leading ``./`` or ``/``). FILE nodes in the workflow graph are keyed by
``NodeIdFactory.file_id(abs_path)``, a hash of the ABSOLUTE path recorded
by a Claude tool-use event (``ev["file_path"]`` in
``workflow_graph_builder_ingest._ingest_tool_event``). Joining the two
requires reconstructing that absolute path: ``<project source root>/
<source_path>``.

The project's source root is resolved via ``wiki_coverage.
_project_source_root``, the SAME git-repo-discovery registry
(``shared.domain_mapping``) that function already uses for the file-level
wiki-coverage audit — reusing the existing convention rather than
inventing a second one.

Best-effort by construction: this is a STRING match (reconstructed
absolute path -> hash -> dict lookup), not a filesystem walk. If the
tool-use event that created the FILE node recorded a differently-shaped
absolute path (a symlink, a different casing, a stray trailing slash) the
reconstructed path won't hash-equal it and the row is silently skipped —
correct per the "no fabricated node" contract (a false negative is
safe; a fabricated edge is not), but an honest precision limitation
worth flagging rather than hiding. See docstring on
``resolve_file_node_id``.

Pure core logic plus the same accepted filesystem-read exception
``wiki_coverage.py`` already documents for ``_project_source_root``
(git-repo discovery) — no network I/O, no database I/O.
"""

from __future__ import annotations

import posixpath

from cortex_viz.core.wiki_coverage import _project_source_root
from cortex_viz.core.workflow_graph_schema import NodeIdFactory

_DOMAIN_PREFIX = "domain:"


def resolve_file_node_id(domain_id: str | None, source_path: str | None) -> str | None:
    """Reconstruct the FILE node id a wiki-page's ``source_path`` should
    resolve to, given the WIKI node's own ``domain_id``.

    Args:
        domain_id: the wiki page's node ``domain_id`` (e.g.
            ``"domain:cortex"``), as already assigned by
            ``WorkflowGraphBuilder._assign_domain`` when the WIKI node was
            ingested. ``None`` or the global-domain sentinel (doesn't
            start with ``"domain:"``... it does, but resolves to no repo)
            yields ``None``.
        source_path: POSIX, source-root-relative path from a
            ``wiki.page_sources`` row.

    Returns:
        The FILE node id (``NodeIdFactory.file_id(abs_path)``) the graph
        WOULD use for that file, or ``None`` when either the domain has
        no known filesystem source root (``_project_source_root`` misses
        — e.g. a memory-only domain with no checked-out repo) or
        ``source_path`` is blank. Callers MUST still check the returned
        id is actually present in the graph's node set before drawing an
        edge — this function only computes the candidate id, it never
        confirms the FILE node exists (that is the ingest-time
        skip-missing-endpoint check, matching ``ingest_wiki_link`` /
        ``ingest_wiki_memory``).
    """
    if not domain_id or not domain_id.startswith(_DOMAIN_PREFIX):
        return None
    if not source_path:
        return None
    canonical = domain_id[len(_DOMAIN_PREFIX) :]
    root = _project_source_root(canonical)
    if not root:
        return None
    abs_path = posixpath.join(root.rstrip("/"), source_path)
    return NodeIdFactory.file_id(abs_path)


__all__ = ["resolve_file_node_id"]
