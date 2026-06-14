"""Re-export shim — the graph cache/build/discussions module was split.

The 2120-line god-module was decomposed (2026-06-14, near-decomposability)
into focused sub-500-line modules:

* ``graph_cache_state``  — ALL shared mutable globals (the single owner of the
  cross-process cache state; build child + server both import it)
* ``graph_wire``         — pure slim-wire helpers (_round4/_slim_node/_place_around)
* ``graph_appliers``     — server-process appliers + read accessors
* ``graph_build``        — background builder (baseline + DrL bake + finalise)
* ``graph_build_l6``     — L6 AST per-project sweep + disk cache
* ``graph_response``     — /api/graph response builder + query parser
* ``graph_discussions``  — discussion pages + memory-vitals helpers

This module keeps every public name resolving from
``cortex_viz.server.http_standalone_graph`` so existing importers
(``http_standalone``, ``http_standalone_endpoints``, ``graph_stream``) work
with ZERO edits.

NOTE on shared globals: ``_graph_cache`` and the other mutable cache globals
are NOT re-exported here. Re-exporting a module global binds the value at
import time (``None``) and never tracks reassignment, which would silently
fork the cross-process state. Direct-global readers must import the OWNER
(``graph_cache_state``) — see ``build_process`` (writes) and
``recompute_layout`` (reads ``_graph_cache``).
"""

from __future__ import annotations

# ── Shared state owner (functions that read live globals are safe to
#    re-export because they execute in the owner's namespace) ──
from cortex_viz.server.graph_cache_state import (  # noqa: F401
    get_layout_authority,
    graph_cache_data,
    set_build_epoch,
)

# ── Server-process appliers + read accessors ──
from cortex_viz.server.graph_appliers import (  # noqa: F401
    apply_delta,
    apply_done,
    apply_graph_replace,
    apply_phase_ready,
    apply_progress,
    begin_epoch,
    get_build_progress,
    get_graph_slice,
    get_node_neighbors,
    get_node_record,
    get_phase_payload,
)

# ── Background builder ──
from cortex_viz.server.graph_build import (  # noqa: F401
    _kick_background_build,
    ensure_build_started,
)

# ── /api/graph response ──
from cortex_viz.server.graph_response import (  # noqa: F401
    get_graph_response,
    parse_graph_query,
)

# ── Discussions ──
from cortex_viz.server.graph_discussions import (  # noqa: F401
    build_discussion_detail,
    build_discussions_response,
    parse_discussion_params,
)
