"""Pure normalization for the public host-event-v1 activity contract."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from math import isfinite
from typing import Any

_EVENT_KINDS = {
    "prompt",
    "tool_call",
    "mcp_call",
    "api_call",
    "db_read",
    "db_write",
    "file_read",
    "file_edit",
    "file_write",
    "terminal_run",
    "skill",
    "subagent",
    "web",
}
_FILE_ACTIONS = {
    "file_read": "read",
    "file_edit": "edit",
    "file_write": "write",
}
_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# Existing prompt labels have always been bounded to 200 characters. Reuse
# that display contract for producer-supplied summaries/results so the neutral
# input cannot make activity rows unbounded.
_DISPLAY_TEXT_CHARS = 200

FileAction = Callable[[str, str, str], dict[str, Any]]


def _timestamp(value: Any) -> float | None:
    """Host-event timestamp → epoch seconds, or ``None`` when invalid."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        return parsed if isfinite(parsed) else None
    except (ValueError, OverflowError):
        return None


def _detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "schema_version": "1",
        "host": str(event["host"]),
    }
    for key in ("input_summary", "result"):
        value = event.get(key)
        if isinstance(value, str) and value:
            detail[key] = value[:_DISPLAY_TEXT_CHARS]
    return detail


def _row(
    event: dict[str, Any],
    ts: float,
    semantics: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    if semantics.get("path"):
        detail["path"] = semantics["path"]
    if semantics.get("command_path"):
        detail["command_path"] = semantics["command_path"]
    kind = str(event["event"])
    return {
        "session_id": str(event["session_id"]),
        "ts": ts,
        "cwd": str(event.get("cwd") or ""),
        "event_type": kind,
        "tool": str(event.get("tool") or kind),
        "action": semantics["action"],
        "target_id": semantics["target_id"],
        "target_kind": semantics["target_kind"],
        "target_label": semantics["target_label"],
        "edge_kind": semantics["edge_kind"],
        "detail": detail,
    }


def normalize_host_event(
    event: dict[str, Any], file_action: FileAction
) -> dict[str, Any] | None:
    """Normalize the public ``host-event-v1`` contract.

    The event kind is authoritative. Tool names are retained as provenance,
    never reinterpreted through Claude's tool-name taxonomy. ``file_action``
    is injected by ``activity_graph`` so both input shapes share the existing
    canonical file-id implementation without a dependency cycle.
    """
    if event.get("schema_version") != "1":
        return None
    host = event.get("host")
    session_id = event.get("session_id")
    kind = event.get("event")
    ts = _timestamp(event.get("timestamp"))
    if not isinstance(host, str) or not _HOST_RE.fullmatch(host):
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    if kind not in _EVENT_KINDS or ts is None:
        return None

    cwd = str(event.get("cwd") or "")
    detail = _detail(event)
    summary = event.get("input_summary")
    artifact = event.get("artifact")
    tool = event.get("tool")

    if kind == "prompt":
        if not isinstance(summary, str) or not summary:
            return None
        return {
            "session_id": session_id,
            "ts": ts,
            "cwd": cwd,
            "event_type": kind,
            "tool": "",
            "action": "prompt",
            "target_id": "",
            "target_kind": "prompt",
            "target_label": summary[:_DISPLAY_TEXT_CHARS],
            "edge_kind": "",
            "detail": detail,
        }

    if kind in _FILE_ACTIONS:
        if not isinstance(artifact, str) or not artifact:
            return None
        return _row(event, ts, file_action(_FILE_ACTIONS[kind], artifact, cwd), detail)

    if kind == "tool_call":
        if not isinstance(tool, str) or not tool:
            return None
        if isinstance(artifact, str) and artifact:
            semantics = file_action("tool", artifact, cwd)
            semantics["edge_kind"] = "use"
        else:
            semantics = {
                "action": "tool",
                "target_id": f"tool:{tool}",
                "target_kind": "tool_hub",
                "target_label": tool,
                "edge_kind": "use",
            }
        return _row(event, ts, semantics, detail)

    if kind == "mcp_call":
        if not isinstance(tool, str) or not tool:
            return None
        label = str(artifact or tool)
        semantics = {
            "action": "mcp_call",
            "target_id": f"mcp:{label}",
            "target_kind": "mcp",
            "target_label": label,
            "edge_kind": "call",
        }
        return _row(event, ts, semantics, detail)

    if kind == "api_call":
        if not isinstance(artifact, str) or not artifact:
            return None
        label = artifact[:_DISPLAY_TEXT_CHARS]
        semantics = {
            "action": "api_call",
            "target_id": f"api:{label}",
            "target_kind": "api",
            "target_label": label,
            "edge_kind": "call",
        }
        return _row(event, ts, semantics, detail)

    if kind in {"db_read", "db_write"}:
        if not isinstance(artifact, str) or not artifact:
            return None
        action = "read" if kind == "db_read" else "write"
        label = artifact[:_DISPLAY_TEXT_CHARS]
        semantics = {
            "action": kind,
            "target_id": f"db:{label}",
            "target_kind": "database",
            "target_label": label,
            "edge_kind": action,
        }
        return _row(event, ts, semantics, detail)

    if kind == "terminal_run":
        if not isinstance(summary, str) or not summary:
            return None
        semantics = {
            "action": "run",
            "target_id": f"cmd:{summary[:80]}",
            "target_kind": "command",
            "target_label": summary[:80],
            "edge_kind": "run",
        }
    elif kind == "skill":
        label = str(tool or summary or "")
        if not label:
            return None
        semantics = {
            "action": "skill",
            "target_id": f"skill:{label}",
            "target_kind": "skill",
            "target_label": label,
            "edge_kind": "use",
        }
    elif kind == "subagent":
        label = str(tool or summary or "")
        if not label:
            return None
        semantics = {
            "action": "subagent",
            "target_id": f"agent:{label}",
            "target_kind": "agent",
            "target_label": label,
            "edge_kind": "spawn",
        }
    else:  # web
        label = str(artifact or summary or "")
        if not label:
            return None
        semantics = {
            "action": "web",
            "target_id": f"web:{label[:80]}",
            "target_kind": "web",
            "target_label": label[:80],
            "edge_kind": "fetch",
        }
    return _row(event, ts, semantics, detail)
