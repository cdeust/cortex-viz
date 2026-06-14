"""Wiki coverage audit — composition root + file-level coverage.

Pure business logic — no I/O beyond filesystem reads. The handler
composes this with the wiki filesystem scan.

This module was 1396 lines; it is now split into:
  * ``wiki_coverage_scope_type``     — ``Scope`` dataclass + constants
  * ``wiki_coverage_scopes(_part*)`` — the canonical ``SCOPES`` table
  * ``wiki_coverage_score``          — anchor-page scope scoring + audit
  * ``wiki_coverage`` (this file)    — public re-export shim + file-level
                                       coverage axis

Problem this module solves
==========================

The auto-curator clusters memories bottom-up: it surfaces topics the
user *worked on* but can't see what the user *didn't write yet*. This
module is the top-down counterpart: given a project (domain), it checks
whether each canonical *scope* is documented (scope axis, in
``wiki_coverage_score``) and whether every source file is referenced
somewhere in the wiki (file axis, below).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Final

# ── Public re-export shim ───────────────────────────────────────────────
# Preserve every historical import path
# (``from cortex_viz.core.wiki_coverage import audit_domain`` etc.).
from cortex_viz.core.wiki_coverage_score import (  # noqa: F401
    _DEFAULT_MAX_AGE_DAYS,
    _COVERAGE_THRESHOLDS,
    _DOMAIN_REJECT_RE,
    _KNOWN_KINDS,
    _count_substantive_pages,
    _has_substantive_anchor,
    _is_plausible_domain,
    _suggested_path_for,
    audit_all_domains,
    audit_domain,
    DomainCoverage,
    list_domains,
    ScopeCoverage,
)
from cortex_viz.core.wiki_coverage_scope_type import (  # noqa: F401
    _MIN_PAGE_BYTES,
    Scope,
)
from cortex_viz.core.wiki_coverage_scopes import SCOPES  # noqa: F401


# ── File-level coverage ────────────────────────────────────────────────
#
# Anchor-page coverage (above) ensures every project has the six
# structural scopes documented. File-level coverage is the second axis:
# every source file in the project must be referenced *somewhere* in
# the wiki. The reference can be inside an architecture page that lists
# the file, a services page that names it, a dedicated file-doc, or an
# ADR that touched it. Anything that isn't named anywhere is a hole.
#
# This is what "nothing should be left uncovered" means concretely:
# a reader following the wiki should never encounter a file in the
# repo that has no breadcrumb back to a wiki page.


# File extensions Cortex treats as source. Documentation files (.md),
# generated artifacts, lock files, and binaries are filtered out at
# scan time, not by extension — but extensions narrow the set first.
_SOURCE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".swift",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".cs",
        ".sql",
    }
)

# Directories never worth scanning — vendored deps, build artifacts,
# generated caches, IDE state.
_SKIP_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "env",
        "deps",
        "site-packages",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".turbo",
        "coverage",
        ".cache",
        ".tox",
        ".eggs",
        ".gradle",
        ".idea",
        ".vscode",
    }
)


def _project_source_root(domain: str) -> str | None:
    """Resolve a domain name to its filesystem source root.

    Returns ``None`` when the domain isn't tied to a git repo (e.g.
    the ``_general`` catch-all bucket, or a domain that exists only as
    a memory tag without a checked-out tree).
    """
    try:
        from cortex_viz.shared.domain_mapping import _build_registry
    except Exception:
        return None
    registry = _build_registry()
    for repo in registry.repos:
        if repo.canonical == domain:
            return repo.fs_path
    return None


def list_source_files(root: str) -> list[str]:
    """Walk ``root`` and return wiki-relative paths of source files.

    Returns paths *relative to ``root``* — that's what the wiki page
    bodies typically cite (e.g. ``mcp_server/core/predictive_coding.py``).
    Filters out vendored deps, build artefacts, and non-source
    extensions. Returns an empty list when ``root`` doesn't exist.
    """
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # In-place filter so os.walk doesn't descend into skip dirs.
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRECTORIES and not d.startswith(".")
        ]
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext not in _SOURCE_EXTENSIONS:
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            out.append(rel)
    return out


def _index_wiki_file_references(
    wiki_root: str, domain: str
) -> tuple[set[str], set[str]]:
    """Index file paths and basenames mentioned anywhere in the wiki.

    Returns ``(rel_paths_referenced, basenames_referenced)`` where:

      * ``rel_paths_referenced`` — full relative paths cited verbatim
        (``mcp_server/core/predictive_coding.py``). High-precision match.
      * ``basenames_referenced`` — bare filenames cited (``predictive_coding.py``).
        Lower-precision but catches pages that name a file without its
        full directory prefix.

    Scans every ``.md`` page across all kinds; not domain-scoped because
    a domain's services may be referenced from cross-cutting pages. The
    domain argument is for future scoping if false-positive cross-domain
    matches become a problem.
    """
    paths: set[str] = set()
    basenames: set[str] = set()
    if not os.path.isdir(wiki_root):
        return paths, basenames

    # File-path-shaped tokens: at least one slash, has a source extension,
    # ends at whitespace or punctuation.
    path_re = re.compile(
        r"[\w./\-]+\.(?:py|ts|tsx|js|jsx|go|rs|rb|java|kt|swift|cpp|cc|c|h|hpp|cs|sql)\b"
    )
    _ = domain  # reserved for future scoping

    for dirpath, dirnames, filenames in os.walk(wiki_root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and not d.startswith("_")
        ]
        for f in filenames:
            if not f.endswith(".md"):
                continue
            full = os.path.join(dirpath, f)
            try:
                with open(full, encoding="utf-8", errors="ignore") as fp:
                    text = fp.read()
            except OSError:
                continue
            for m in path_re.finditer(text):
                token = m.group(0).lstrip("./").strip()
                if "/" in token:
                    paths.add(token)
                basenames.add(os.path.basename(token))
    return paths, basenames


@dataclass
class FileCoverage:
    """File-level coverage roll-up for one domain."""

    domain: str
    source_root: str | None
    source_file_count: int
    covered_file_count: int  # matched by path or basename
    uncovered_files: list[str] = field(default_factory=list)

    @property
    def coverage_ratio(self) -> float:
        if not self.source_file_count:
            return 1.0
        return self.covered_file_count / self.source_file_count


def audit_files(wiki_root: str, domain: str) -> FileCoverage:
    """Compute file-level coverage for one domain.

    A file is *covered* when its relative path OR its basename appears
    in the body of any wiki page. Returns the uncovered list capped at
    50 entries so a wide-open project doesn't balloon the return.
    """
    src_root = _project_source_root(domain)
    if src_root is None:
        return FileCoverage(
            domain=domain,
            source_root=None,
            source_file_count=0,
            covered_file_count=0,
        )

    files = list_source_files(src_root)
    if not files:
        return FileCoverage(
            domain=domain,
            source_root=src_root,
            source_file_count=0,
            covered_file_count=0,
        )

    paths_ref, basenames_ref = _index_wiki_file_references(wiki_root, domain)
    uncovered: list[str] = []
    covered = 0
    for rel in files:
        bn = os.path.basename(rel)
        if rel in paths_ref or bn in basenames_ref:
            covered += 1
        else:
            uncovered.append(rel)

    return FileCoverage(
        domain=domain,
        source_root=src_root,
        source_file_count=len(files),
        covered_file_count=covered,
        uncovered_files=uncovered[:50],
    )


def audit_all_file_coverage(wiki_root: str) -> list[FileCoverage]:
    """Audit file-level coverage for every discovered domain that has a
    resolvable source root. Sorted by uncovered count desc.
    """
    out: list[FileCoverage] = []
    for domain in list_domains(wiki_root):
        roll = audit_files(wiki_root, domain)
        if roll.source_root is not None:
            out.append(roll)
    out.sort(key=lambda r: r.source_file_count - r.covered_file_count, reverse=True)
    return out
