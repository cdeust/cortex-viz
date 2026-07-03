"""Dashboard data formatting helpers.

Pure functions that transform store data into dashboard API responses.
No I/O or server logic -- only data formatting.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

# Node caps for the 3D atom-shell view. Each node is a THREE.Group of ~3–4
# objects (core mesh + glow sprite + label); keeping the visible set near ~1k
# nodes holds the scene well inside WebGL draw-call budgets and keeps the
# polled JSON small. The full corpus (100k+ memories / entities) belongs to
# the galaxy graph build, not this hot-subset dashboard.
DASHBOARD_MEMORY_LIMIT = 600
DASHBOARD_ENTITY_LIMIT = 400


def build_dashboard_data(store) -> dict:
    """Assemble the hot-subset dashboard data in a single response.

    Bounded to the hottest memories + entities so the 3D atom view stays
    renderable; ``stats`` still reflects the full-corpus counts.
    """
    counts = store.count_memories()
    hot = store.get_hot_memories(min_heat=0.0, limit=DASHBOARD_MEMORY_LIMIT)
    entities = sorted(
        store.get_all_entities(),
        key=lambda e: e.get("heat", 0) or 0,
        reverse=True,
    )[:DASHBOARD_ENTITY_LIMIT]
    kept_entity_ids = {e["id"] for e in entities}
    relationships = [
        r
        for r in store.get_all_relationships()
        if r.get("source_entity_id") in kept_entity_ids
        and r.get("target_entity_id") in kept_entity_ids
    ]
    recent = store.get_recent_memories(limit=50)
    domain_counts = store.get_domain_counts()
    engram_slots = _safe_call(store, "get_all_engram_slots", [])
    slot_occupancy = _safe_call(store, "get_slot_occupancy", {})

    engram_active = build_engram_data(engram_slots, slot_occupancy)

    stage_counts = _safe_call(store, "get_stage_counts", {})
    schema_count = _safe_call(store, "count_schemas", 0)
    schemas = _safe_call(store, "get_all_schemas", [])

    return {
        "stats": build_stats(
            store,
            counts,
            len(engram_slots),
            len(engram_active),
            stage_counts=stage_counts,
            schema_count=schema_count,
        ),
        "hot_memories": [format_memory(m, 120) for m in hot],
        "entities": [format_entity(e) for e in entities],
        "relationships": [format_relationship(r) for r in relationships],
        "recent_memories": [format_memory(m, 200) for m in recent],
        "engram_slots": engram_active,
        "domain_counts": domain_counts,
        "stage_counts": stage_counts,
        "schemas": [format_schema(s) for s in schemas],
        "schema_count": schema_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _safe_call(store, method: str, default):
    """Call an optional store method by name, returning default if it is
    absent or raises.

    The thin-viz ``MemoryReader`` deliberately omits engram/schema/stage
    methods that the full Cortex ``MemoryStore`` has. Looking the method up
    via ``getattr`` (rather than evaluating ``store.method`` at the call
    site) lets the dashboard degrade those optional sections to empty
    instead of crashing the whole response.
    """
    fn = getattr(store, method, None)
    if fn is None:
        return default
    try:
        return fn()
    except Exception:
        return default


def build_stats(
    store,
    counts: dict,
    total_slots: int = 0,
    active_slots: int = 0,
    stage_counts: dict | None = None,
    schema_count: int = 0,
) -> dict:
    """Build the stats summary block."""
    sc = stage_counts or {}
    return {
        "total": counts.get("total", 0),
        "episodic": counts.get("episodic", 0),
        "semantic": counts.get("semantic", 0),
        "active": counts.get("active", 0),
        "archived": counts.get("archived", 0),
        "stale": counts.get("stale", 0),
        "protected": counts.get("protected", 0),
        "avg_heat": round(store.get_avg_heat(), 4),
        "entities": store.count_entities(),
        "relationships": store.count_relationships(),
        "triggers": store.count_active_triggers(),
        "procedural_skills": _safe_call(store, "count_procedural_skills", 0),
        "provenance": _safe_call(
            store,
            "count_provenance",
            {"perceived": 0, "told": 0, "inferred": 0, "unknown": 0},
        ),
        "crystallized_confabulations": _safe_call(
            store, "count_crystallized_confabulations", 0
        ),
        "habituated_repeats": _safe_call(store, "count_habituated_repeats", 0),
        "extinguished": _safe_call(store, "count_extinguished", 0),
        "conflicting_claim_pairs": _safe_call(
            store, "count_conflicting_claim_pairs", 0
        ),
        "familiarity_resolvable": _safe_call(
            store,
            "count_familiarity_resolvable",
            {"sampled": 0, "resolvable": 0, "share": 0.0, "mean_top_sim": 0.0},
        ),
        "sleep_phase_outputs": _safe_call(
            store, "count_sleep_phase_outputs", {"nrem": 0, "rem": 0}
        ),
        "targeted_reactivation": _safe_call(
            store, "count_targeted_reactivation", {"cue": None, "cued_replayed": 0}
        ),
        "stress_modulation": _safe_call(
            store,
            "count_stress_modulation",
            {"stress": 0.0, "gain": 1.0, "is_impairing": False},
        ),
        "active_goal": _safe_call(
            store,
            "count_active_goal",
            {"active": False, "triggers": 0, "keywords": 0, "label": None},
        ),
        "attentional_salience": _safe_call(
            store,
            "count_attentional_salience",
            {
                "sampled": 0,
                "focus_share": 0.0,
                "mean_salience": 0.0,
                "max_salience": 0.0,
            },
        ),
        "forward_model": _safe_call(
            store,
            "count_forward_model",
            {"sampled": 0, "mean_error": 0.0},
        ),
        "last_consolidation": store.get_last_consolidation(),
        "engram_total_slots": total_slots,
        "engram_occupied_slots": active_slots,
        "labile": sc.get("labile", 0),
        "early_ltp": sc.get("early_ltp", 0),
        "late_ltp": sc.get("late_ltp", 0),
        "consolidated": sc.get("consolidated", 0),
        "reconsolidating": sc.get("reconsolidating", 0),
        "schema_count": schema_count,
    }


def format_memory(m: dict, content_limit: int) -> dict:
    """Format a memory row for the dashboard API."""
    content = m.get("content", "")
    return {
        "id": m["id"],
        "content": content,
        "heat": round(m.get("heat", 0), 4),
        "importance": round(m.get("importance", 0.5), 4),
        "store_type": m.get("store_type", "episodic"),
        "tags": parse_tags(m.get("tags", [])),
        "created_at": m.get("created_at", ""),
        "domain": m.get("domain", ""),
        "surprise_score": round(m.get("surprise_score", 0), 4),
        "emotional_valence": round(m.get("emotional_valence", 0), 4),
        "compression_level": m.get("compression_level", 0),
        "is_compressed": bool(m.get("is_compressed", 0)),
        "is_protected": bool(m.get("is_protected", 0)),
        "slot_index": m.get("slot_index"),
        "source": m.get("source", ""),
        "agent_context": m.get("agent_context", ""),
        "is_global": bool(m.get("is_global", False)),
        "access_count": m.get("access_count", 0),
        "consolidation_stage": m.get("consolidation_stage", "labile"),
        "schema_match_score": round(m.get("schema_match_score", 0), 4),
        "interference_score": round(m.get("interference_score", 0), 4),
        "hippocampal_dependency": round(m.get("hippocampal_dependency", 1.0), 4),
        "theta_phase": round(m.get("theta_phase_at_encoding", 0), 4),
        "encoding_strength": round(m.get("encoding_strength", 1.0), 4),
        "separation_index": round(m.get("separation_index", 0), 4),
        "plasticity": round(m.get("plasticity", 1.0), 4),
        "stability": round(m.get("stability", 0), 4),
        "last_accessed": m.get("last_accessed", ""),
        "replay_count": m.get("replay_count", 0),
        "hours_in_stage": round(m.get("hours_in_stage", 0), 2),
        "reconsolidation_count": m.get("reconsolidation_count", 0),
        "excitability": round(m.get("excitability", 1.0), 4),
        "stage_entered_at": m.get("stage_entered_at", ""),
        "confidence": round(m.get("confidence", 1.0), 4),
    }


def format_entity(e: dict) -> dict:
    """Format an entity row for the dashboard API."""
    return {
        "id": e["id"],
        "name": e.get("name", ""),
        "type": e.get("type", "unknown"),
        "heat": round(e.get("heat", 0), 4),
        "domain": e.get("domain", ""),
    }


def format_relationship(r: dict) -> dict:
    """Format a relationship row for the dashboard API."""
    return {
        "source": r["source_entity_id"],
        "target": r["target_entity_id"],
        "type": r.get("relationship_type", "related"),
        "weight": round(r.get("weight", 1.0), 4),
        "is_causal": bool(r.get("is_causal", 0)),
        "release_probability": round(r.get("release_probability", 0.5), 4),
        "facilitation": round(r.get("facilitation", 0), 4),
        "depression": round(r.get("depression", 0), 4),
        "confidence": round(r.get("confidence", 1.0), 4),
        "last_reinforced": r.get("last_reinforced", ""),
    }


def format_schema(s: dict) -> dict:
    """Format a schema row for the dashboard API."""
    return {
        "id": s.get("schema_id", ""),
        "label": s.get("label", ""),
        "domain": s.get("domain", ""),
        "formation_count": s.get("formation_count", 0),
        "assimilation_count": s.get("assimilation_count", 0),
        "violation_count": s.get("violation_count", 0),
        "consistency_threshold": round(s.get("consistency_threshold", 0.5), 4),
    }


def build_engram_data(slots: list, occupancy: dict) -> list:
    """Format engram slot data -- only slots with memories assigned."""
    result = []
    for s in slots:
        idx = s.get("slot_index", 0)
        occ = occupancy.get(idx, 0)
        if occ > 0:
            result.append(
                {
                    "slot_index": idx,
                    "excitability": round(s.get("excitability", 0), 4),
                    "last_activated": s.get("last_activated", ""),
                    "occupancy": occ,
                }
            )
    return result


def parse_tags(tags) -> list:
    """Parse tags from various storage formats."""
    if isinstance(tags, list):
        return tags
    if isinstance(tags, str):
        try:
            return json.loads(tags)
        except (ValueError, TypeError):
            return []
    return []
