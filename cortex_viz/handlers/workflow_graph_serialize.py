"""Node/edge → JSON-dict serialization for the workflow graph.

Split out of ``handlers/workflow_graph.py`` (was 817 lines) so the
serialization helpers can be shared between the synchronous builder
path and the interleaved streaming path without an import cycle. Pure
mapping logic — no I/O.
"""

from __future__ import annotations

from typing import Any

_GLOBAL_DOMAIN_TOKEN = "__global__"


def _plain_domain(domain_id: str | None) -> str:
    """Strip the ``domain:`` prefix so JS views can filter by plain label."""
    if not domain_id:
        return ""
    if domain_id.startswith("domain:"):
        return domain_id.split(":", 1)[1]
    return domain_id


# Snake_case → camelCase aliases for UI compatibility. The card
# renderers (knowledge.js, timeline.js) predate the v1 schema and read
# camelCase field names; the schema itself stays snake_case.
_CAMEL_ALIASES = {
    "consolidation_stage": "consolidationStage",
    "heat_base": "heatBase",
    "hours_in_stage": "hoursInStage",
    "stage_entered_at": "stageEnteredAt",
    "access_count": "accessCount",
    "useful_count": "usefulCount",
    "replay_count": "replayCount",
    "reconsolidation_count": "reconsolidationCount",
    "surprise_score": "surpriseScore",
    "emotional_valence": "emotionalValence",
    "dominant_emotion": "dominantEmotion",
    "hippocampal_dependency": "hippocampalDependency",
    "schema_match_score": "schemaMatchScore",
    "schema_id": "schemaId",
    "separation_index": "separationIndex",
    "interference_score": "interferenceScore",
    "encoding_strength": "encodingStrength",
    "compression_level": "compressionLevel",
    "store_type": "storeType",
    "is_protected": "isProtected",
    "is_stale": "isStale",
    "is_benchmark": "isBenchmark",
    "is_global": "isGlobal",
    "no_decay": "noDecay",
    "last_accessed": "lastAccessed",
    "created_at": "createdAt",
    "subagent_type": "subagentType",
    "session_id": "sessionId",
}


def _node_to_dict(n) -> dict[str, Any]:
    d = n.model_dump(exclude_none=True)
    # D3 convention
    d["type"] = d["kind"]
    # Legacy UI compatibility — knowledge.js / timeline.js expect a plain
    # ``domain`` label and ``isGlobal`` flag on every node. The v1 schema
    # only carries ``domain_id`` (e.g. ``domain:cortex``), so we derive.
    domain_id = d.get("domain_id") or ""
    plain = _plain_domain(domain_id)
    if plain and plain != _GLOBAL_DOMAIN_TOKEN:
        d["domain"] = plain
        if "isGlobal" not in d:
            d["isGlobal"] = False
        # selectableDomain = this is a real project slug, not a filesystem path.
        # Rules (single definition, no client re-derives):
        #   - Filesystem paths contain '/' → not a project slug
        #   - Build-artifact subdirectories contain '(' → not a project slug
        # Everything else (cortex, agentic-ai, ...) is a selectable project.
        if d.get("kind") == "domain":
            _lbl = plain or ""
            d["selectableDomain"] = (
                "/" not in _lbl and "\\" not in _lbl and "(" not in _lbl
            )
    else:
        d["domain"] = "global"
        d["isGlobal"] = True
        if d.get("kind") == "domain":
            d["selectableDomain"] = False
    # camelCase aliases — card renderers use these
    for snake, camel in _CAMEL_ALIASES.items():
        if snake in d and camel not in d:
            d[camel] = d[snake]
    return d


def _edge_to_dict(e) -> dict[str, Any]:
    d = e.model_dump(exclude_none=True)
    d["type"] = d["kind"]
    return d
