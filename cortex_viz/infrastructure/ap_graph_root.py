"""Resolve the absolute source root an automatised-pipeline (AP) graph was
indexed from.

Why this exists
===============
AP emits repo-RELATIVE file paths in its graph (``cortex_viz/core/x.py``, not
``/Users/.../cortex-viz/cortex_viz/core/x.py``) — deliberately, so the graph
structure stays portable across machines. But the rest of the workflow graph
keys FILE nodes by the ABSOLUTE path: tool-event file nodes use the absolute
``ev["file_path"]`` a Claude tool recorded, and ``wiki_source_resolve.
resolve_file_node_id`` reconstructs ``<repo root>/<source_path>``. For the L6
AST FILE nodes to share that one keying scheme — so a wiki page can link to an
AST-indexed file, and a tool-touched file and its AST symbols collapse onto one
node — cortex-viz must turn AP's relative paths back into absolute ones, which
needs the root AP indexed.

Two sources, in precedence order
================================
1. **Sidecar** ``<graph_dir>/meta.json`` — ``{"root": "<abs source path>"}``,
   written by AP next to the graph at index time (see automatised-pipeline
   ``do_index_codebase`` / ``do_analyze_codebase``). Canonical and exact: it is
   the very path AP relativised against, so ``join(root, ap_relative)`` is
   byte-identical to the file AP walked. Present only for graphs indexed by an
   AP build that writes it.
2. **Registry fallback** — the git-repo registry (``shared.domain_mapping``)
   matched by graph-directory basename == repo directory basename. Transitional
   cover for graphs indexed before the sidecar existed, so the wiki->file join
   works without waiting for a full re-index.

Returns ``None`` when neither resolves — callers then keep the relative path
(the pre-fix behaviour: no regression, just no wiki->file join for that graph).

Pure infrastructure: filesystem reads + the same git-repo registry the
wiki-coverage audit already uses. No database, no network.
"""

from __future__ import annotations

import json
import os

_META_FILENAME = "meta.json"


def _root_from_sidecar(graph_path: str) -> str | None:
    """Read ``<graph_dir>/meta.json`` written by AP at index time.

    ``graph_path`` is the LadybugDB ``graph`` file (or dir); the sidecar sits
    next to it in the same output directory. Best-effort: a missing / malformed
    sidecar, or a ``root`` that no longer exists on disk, yields ``None``.
    """
    try:
        graph_dir = os.path.dirname(graph_path.rstrip("/"))
        meta_path = os.path.join(graph_dir, _META_FILENAME)
        if not os.path.isfile(meta_path):
            return None
        with open(meta_path, encoding="utf-8") as fp:
            meta = json.load(fp)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    root = meta.get("root")
    if not isinstance(root, str) or not root:
        return None
    # Only trust a root that actually exists — a stale sidecar (repo moved)
    # must fall through to the registry rather than mint dead file ids.
    return root if os.path.isdir(root) else None


def _root_from_registry(proj_name: str) -> str | None:
    """Match a graph-directory basename to a checked-out repo root.

    The graph dir basename (``cortex-viz`` in
    ``~/.cache/cortex/code-graphs/cortex-viz/graph``) equals the repo directory
    basename for graphs indexed from a real checkout. Match on that; distinct
    repos have distinct basenames even inside a canonical *family* (``cortex``
    collapses ``cortex-viz`` / ``cortex-voice`` / ... which keep their own dir
    names), so this stays repo-exact. Noise graphs (worktree hashes, bench
    fixtures) simply don't match and return ``None``.
    """
    if not proj_name:
        return None
    try:
        from cortex_viz.shared.domain_mapping import _build_registry
    except Exception:
        return None
    try:
        repos = _build_registry().repos
    except Exception:
        return None
    for repo in repos:
        fs_path = getattr(repo, "fs_path", "") or ""
        if fs_path and os.path.basename(fs_path.rstrip("/")) == proj_name:
            return fs_path
    return None


def graph_source_root(graph_path: str, proj_name: str) -> str | None:
    """Absolute source root for an AP graph, or ``None`` if unresolvable.

    Args:
        graph_path: the AP LadybugDB graph path (``.../<name>/graph``).
        proj_name: the graph's project name (its directory basename), as the
            L6 sweep already derives it.

    Returns:
        The absolute directory AP indexed, sidecar-first then registry, or
        ``None`` when neither source resolves it.
    """
    return _root_from_sidecar(graph_path) or _root_from_registry(proj_name)


def absolutize(root: str | None, rel_path: str) -> str:
    """Join an AP-relative path onto ``root`` (POSIX), or return it unchanged
    when ``root`` is ``None``.

    Keeping the relative path on a missing root is the deliberate no-regression
    fallback: the FILE node is then keyed exactly as it was before this fix.
    """
    if not root:
        return rel_path
    return os.path.join(root.rstrip("/"), rel_path)


__all__ = ["graph_source_root", "absolutize"]
