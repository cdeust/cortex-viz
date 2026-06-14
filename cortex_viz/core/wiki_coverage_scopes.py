"""The public ``SCOPES`` tuple — canonical documentation scopes.

Assembled from three data parts (split to respect the 500-line file
limit). Re-exports ``Scope`` and the page-size / freshness constants so
existing ``from cortex_viz.core.wiki_coverage_scopes import ...`` and the
composition module keep resolving.

Scopes (canonical, ordered by structural primacy) — see individual
``Scope`` descriptions for what each one documents.
"""

from __future__ import annotations

from typing import Final

from cortex_viz.core.wiki_coverage_scope_type import (  # noqa: F401
    _DEFAULT_MAX_AGE_DAYS,
    _MIN_PAGE_BYTES,
    Scope,
)
from cortex_viz.core.wiki_coverage_scopes_part1 import SCOPES_PART1
from cortex_viz.core.wiki_coverage_scopes_part2 import SCOPES_PART2
from cortex_viz.core.wiki_coverage_scopes_part3 import SCOPES_PART3

SCOPES: Final[tuple[Scope, ...]] = SCOPES_PART1 + SCOPES_PART2 + SCOPES_PART3
