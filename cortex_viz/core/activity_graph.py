"""Pure mapping: one Claude action (hook event) → directional graph fragment.

The live session-activity spine. A Claude Code hook fires on EVERY action and
hands the viz server the raw event ``{tool_name, tool_input, tool_response,
cwd, session_id, ts}``; this module turns it into the normalized activity row
(persisted by ``infrastructure.activity_store``) and into the directional
nodes/edges streamed to the live graph.

Every Claude action is one of a fixed taxonomy — tool / mcp_call / file-read /
file-edit / file-write / terminal-run / skill / subagent / web / prompt — and
each maps to a target node and a typed, DIRECTIONAL edge from the action:

    session ──did──▶ action ──{read|edit|write|run|call|use|spawn|fetch}──▶ target

Pure: zero I/O, stdlib only. Mirrors the existing ``trace.v1`` verbs
(read/edit/write/run) so the live spine and the post-hoc Trace view speak the
same edge language.

FILE target ids use ``core.activity_paths.file_target_id`` — the SAME
``file:<hash>`` scheme the galaxy workflow graph mints (P4 node-unification,
see that module's docstring) — so the live spine's FILE nodes dedup-merge
with the galaxy's on the client instead of rendering as duplicates.
"""

from __future__ import annotations

import re
from typing import Any

from cortex_viz.core.activity_paths import (
    canonical_file_id_for_legacy,
    canonicalize_path,
    file_target_id,
    is_canonical_file_target_id,
)
from cortex_viz.core.workflow_graph_schema import NodeIdFactory

# Tool → (action verb, target kind, edge kind). The verbs match the Trace
# view's file-edge vocabulary (read/edit/write/run) so both renderers agree.
_FILE_READ = {"Read", "NotebookRead", "Grep", "Glob"}
_FILE_EDIT = {"Edit", "MultiEdit", "NotebookEdit"}
_FILE_WRITE = {"Write"}

# Bash command → leading file path (same regex the Trace source uses to pull
# touched paths out of a shell command). source: trace_source path-ref regex.
_PATH_RE = re.compile(r"(?:^|[\s=])((?:\.{1,2}/|~/|/)[^\s`'\"]{3,})")


def _first_path(text: str) -> str | None:
    m = _PATH_RE.search(text or "")
    return m.group(1) if m else None


def classify(
    tool_name: str, tool_input: dict[str, Any], cwd: str = ""
) -> dict[str, Any]:
    """Map (tool_name, tool_input) → action semantics.

    Returns ``{action, target_id, target_kind, target_label, edge_kind}``.
    Covers the full taxonomy the spec demands: tools, MCP calls, file
    read/edit/write, terminal commands, skills/slash-commands, subagents, web.

    ``cwd`` (the hook event's working directory) resolves ``cwd``-relative or
    ``~``-prefixed paths to the canonical absolute form before minting a FILE
    target id (see ``core.activity_paths.canonicalize_path``) — needed for
    Bash-derived paths, which are frequently relative; Read/Edit/Write
    ``file_path`` arguments are already absolute per Claude Code's own tool
    contract, so canonicalization is a no-op there.
    """
    ti = tool_input or {}
    # MCP call: tool_name is ``mcp__<server>__<tool>`` (or plugin-namespaced
    # ``mcp__plugin_<x>_<server>__<tool>``). The node is the server; the tool
    # is carried as a label so every called MCP is visible.
    if tool_name.startswith("mcp__"):
        rest = tool_name[len("mcp__") :]
        server, _, tool = rest.partition("__")
        return {
            "action": "mcp_call",
            "target_id": f"mcp:{server}",
            "target_kind": "mcp",
            "target_label": f"{server}:{tool}" if tool else server,
            "edge_kind": "call",
        }
    if tool_name in _FILE_READ:
        path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
        return _file_action("read", path or ti.get("pattern") or "?", cwd)
    if tool_name in _FILE_EDIT:
        return _file_action(
            "edit", ti.get("file_path") or ti.get("notebook_path") or "?", cwd
        )
    if tool_name in _FILE_WRITE:
        return _file_action("write", ti.get("file_path") or "?", cwd)
    if tool_name == "Bash":
        cmd = str(ti.get("command") or "")
        raw_cpath = _first_path(cmd)
        return {
            "action": "run",
            "target_id": f"cmd:{cmd[:80]}" if cmd else "cmd:?",
            "target_kind": "command",
            "target_label": cmd[:80] or "?",
            "edge_kind": "run",
            # a touched file, if any — canonicalized so it joins the SAME
            # file-id space as every other FILE target (see module docstring).
            "command_path": canonicalize_path(raw_cpath, cwd) if raw_cpath else None,
        }
    if tool_name == "Skill":
        skill = str(ti.get("skill") or ti.get("name") or "?")
        return {
            "action": "skill",
            "target_id": f"skill:{skill}",
            "target_kind": "skill",
            "target_label": skill,
            "edge_kind": "use",
        }
    if tool_name in {"Task", "Agent"}:
        agent = str(ti.get("subagent_type") or ti.get("description") or "agent")
        return {
            "action": "subagent",
            "target_id": f"agent:{agent}",
            "target_kind": "agent",
            "target_label": agent,
            "edge_kind": "spawn",
        }
    if tool_name in {"WebFetch", "WebSearch"}:
        tgt = str(ti.get("url") or ti.get("query") or "?")
        return {
            "action": "web",
            "target_id": f"web:{tgt[:80]}",
            "target_kind": "web",
            "target_label": tgt[:80],
            "edge_kind": "fetch",
        }
    # Any other tool — keep it; the spec is "ALL tools used".
    return {
        "action": "tool",
        "target_id": f"tool:{tool_name}",
        "target_kind": "tool_hub",
        "target_label": tool_name,
        "edge_kind": "use",
    }


