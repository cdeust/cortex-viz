"""The ``Scope`` dataclass + page-size / freshness constants.

Split out of ``wiki_coverage.py`` (was 1396 lines) so the scope *type*
and the scope *data* can live in dedicated sub-500-line modules. Pure
business logic — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

# Minimum useful page size in bytes. Below this, a page is a stub —
# the scope is not really covered.
_MIN_PAGE_BYTES = 800

# Refresh window: a scope page older than this many days is considered
# stale and counts as missing again, so the auto-curator re-emits an
# authoring job to bring it back in line with the codebase. The wiki
# stays up to date without a human in the loop.
#
# 2026-05-18: 90 days is the conservative default. Pages move slowly;
# anchor pages (architecture / services / api) churn even more slowly.
# Callers that want a tighter cadence pass ``max_age_days`` to
# ``audit_domain`` / ``audit_all_domains``.
_DEFAULT_MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class Scope:
    """One structural documentation scope.

    Each scope names a category of knowledge every project should
    document. ``anchor_paths`` are wiki-relative paths (without the
    domain segment) the coverage scan looks for; the first match counts
    as coverage. ``directory`` is the wiki subtree where pages of this
    scope live — used to find substantive coverage beyond the anchor
    pages.
    """

    name: str
    title: str
    description: str
    anchor_filenames: tuple[str, ...]
    directories: tuple[str, ...]
    suggested_kind: str  # wiki kind to author the missing page as
