"""Procedural-skill HTTP endpoint (B1 — basal-ganglia skill memory).

GET /api/skills — learned procedures (recurring successful action sequences),
ranked by proficiency. Read-only; reads the shared Cortex Postgres
``procedural_skills`` table directly, mirroring the memory-browser endpoints.

Procedural memory is the non-declarative counterpart to the episodic/semantic
memories shown elsewhere in the viz: skills are retrieved by *situation*, not by
content, and each carries a reinforced success rate (proficiency) and a
habitual flag. This endpoint is defensive — a Cortex store that predates
procedural memory simply returns an empty list.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from cortex_viz.server.http_standalone_response import send_json_error, send_json_ok


def _query_params(handler) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items() if v}


def _format_skill(row: dict) -> dict:
    """Serialize a procedural_skills row for the UI.

    ``action_sequence`` is a ``>``-joined string of step keys
    (``tool`` or ``tool:target_kind``); expose it both raw and as a list so the
    panel can render the routine as a chain.
    """
    seq_raw = row.get("action_sequence") or ""
    steps = [s for s in (part.strip() for part in seq_raw.split(">")) if s]
    occ = row.get("occurrences", 0) or 0
    succ = row.get("success_count", 0) or 0
    last_seen = row.get("last_seen")
    return {
        "skill_id": row.get("skill_id", ""),
        "sequence": steps,
        "sequence_text": " → ".join(steps),
        "length": len(steps),
        "context_signature": row.get("context_signature", "") or "",
        "occurrences": occ,
        "success_count": succ,
        "failure_count": row.get("failure_count", 0) or 0,
        "proficiency": round(float(row.get("proficiency", 0.0) or 0.0), 4),
        "is_habitual": bool(row.get("is_habitual", False)),
        "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat")
        else (last_seen or ""),
    }


def serve_skills(handler, store) -> None:
    """GET /api/skills — procedural skills ranked by proficiency."""
    try:
        params = _query_params(handler)
        min_prof = float(params.get("min_proficiency", "0") or 0)
        limit = int(params.get("limit", "200") or 200)
        rows = store.list_procedural_skills(min_proficiency=min_prof, limit=limit)
        skills = [_format_skill(r) for r in rows]
        send_json_ok(
            handler,
            {
                "skills": skills,
                "count": len(skills),
                "habitual_count": sum(1 for s in skills if s["is_habitual"]),
            },
        )
    except Exception as e:  # pragma: no cover - defensive
        send_json_error(handler, e)


__all__ = ["serve_skills"]
