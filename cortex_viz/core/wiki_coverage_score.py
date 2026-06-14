"""Anchor-page coverage scoring for wiki documentation scopes.

Split out of ``wiki_coverage.py`` (was 1396 lines). Pure scoring +
filesystem scan for the *scope* axis: per-domain, which canonical scopes
have a substantive anchor page. The composition module
``wiki_coverage`` re-exports these symbols.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from cortex_viz.core.wiki_coverage_scope_type import (
    _DEFAULT_MAX_AGE_DAYS,
    _MIN_PAGE_BYTES,
    Scope,
)
from cortex_viz.core.wiki_coverage_scopes import SCOPES

@dataclass
class ScopeCoverage:
    """Whether a single scope is covered for a domain, and how."""

    scope: Scope
    domain: str
    covered: bool
    page_count: int  # substantive pages found in this scope's directories
    anchor_page: str | None  # wiki-relative path of the page that anchors coverage
    suggested_path: str  # path to author if uncovered


@dataclass
class DomainCoverage:
    """Roll-up of all scopes for one domain."""

    domain: str
    scopes: list[ScopeCoverage] = field(default_factory=list)

    @property
    def covered_count(self) -> int:
        return sum(1 for s in self.scopes if s.covered)

    @property
    def missing_count(self) -> int:
        return sum(1 for s in self.scopes if not s.covered)

    @property
    def coverage_ratio(self) -> float:
        return self.covered_count / len(self.scopes) if self.scopes else 0.0

    def missing_scopes(self) -> list[ScopeCoverage]:
        return [s for s in self.scopes if not s.covered]


# ── Filesystem scan ─────────────────────────────────────────────────────


def _has_substantive_anchor(
    wiki_root: str,
    directories: tuple[str, ...],
    domain: str,
    anchor_filenames: tuple[str, ...],
    max_age_days: float | None = None,
) -> str | None:
    """Return the wiki-relative path of the first substantive anchor page,
    or None if no anchor exists.

    A page is substantive when it exists and is at least ``_MIN_PAGE_BYTES``
    bytes. This guards against empty placeholders authored by the groomer
    or stub pages created by codebase_analyze.

    When ``max_age_days`` is set, an anchor page older than that window
    is treated as **stale** (returns None as if it didn't exist), so the
    auto-curator re-emits an authoring job. Existing pages get the same
    coverage discipline as missing ones — the wiki stays in sync with
    the codebase without a human in the loop.
    """
    import time

    for directory in directories:
        for filename in anchor_filenames:
            rel = f"{directory}/{domain}/{filename}"
            full = os.path.join(wiki_root, rel)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_size < _MIN_PAGE_BYTES:
                continue
            if max_age_days is not None:
                age_days = (time.time() - st.st_mtime) / 86400.0
                if age_days > max_age_days:
                    continue
            return rel
    return None


def _count_substantive_pages(
    wiki_root: str,
    directories: tuple[str, ...],
    domain: str,
) -> int:
    """Count substantive ``.md`` pages under ``<wiki>/<dir>/<domain>/`` for
    each directory in ``directories``.

    Used to detect scopes that are "covered by accumulation" — many ADRs
    cover the ``decisions`` scope even without an anchor file.
    """
    count = 0
    for directory in directories:
        dom_path = os.path.join(wiki_root, directory, domain)
        if not os.path.isdir(dom_path):
            continue
        for entry in os.listdir(dom_path):
            if not entry.endswith(".md"):
                continue
            full = os.path.join(dom_path, entry)
            try:
                if os.path.getsize(full) >= _MIN_PAGE_BYTES:
                    count += 1
            except OSError:
                continue
    return count


def _suggested_path_for(scope: Scope, domain: str) -> str:
    """Where the LLM should write the missing scope's anchor page."""
    primary_dir = scope.directories[0] if scope.directories else "reference"
    if scope.anchor_filenames:
        filename = scope.anchor_filenames[0]
    else:
        filename = f"{scope.name}.md"
    return f"{primary_dir}/{domain}/{filename}"


_COVERAGE_THRESHOLDS: Final[dict[str, int]] = {
    # decisions scope is covered when any substantive ADR exists.
    "decisions": 1,
}


def audit_domain(
    wiki_root: str,
    domain: str,
    *,
    max_age_days: float | None = _DEFAULT_MAX_AGE_DAYS,
) -> DomainCoverage:
    """Compute coverage for one domain across all canonical scopes.

    Returns a ``DomainCoverage`` whose ``scopes`` list mirrors ``SCOPES``
    order, with ``covered=True`` for scopes that meet the coverage bar.

    Coverage rules:
      * If the scope has anchor filenames, a substantive anchor page
        counts as coverage. ``services`` and ``api`` are pre-eminent
        anchor-based scopes.
      * If the scope has no anchor filenames (``decisions``), any
        substantive page in its directories counts after the minimum
        page count is met (default 1).
      * If ``max_age_days`` is set (default 90), anchor pages older than
        the window count as missing so the auto-curator refreshes them.

    Pass ``max_age_days=None`` to disable freshness checks — the older
    "any anchor counts" semantics.
    """
    out = DomainCoverage(domain=domain)
    for scope in SCOPES:
        anchor = _has_substantive_anchor(
            wiki_root,
            scope.directories,
            domain,
            scope.anchor_filenames,
            max_age_days=max_age_days,
        )
        page_count = _count_substantive_pages(wiki_root, scope.directories, domain)
        threshold = _COVERAGE_THRESHOLDS.get(scope.name, 1)
        covered = anchor is not None or (
            not scope.anchor_filenames and page_count >= threshold
        )
        out.scopes.append(
            ScopeCoverage(
                scope=scope,
                domain=domain,
                covered=covered,
                page_count=page_count,
                anchor_page=anchor,
                suggested_path=_suggested_path_for(scope, domain),
            )
        )
    return out


_DOMAIN_REJECT_RE = (
    # Bare year buckets (notes/2026/*.md) — these are time buckets, not projects.
    "year",
)


def _is_plausible_domain(name: str) -> bool:
    """Filter for ``list_domains`` — accept project names, reject buckets.

    Rejected:
      * Bare years (``2026``) — time buckets dropped into the wiki by
        slug normalisation, not real projects.
      * Names starting with ``.`` or ``_`` — reserved (``_general`` is
        an exception covered downstream).
    """
    if not name or name.startswith((".", "_")):
        return False
    if name.isdigit() and len(name) == 4:  # bare year
        return False
    return True


_KNOWN_KINDS: Final[frozenset[str]] = frozenset(
    {
        "reference",
        "explanation",
        "adr",
        "adrs",
        "runbook",
        "specs",
        "notes",
        "guides",
        "conventions",
        "lessons",
        "rfc",
        "how-to",
        "tutorial",
        "files",
        "architecture",
    }
)


def list_domains(wiki_root: str) -> list[str]:
    """Discover domains by scanning ``<wiki>/<kind>/<domain>/`` subdirs.

    A directory is considered a domain when at least two known wiki
    kinds contain it as a subdirectory. Reserved buckets (``_general``,
    bare years) are filtered.
    """
    if not os.path.isdir(wiki_root):
        return []
    counts: dict[str, int] = {}
    for kind in _KNOWN_KINDS:
        kind_dir = os.path.join(wiki_root, kind)
        if not os.path.isdir(kind_dir):
            continue
        try:
            entries = os.listdir(kind_dir)
        except OSError:
            continue
        for entry in entries:
            if not _is_plausible_domain(entry):
                continue
            if os.path.isdir(os.path.join(kind_dir, entry)):
                counts[entry] = counts.get(entry, 0) + 1
    return sorted(d for d, c in counts.items() if c >= 2)


def audit_all_domains(
    wiki_root: str,
    *,
    max_age_days: float | None = _DEFAULT_MAX_AGE_DAYS,
) -> list[DomainCoverage]:
    """Audit every discovered domain. Sorted by missing-count desc so the
    most under-documented projects surface first.

    ``max_age_days`` propagates to each per-domain audit so stale anchor
    pages count as missing.
    """
    rolls = [
        audit_domain(wiki_root, d, max_age_days=max_age_days)
        for d in list_domains(wiki_root)
    ]
    rolls.sort(key=lambda r: r.missing_count, reverse=True)
    return rolls
