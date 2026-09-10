"""The directories the wiki view reads pages from.

Cortex publishes wiki pages into two kinds of root, and the view has to
read both or it shows a partial wiki:

* the **global root** (``~/.claude/methodology/wiki``, ``config.WIKI_ROOT``),
  written by the default mode of ``wiki_write`` / ``wiki_adr`` and indexed
  in the ``wiki.*`` PostgreSQL schema;
* one **project root per repository**, ``<repo>/wiki/``, written by the
  ``project_root`` mode of the same tools. That mode is filesystem-only by
  contract (Cortex ADR-0056 / ADR-0593, ``memory_sync: project-files-only``):
  the page, ``wiki/manifest.json`` and the ``docs/adr`` mirror are the whole
  publication. Nothing lands in PostgreSQL and nothing lands under the global
  root, so a reader keyed on either never sees those pages. The Cortex ADRs
  1060 to 1062 (2026-09-09) were invisible in the Wiki view for exactly that
  reason.

A project root is recognised by its manifest: ``<repo>/wiki/manifest.json``
with ``version: 1`` and a non-empty ``project`` name (the same validation
Cortex's ``infrastructure.project_wiki.load_manifest`` applies). The
candidate repositories are the git checkouts the domain registry
(``shared.domain_mapping``) already discovers for the coverage audit, so
there is one discovery convention, not two.

Page paths from a project root are namespaced as ``@<project>/<rel_path>``
so one ``path`` query parameter still addresses every page; ``locate``
turns that back into ``(directory, rel_path)`` for the readers. Pure
filesystem reads, no database.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Leading character of a project-wiki page path. Chosen because it cannot
# start a path inside the global root (Cortex's ``wiki_layout.slugify``
# emits ``[a-z0-9-]`` names and every kind directory is alphabetic), so a
# global page and a project page can never collide.
PROJECT_PREFIX = "@"

# Cortex project-mode manifest: ``<repo>/wiki/manifest.json``.
# source: Cortex ``mcp_server.infrastructure.project_wiki.load_manifest``.
_MANIFEST_RELATIVE = os.path.join("wiki", "manifest.json")
_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class WikiRoot:
    """One directory of wiki pages.

    ``name`` is ``""`` for the global root and the manifest's ``project``
    for a project root; ``directory`` is where the ``.md`` pages live.
    """

    name: str
    directory: Path

    @property
    def prefix(self) -> str:
        """The path prefix every page of this root carries in the API."""
        return f"{PROJECT_PREFIX}{self.name}/" if self.name else ""


def candidate_repo_paths() -> list[str]:
    """Every git checkout the domain registry knows, in discovery order."""
    try:
        from cortex_viz.shared.domain_mapping import _build_registry
    except Exception:
        return []
    return [repo.fs_path for repo in _build_registry().repos]


def _manifest_project(repo_path: str) -> str | None:
    """The ``project`` name of a valid project-wiki manifest, else ``None``."""
    manifest_path = os.path.join(repo_path, _MANIFEST_RELATIVE)
    try:
        with open(manifest_path, encoding="utf-8") as fp:
            manifest = json.load(fp)
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("version") != _MANIFEST_VERSION:
        return None
    project = manifest.get("project")
    if not isinstance(project, str):
        return None
    project = project.strip()
    if not project or "/" in project or os.sep in project:
        return None
    return project


def project_roots() -> list[WikiRoot]:
    """The project wiki roots discovered on this machine.

    Read on every call (no cache) so a wiki published into a repository
    after the server started shows up on the next request. Two checkouts
    declaring the same project name would be indistinguishable in a page
    path, so the first one in registry order wins and the other is
    skipped.
    """
    roots: list[WikiRoot] = []
    seen: set[str] = set()
    for repo_path in candidate_repo_paths():
        project = _manifest_project(repo_path)
        if project is None or project in seen:
            continue
        seen.add(project)
        roots.append(WikiRoot(project, Path(repo_path) / "wiki"))
    return roots


def locate(rel_path: str, global_root: Path) -> tuple[WikiRoot, str] | None:
    """Split an API page path into ``(root, path inside that root)``.

    An unprefixed path belongs to ``global_root``. A ``@<project>/...`` path
    belongs to that project's root; an unknown project, or a prefix with
    nothing after it, yields ``None`` so the caller refuses the path the
    same way it refuses a traversal.
    """
    if not rel_path.startswith(PROJECT_PREFIX):
        return WikiRoot("", global_root), rel_path
    name, _, rest = rel_path[len(PROJECT_PREFIX) :].partition("/")
    if not name or not rest:
        return None
    for root in project_roots():
        if root.name == name:
            return root, rest
    return None


__all__ = [
    "PROJECT_PREFIX",
    "WikiRoot",
    "candidate_repo_paths",
    "locate",
    "project_roots",
]