def _file_action(verb: str, path: str, cwd: str) -> dict[str, Any]:
    """``path`` unresolved (e.g. a bare Grep ``pattern`` with no ``path``
    key, sentinel ``"?"``) can't be canonicalized or hashed — kept as a
    literal degenerate id so the action still renders, just outside the
    unified file-id space (no galaxy FILE node exists for it to join
    anyway)."""
    if not path or path == "?":
        return {
            "action": verb,
            "target_id": "file:?",
            "target_kind": "file",
            "target_label": "?",
            "edge_kind": verb,
            "path": None,
        }
    abs_path = canonicalize_path(path, cwd)
    return {
        "action": verb,
        "target_id": file_target_id(path, cwd),
        "target_kind": "file",
        "target_label": abs_path.rsplit("/", 1)[-1] or abs_path,
        "edge_kind": verb,
        # canonical absolute path, threaded through to ``detail`` so
        # downstream consumers needing a REAL filesystem path (the P3 live
        # blast-radius trigger, ``core.wiki_page_actions``) don't have to
        # reverse-engineer it out of the hashed id.
        "path": abs_path,
    }


def normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Raw hook payload → normalized activity row (or None to drop).

    Accepts the PostToolUse-shaped ``{tool_name, tool_input, tool_response,
    cwd, session_id, ts, event_type}``. UserPromptSubmit-shaped events
    (``{prompt|content}``) normalize to a ``prompt`` action. Anything without
    a recognizable action is dropped (returns None) so the spine stays signal.
    """
    etype = event.get("event_type") or event.get("hook_event_name") or ""
    session_id = event.get("session_id") or event.get("sessionId") or "live"
    ts = event.get("ts") or event.get("timestamp")
    cwd = event.get("cwd") or ""

    # Prompt events (UserPromptSubmit) — the spine's roots.
    if etype in {"UserPromptSubmit", "prompt"} or (
        not event.get("tool_name") and (event.get("prompt") or event.get("content"))
    ):
        text = str(event.get("prompt") or event.get("content") or "")[:200]
        if not text:
            return None
        return {
            "session_id": session_id,
            "ts": ts,
            "cwd": cwd,
            "event_type": "prompt",
            "tool": "",
            "action": "prompt",
            "target_id": "",
            "target_kind": "prompt",
            "target_label": text,
            "edge_kind": "",
            "detail": {},
        }

    tool_name = str(event.get("tool_name") or "")
    if not tool_name:
        return None
    c = classify(tool_name, event.get("tool_input") or {}, cwd)
    detail: dict[str, Any] = {}
    if c.get("command_path"):
        detail["command_path"] = c["command_path"]
    if c.get("path"):
        # canonical absolute path for a FILE target (kept alongside the
        # hashed target_id — see ``_file_action``'s docstring on why).
        detail["path"] = c["path"]
    return {
        "session_id": session_id,
        "ts": ts,
        "cwd": cwd,
        "event_type": etype or "PostToolUse",
        "tool": tool_name,
        "action": c["action"],
        "target_id": c["target_id"],
        "target_kind": c["target_kind"],
        "target_label": c["target_label"],
        "edge_kind": c["edge_kind"],
        "detail": detail,
    }


def event_to_graph(row: dict[str, Any]) -> dict[str, list]:
    """Normalized activity row → directional ``{nodes, edges}`` fragment.

    session ──did──▶ action ──edge_kind──▶ target. ``appendGraphDelta`` on the
    client dedups by id, so the session/target nodes coalesce across events
    while each action is unique (id keyed on session + ts/seq).

    Self-heals LEGACY rows (written before the P4 path-unification fix, whose
    ``target_id`` still embeds the raw path — ``file:<raw path>``) into the
    canonical ``file:<hash>`` id on every call, using the row's own ``cwd``
    column. This is why SSE replay (which re-derives a fragment from the
    STORED row on every reconnect) merges old and new activity into the same
    galaxy FILE nodes without a DB migration — see ``core.activity_paths``.
    A row already in canonical shape (the common case going forward) skips
    the recompute; ``is_canonical_file_target_id`` gates it.
    """
    sid = row["session_id"]
    seq = row.get("seq") or row.get("id") or row.get("ts") or "0"
    nodes: list[dict] = [
        {
            "id": f"session:{sid}",
            "kind": "session",
            "label": sid[:12],
            "type": "session",
        },
    ]
    edges: list[dict] = []

    if row["action"] == "prompt":
        pid = f"act:{sid}:{seq}"
        nodes.append(
            {
                "id": pid,
                "kind": "prompt",
                "type": "prompt",
                "label": row["target_label"],
                "tool": "",
            }
        )
        edges.append(
            {
                "id": f"session:{sid}->{pid}",
                "source": f"session:{sid}",
                "target": pid,
                "kind": "did",
                "type": "did",
            }
        )
        return {"nodes": nodes, "edges": edges}

    aid = f"act:{sid}:{seq}"
    nodes.append(
        {
            "id": aid,
            "kind": "action",
            "type": "action",
            "label": row["action"],
            "tool": row["tool"],
        }
    )
    edges.append(
        {
            "id": f"session:{sid}->{aid}",
            "source": f"session:{sid}",
            "target": aid,
            "kind": "did",
            "type": "did",
        }
    )
    tid = row.get("target_id") or ""
    if (
        tid
        and row.get("target_kind") == "file"
        and not is_canonical_file_target_id(tid)
    ):
        tid = canonical_file_id_for_legacy(tid, row.get("cwd") or "")
    if tid:
        target_node = {
            "id": tid,
            "kind": row["target_kind"],
            "type": row["target_kind"],
            "label": row["target_label"],
        }
        if row.get("target_kind") == "file":
            # Absolute path, same shape as the snapshot builder's FILE nodes
            # (workflow_graph_builder_ingest.py:_finalize_files) — without
            # it a live spine FILE node can't drive git-diff lookups, and a
            # first-arrival dedup on the client permanently masks the
            # snapshot node that DOES carry a path (contract A.6).
            path = (row.get("detail") or {}).get("path")
            if path:
                target_node["path"] = path
        nodes.append(target_node)
        edges.append(
            {
                "id": f"{aid}->{tid}",
                "source": aid,
                "target": tid,
                "kind": row["edge_kind"],
                "type": row["edge_kind"],
            }
        )
    # A terminal command that touched a file gets a second directional edge
    # action ──run──▶ file, so shell file-writes are visible too.
    # ``canonicalize_path`` is idempotent on an already-clean absolute path
    # (the post-fix common case), so re-applying it here is a safe no-op for
    # new rows and the self-heal step for legacy rows (whose stored
    # ``command_path`` is still the raw, uncanonicalized token).
    cpath_raw = (row.get("detail") or {}).get("command_path")
    if cpath_raw:
        cpath = canonicalize_path(cpath_raw, row.get("cwd") or "")
        fid = NodeIdFactory.file_id(cpath)
        nodes.append(
            {
                "id": fid,
                "kind": "file",
                "type": "file",
                "label": cpath.rsplit("/", 1)[-1],
                "path": cpath,
            }
        )
        edges.append(
            {
                "id": f"{aid}->{fid}",
                "source": aid,
                "target": fid,
                "kind": "run",
                "type": "run",
            }
        )
    return {"nodes": nodes, "edges": edges}
