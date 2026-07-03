"""Discussion-page builders + memory-vitals helpers.

Extracted verbatim from ``http_standalone_graph.py``. Depends only on the
shared cache state (``graph_cache_state._cached_domain_hub_ids``) and
infrastructure I/O — no build/appliers coupling.
"""

from __future__ import annotations

import time

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.http_standalone_state import (
    CONVERSATIONS_CACHE_TTL,
    get_cached_conversations_state,
    set_cached_conversations_state,
)


def parse_discussion_params(path: str) -> dict:
    """Parse ``/api/discussions`` query string."""
    result: dict = {"project": None, "batch": 0, "batch_size": 500}
    if "?" not in path:
        return result
    for p in path.split("?", 1)[1].split("&"):
        if p.startswith("project="):
            result["project"] = p[8:]
        elif p.startswith("batch="):
            try:
                result["batch"] = int(p[6:])
            except ValueError:
                pass
        elif p.startswith("batch_size="):
            try:
                result["batch_size"] = int(p[11:])
            except ValueError:
                pass
    return result


def _compute_memory_vitals(store) -> dict:
    """Aggregate consolidation-stage counts, mean heat, store-type split,
    B1 procedural-skill counts, and the C1 provenance breakdown.

    SQL-side aggregates only — the previous version pulled the ENTIRE
    corpus through ``get_hot_memories`` (every row through
    ``effective_heat``, ~50 s at 108k memories), which is why it never got
    wired to an endpoint. GROUP BYs are ~140 ms and the effective-heat AVG
    ~1.9 s (measured 2026-07-02); the /api/stats caller TTL-caches the
    result so polls never stack the AVG.
    """
    counts = store.count_memories() or {}
    stages = store.get_stage_counts() or {}
    # C1 source / reality monitoring: epistemic-origin breakdown. `inferred`
    # is the confabulation-risk cohort (self-generated, no external
    # grounding). Unrecognised attributions fold into `unknown`.
    provenance = {"perceived": 0, "told": 0, "inferred": 0, "unknown": 0}
    for attr, n in (store.get_provenance_counts() or {}).items():
        provenance[attr if attr in provenance else "unknown"] += int(n)

    # C1 read-side enforcement: semantic memories flagged at the promotion point
    # as a confabulation crystallized as fact (INFERRED cluster, no grounding).
    # Distinct from provenance['inferred'] (inferred memories AT REST) — this is
    # the subset that was PROMOTED to knowledge. Zero on a store predating the
    # C1 read-side gate.
    crystallized_confabulations = 0
    try:
        crystallized_confabulations = store.count_crystallized_confabulations()
    except Exception:
        pass
    procedural_skills = 0
    habitual_skills = 0
    try:
        skill_rows = store.list_procedural_skills(min_proficiency=0.0, limit=1000)
        procedural_skills = len(skill_rows)
        habitual_skills = sum(1 for r in skill_rows if r.get("is_habitual"))
    except Exception:
        pass  # store predates procedural memory (B1) — report zero

    # E1 habituation & sensitization: surplus repeated presentations the write
    # gate's response decrement is damping (Rankin 2009). Zero on a store
    # predating habituation.
    habituated_repeats = 0
    try:
        habituated_repeats = store.count_habituated_repeats()
    except Exception:
        pass

    # E2 fear extinction / inhibitory learning: memories carrying a reversible
    # inhibitory extinction tag — deprecated-but-retained (suppressed WITHOUT
    # deletion), so they can spontaneously recover or be reinstated (Bouton
    # 2004; Milad & Quirk 2012). Distinct from is_stale (active_forgetting's
    # soft-delete). Zero on a store predating extinction.
    extinguished = 0
    try:
        extinguished = store.count_extinguished()
    except Exception:
        pass

    # A2 conflict monitoring: pairs of persisted claims that disagree (share an
    # entity, opposing claim_type) — the standing counterpart of the recall-time
    # conflict monitor's claim_resolver routing (Botvinick 2001). Zero on a
    # store predating the claim/wiki layer.
    conflicting_claim_pairs = 0
    try:
        conflicting_claim_pairs = store.count_conflicting_claim_pairs()
    except Exception:
        pass

    # C2 dual-process retrieval: over a bounded recent sample, the share of
    # memories resolvable by FAMILIARITY ALONE (a near-duplicate neighbour above
    # the familiarity threshold) — the standing counterpart of the recall-time
    # familiarity triage (Yonelinas 2002; Diana et al 2007). Zero-filled on a
    # store predating vectors.
    familiarity_resolvable = {
        "sampled": 0, "resolvable": 0, "share": 0.0, "mean_top_sim": 0.0
    }
    try:
        familiarity_resolvable = store.count_familiarity_resolvable()
    except Exception:
        pass

    # F1 two-phase consolidation: standing footprint of the NREM/REM split
    # (mcp_server/core/sleep_phases.py) — NREM-like replay stores auto-narration
    # semantic memories (source='sleep-compute'); REM-like recombination forms
    # abstract schemas (schemas table). Diekelmann & Born 2010; van de Ven 2020.
    # Zero-filled on a store predating either phase.
    sleep_phase_outputs = {"nrem": 0, "rem": 0}
    try:
        sleep_phase_outputs = store.count_sleep_phase_outputs()
    except Exception:
        pass

    # D1 stress-hormone (glucocorticoid) modulation: the session-stress scalar
    # and inverted-U consolidation gain of the LAST offline CLS cycle. Moderate
    # session stress enhances consolidation, extreme impairs it (Roozendaal &
    # McGaugh 2011; McGaugh 2000). None-safe neutral (stress 0, gain 1.0) when
    # the store predates stress logging or the last cycle was calm/ablated.
    stress_modulation = {"stress": 0.0, "gain": 1.0, "is_impairing": False}
    try:
        stress_modulation = store.count_stress_modulation()
    except Exception:
        pass

    # F2 targeted memory reactivation: the cue that biased the last offline
    # consolidation's NREM replay (mcp_server/core/targeted_reactivation.py) —
    # cueing biases *which* memories consolidate (Rasch 2007; Oudiette & Paller
    # 2013). None when the last cycle ran cue-free / TMR was ablated / the store
    # predates cue logging.
    targeted_reactivation = {"cue": None, "cued_replayed": 0}
    try:
        targeted_reactivation = store.count_targeted_reactivation()
    except Exception:
        pass

    # A3 goal / task-set maintenance: the sustained goal vector promoted from
    # the store's active prospective triggers (mcp_server/core/goal_maintenance.py).
    # While a goal is active it biases the write gate + recall fusion toward
    # goal-relevant information (Miller & Cohen 2001). Inactive (no active
    # trigger contributes a keyword/entity/directory signal) = the write+recall
    # identity, exactly the no-goal case. Zero-filled/inactive on a store
    # predating goal maintenance. DESIGN INFERENCE — a keyword/entity goal-match
    # promoted from the trigger surface, not a learned PFC task-set controller.
    active_goal = {"active": False, "triggers": 0, "keywords": 0, "label": None}
    try:
        active_goal = store.count_active_goal()
    except Exception:
        pass

    # B3 cerebellar forward model: over a bounded recent sample, the mean
    # absolute one-step forward-model prediction error of the heat trajectory
    # (mcp_server/core/forward_model.py) — how self-predictable recent heat is.
    # High = jumpy activation the smooth dynamics fail to anticipate; ~0 =
    # predictable. Zero-filled on a store predating heat_base. Wolpert, Miall &
    # Kawato 1998; Ito 2008. A minimal deterministic predict→error→correct EMA
    # (LOW AI PRIORITY per the gap analysis), not a learned cerebellar circuit.
    forward_model = {"sampled": 0, "mean_error": 0.0}
    try:
        forward_model = store.count_forward_model()
    except Exception:
        pass

    # A1 central-executive attentional control: the standing bottom-up SALIENCE
    # footprint that feeds the recall-time attentional re-weight
    # (mcp_server/core/attentional_control.py, wired via
    # recall_pipeline.attentional_focus_rerank). The top-down half of that stage
    # is query-dependent and in-flight (nothing at rest to measure); the
    # bottom-up half (0.5·importance + 0.5·|valence|) is persisted, so we report
    # how CONCENTRATED it is — the share of recent salience mass held by the
    # Cowan ~4 most-salient memories. High = a few memories dominate stimulus-
    # driven capture; low = salience spread evenly (attention rests on the query
    # alone). Descriptive salience statistic, NOT the softmax spotlight (which
    # needs a live query) — zero-filled on a store predating affect columns.
    attentional_salience = {
        "sampled": 0, "focus_share": 0.0, "mean_salience": 0.0, "max_salience": 0.0
    }
    try:
        attentional_salience = store.count_attentional_salience()
    except Exception:
        pass

    return {
        "consolidation_pipeline": stages,
        "mean_heat": round(store.get_avg_heat() or 0.0, 4),
        "total_memories": int(counts.get("total", 0) or 0),
        "episodic": int(counts.get("episodic", 0) or 0),
        "semantic": int(counts.get("semantic", 0) or 0),
        "procedural_skills": procedural_skills,
        "habitual_skills": habitual_skills,
        "provenance": provenance,
        "inferred_memories": provenance["inferred"],
        "crystallized_confabulations": crystallized_confabulations,
        "habituated_repeats": habituated_repeats,
        "extinguished": extinguished,
        "conflicting_claim_pairs": conflicting_claim_pairs,
        "familiarity_resolvable": familiarity_resolvable,
        "sleep_phase_outputs": sleep_phase_outputs,
        "targeted_reactivation": targeted_reactivation,
        "stress_modulation": stress_modulation,
        "active_goal": active_goal,
        "forward_model": forward_model,
        "attentional_salience": attentional_salience,
    }


