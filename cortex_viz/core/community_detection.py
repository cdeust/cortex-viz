"""Associative community detection for the brain view (server-side).

Replaces the browser-side label-propagation (Raghavan, Albert & Kumara
2007) that collapsed the dense combined association substrate into one
mega-community — measured 87-93% of memories under a single label on
the live corpus (2026-07-07). Label propagation optimizes no global
objective; on a graph with hubs its label dynamics percolate like an
epidemic (the epidemic threshold vanishes on heavy-tailed graphs —
Pastor-Satorras & Vespignani, Phys. Rev. Lett. 86:3200, 2001), so one
label swallows the graph.

This module runs Leiden with the CPM (Constant Potts Model) objective
on the SPARSE co-entity channel only (the topical backbone), while the
brain view still RENDERS the full additive 3-channel substrate — the
detection/rendering split the collapse fix depends on.

  source: Traag, V.A., Waltman, L. & van Eck, N.J. (2019), "From
  Louvain to Leiden: guaranteeing well-connected communities", Sci.
  Rep. 9:5233 — the Leiden algorithm; unlike Louvain and LPA it
  guarantees each community is internally connected.
  source: Traag, V.A., Van Dooren, P. & Nesterov, Y. (2011), "Narrow
  scope for resolution-limit-free community detection", Phys. Rev. E
  84:016114 — CPM is resolution-limit-free, so it does not silently
  merge communities smaller than ~sqrt(2m) edges the way modularity
  does (the resolution limit of Fortunato & Barthelemy, PNAS 104:36,
  2007).

Pure core: no I/O. Takes an in-memory edge list + node-id set, returns
a ``node_id -> community_id`` mapping. Degrades to an EMPTY mapping
(every memory left a singleton; the client falls back to per-kind
colour) when igraph/leidenalg are not installed — never raises, so a
build on a machine without the ``community`` extra still produces a
graph.
"""

from __future__ import annotations

import os
from typing import Iterable

# source: CPM resolution gamma is NOT a paper-derived universal constant
# — it sets the density granularity of the partition (a community's
# internal density must exceed gamma; Traag, Van Dooren & Nesterov
# 2011). The right value is corpus-specific and must be measured, never
# imported from another dataset.
#
# source: benchmark scratchpad/community_stats.py on the live corpus
# (9 889 non-stale memories, 27 346 co-entity detection edges),
# 2026-07-07. gamma sweep, largest-community % and count of communities
# clearing the client's MIN_COMMUNITY_SIZE=12 (coverage = memories in
# such a community; the ~37% of memories with no co-entity edge are an
# unclusterable floor, so the coverage ceiling is ~63%):
#   gamma    largest%   #comm>=12   coverage
#   0.0002     3.4%        91         53.1%
#   0.0005     1.8%       133         49.9%   <- selected (knee)
#   0.001      1.0%       162         44.0%
#   0.002      0.7%       134         31.6%
# Every gamma eliminates the label-propagation collapse (baseline: one
# mega-community of 87-93% of memories). 0.0005 is the knee: near-ceiling
# coverage with a large number of distinct communities and the largest
# community at a safe 1.8%. Retune per corpus via
# CORTEX_VIZ_COMMUNITY_RESOLUTION without a code change.
DEFAULT_RESOLUTION = 0.0005

# source: deterministic seed matches Cortex core's codebase_communities
# (seed=42) so community ids are stable across builds — the brain view's
# per-community colours/attractors depend on a stable assignment (a
# reshuffled community id would repaint the whole view for no reason on
# every rebuild).
SEED = 42


def _resolve_resolution(resolution: float | None) -> float:
    """Resolve the CPM resolution gamma from the argument, then the
    ``CORTEX_VIZ_COMMUNITY_RESOLUTION`` env knob, then the benchmarked
    default."""
    if resolution is not None:
        return float(resolution)
    try:
        return float(
            os.environ.get("CORTEX_VIZ_COMMUNITY_RESOLUTION", DEFAULT_RESOLUTION)
        )
    except (TypeError, ValueError):
        return DEFAULT_RESOLUTION


def detect_communities(
    edges: Iterable[tuple[str, str, float]],
    node_ids: Iterable[str] | None = None,
    *,
    resolution: float | None = None,
    seed: int = SEED,
) -> dict[str, int]:
    """Partition an associative graph into communities via Leiden + CPM.

    Args:
        edges: iterable of ``(source_id, target_id, weight)`` — the
            SPARSE detection substrate (co-entity channel). Weights
            should be positive; non-positive or missing weights are
            floored to a tiny epsilon so the edge still exists but
            contributes minimally (CPM uses edge weights in its
            objective). Self-loops (source == target) are dropped.
        node_ids: optional full set of node ids that must receive a
            community. Any id here that carries no detection edge is
            assigned its own fresh singleton community (matching the
            prior label-propagation semantics, where an unlinked memory
            ended up a singleton). When ``None``, only vertices that
            appear in ``edges`` are returned.
        resolution: CPM resolution gamma. ``None`` resolves from
            ``CORTEX_VIZ_COMMUNITY_RESOLUTION`` (default
            ``DEFAULT_RESOLUTION``).
        seed: RNG seed for Leiden's local-moving randomness
            (default ``SEED`` for cross-build determinism).

    Returns:
        ``{node_id: community_id}`` with contiguous integer community
        ids starting at 0. Empty dict when igraph/leidenalg are absent
        (graceful degradation — the caller then leaves every node
        without a ``community_id`` and the client colours by kind).
    """
    try:
        import igraph  # type: ignore
        import leidenalg  # type: ignore
    except ImportError:
        return {}

    gamma = _resolve_resolution(resolution)

    # Deterministic vertex order: sort the union of edge endpoints so
    # the igraph vertex indices — and therefore the seeded partition —
    # are reproducible across builds regardless of edge arrival order.
    vertex_set: set[str] = set()
    clean_edges: list[tuple[str, str, float]] = []
    for src, tgt, weight in edges:
        if src is None or tgt is None or src == tgt:
            continue
        vertex_set.add(src)
        vertex_set.add(tgt)
        # CPM weights must be positive to contribute; floor rather than
        # drop so a zero/missing-weight co-entity edge still couples its
        # endpoints structurally.
        w = float(weight) if weight else 0.0
        clean_edges.append((src, tgt, w if w > 0.0 else 1e-9))

    vertices = sorted(vertex_set)
    index_of = {vid: i for i, vid in enumerate(vertices)}

    mapping: dict[str, int] = {}
    next_cid = 0

    if vertices and clean_edges:
        graph = igraph.Graph(n=len(vertices), directed=False)
        graph.add_edges([(index_of[s], index_of[t]) for s, t, _ in clean_edges])
        weights = [w for _, _, w in clean_edges]
        partition = leidenalg.find_partition(
            graph,
            leidenalg.CPMVertexPartition,
            weights=weights,
            resolution_parameter=gamma,
            n_iterations=-1,  # iterate to a stable partition (leidenalg)
            seed=seed,
        )
        membership = partition.membership
        for vid, comm in zip(vertices, membership):
            mapping[vid] = int(comm)
        next_cid = (max(membership) + 1) if membership else 0

    # Isolated nodes (no detection edge) each get a fresh singleton
    # community — same semantics as the prior LPA, where an unlinked
    # memory kept its own initial label.
    if node_ids is not None:
        for nid in node_ids:
            if nid not in mapping:
                mapping[nid] = next_cid
                next_cid += 1

    return mapping


__all__ = ["detect_communities", "DEFAULT_RESOLUTION", "SEED"]
