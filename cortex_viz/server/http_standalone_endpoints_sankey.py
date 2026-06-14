"""Sankey + HUD-stats endpoints for the standalone HTTP server.

Split out of ``http_standalone_endpoints.py`` (was 527 lines) to respect
the 500-line file limit. Owns the consolidation-pipeline Sankey query
(``serve_sankey`` + its SQL helpers) and the true-store-count HUD
endpoint (``serve_stats``). Response shaping flows through
``http_standalone_response``. ``http_standalone_endpoints`` re-exports
these so existing import paths keep resolving.
"""

from __future__ import annotations

from cortex_viz.server.http_standalone_response import (
    send_json_error,
    send_json_ok,
)

_STAGES = (
    "labile",
    "early_ltp",
    "late_ltp",
    "consolidated",
    "reconsolidating",
)

_STAGE_METRICS_SQL = (
    "SELECT COUNT(*) as count, "
    # source: memories table column is heat_base (no `heat` column exists —
    # verified via information_schema 2026-06-14); selecting heat raised a
    # 500 on /api/sankey. heat_base is the canonical thermodynamic heat field.
    "AVG(heat_base) as avg_heat, AVG(importance) as avg_importance, "
    "AVG(replay_count) as avg_replay, AVG(access_count) as avg_access, "
    "AVG(encoding_strength) as avg_encoding, "
    "AVG(interference_score) as avg_interference, "
    "AVG(schema_match_score) as avg_schema, "
    "AVG(hippocampal_dependency) as avg_hippo, "
    "AVG(plasticity) as avg_plasticity, "
    "AVG(stability) as avg_stability, "
    "AVG(hours_in_stage) as avg_hours "
    "FROM memories WHERE consolidation_stage = %s "
    "AND NOT is_benchmark AND NOT is_stale"
)


def _sankey_transitions(store) -> list[dict]:
    rows = store._execute(
        "SELECT from_stage, to_stage, COUNT(*) as count "
        "FROM stage_transitions "
        "GROUP BY from_stage, to_stage "
        "ORDER BY from_stage, to_stage"
    ).fetchall()
    return [dict(r) for r in rows]


def _sankey_timing(store) -> dict[str, dict[str, float]]:
    rows = store._execute(
        "SELECT from_stage, to_stage, "
        "AVG(hours_in_prev_stage) as avg_hours, "
        "MIN(hours_in_prev_stage) as min_hours, "
        "MAX(hours_in_prev_stage) as max_hours "
        "FROM stage_transitions GROUP BY from_stage, to_stage"
    ).fetchall()
    timing: dict[str, dict[str, float]] = {}
    for r in rows:
        key = r["from_stage"] + "->" + r["to_stage"]
        timing[key] = {
            "avg_hours": round(r["avg_hours"], 1),
            "min_hours": round(r["min_hours"], 1),
            "max_hours": round(r["max_hours"], 1),
        }
    return timing


def _sankey_stage_metrics(store) -> dict[str, dict]:
    stage_metrics: dict[str, dict] = {}
    for s in _STAGES:
        r = store._execute(_STAGE_METRICS_SQL, (s,)).fetchone()
        stage_metrics[s] = {
            k: round(v, 3) if isinstance(v, float) else (v or 0)
            for k, v in dict(r).items()
        }
    return stage_metrics


def serve_sankey(handler, store) -> None:
    """GET /api/sankey — consolidation-pipeline Sankey dataset."""
    try:
        total = store._execute(
            "SELECT COUNT(*) as c FROM memories WHERE NOT is_benchmark AND NOT is_stale"
        ).fetchone()
        send_json_ok(
            handler,
            {
                "transitions": _sankey_transitions(store),
                "timing": _sankey_timing(store),
                "stage_metrics": _sankey_stage_metrics(store),
                "total_memories": total["c"],
            },
        )
    except Exception as e:
        send_json_error(handler, e)


def serve_stats(handler, store) -> None:
    """GET /api/stats — TRUE store counts for the HUD.

    The sidebar counters must reflect the whole memory system (what the user
    actually has), NOT just the nodes the current view happens to render. The
    trace view, for instance, renders domain/session/chain nodes and zero
    memory nodes — counting loaded nodes there showed "Memories 0" against a
    475k-memory store. These are cheap COUNT(*) queries straight off the store,
    independent of any graph build. source: user report — HUD showed 0 memories.
    """
    try:
        counts = store.count_memories()
        domains = store.get_domain_counts() or {}
        mem = int(counts.get("total", 0) or 0)
        ent = int(store.count_entities() or 0)
        rel = int(store.count_relationships() or 0)
        send_json_ok(
            handler,
            {
                "domain_count": len(domains),
                "memory_count": mem,
                "entity_count": ent,
                # HUD "Synapses" reads edge_count; here that is the knowledge-
                # graph relationship total (the real synapse population).
                "edge_count": rel,
                # HUD "Nodes": the addressable memory+entity population.
                "node_count": mem + ent,
                "discussion_count": int(counts.get("discussions", 0) or 0),
            },
        )
    except Exception as e:
        send_json_error(handler, e)