def _session_counts_from_profiles(profiles: dict) -> dict[str, int]:
    """Extract per-domain session counts from a profiles.json payload."""
    out: dict[str, int] = {}
    for did, ddata in (profiles.get("domains") or {}).items():
        out[did] = ddata.get("sessionCount", 0)
    return out


def _get_cached_conversations() -> list[dict]:
    """Shared cache wrapper — refreshes via ``discover_conversations``."""
    cached, ts = get_cached_conversations_state()
    now = time.time()
    if cached is None or (now - ts) > CONVERSATIONS_CACHE_TTL:
        from cortex_viz.infrastructure.scanner import discover_conversations

        cached = discover_conversations()
        set_cached_conversations_state(cached, now)
    return cached


def build_discussions_response(path: str) -> dict:
    """Paginated response for ``/api/discussions``."""
    from cortex_viz.core.graph_builder_discussions import build_discussion_nodes

    params = parse_discussion_params(path)
    conversations = _get_cached_conversations()
    if params["project"]:
        conversations = [
            c for c in conversations if c.get("project") == params["project"]
        ]
    conversations = sorted(
        conversations,
        key=lambda c: c.get("startedAt") or "",
        reverse=True,
    )
    total = len(conversations)
    batch_size = max(1, params["batch_size"])
    batch = params["batch"]
    total_batches = max(1, (total + batch_size - 1) // batch_size)
    start = batch * batch_size
    end = start + batch_size
    page = conversations[start:end]
    nodes, edges = build_discussion_nodes(page, state._cached_domain_hub_ids)
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "total": total,
            "batch": batch,
            "batch_size": batch_size,
            "total_batches": total_batches,
        },
    }


def _find_session_file(session_id: str):
    """Whitelist scan of every project dir for ``<session_id>.jsonl``."""
    from cortex_viz.infrastructure.config import CLAUDE_DIR

    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.is_dir():
        return None
    target = session_id + ".jsonl"
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / target
        if candidate.is_file():
            return candidate
    return None


def build_discussion_detail(session_id: str) -> dict:
    """Detail response for ``/api/discussion/<session_id>``."""
    from cortex_viz.infrastructure.conversation_reader import (
        format_conversation_messages,
        read_full_conversation,
    )

    conversations = _get_cached_conversations()
    conv = next(
        (c for c in conversations if c.get("sessionId") == session_id),
        None,
    )
    if conv is None:
        return {"error": "Discussion not found", "sessionId": session_id}

    found_path = _find_session_file(session_id)
    if found_path is None:
        return {"error": "Session file not found", "sessionId": session_id}

    raw = read_full_conversation(str(found_path))
    messages = format_conversation_messages(raw)
    return {
        "sessionId": session_id,
        "project": conv.get("project"),
        "messages": messages,
        "startedAt": conv.get("startedAt"),
        "endedAt": conv.get("endedAt"),
        "duration": conv.get("duration"),
        "turnCount": conv.get("turnCount"),
    }
