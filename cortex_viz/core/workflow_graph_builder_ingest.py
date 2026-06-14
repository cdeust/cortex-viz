"""Per-source node-ingest helpers for ``WorkflowGraphBuilder``.

Split out of ``workflow_graph_builder.py`` (was 593 lines) to respect the
500-line file limit. Pure core, no I/O. Each function takes the builder
instance as its first argument (same convention as
``workflow_graph_builder_relational``) and mutates its node/edge state.
The builder exposes thin method wrappers that delegate here so the
streaming-build dispatch table (``self._ingest_*``) is unchanged.
"""

from __future__ import annotations

from cortex_viz.core.workflow_graph_palette import (
    AGENT_COLOR,
    COMMAND_COLOR,
    DISCUSSION_COLOR,
    HOOK_COLOR,
    MEMORY_STAGE_COLORS,
    SKILL_COLOR,
    classify_primary_tool,
    primary_tool_color,
)
from cortex_viz.core.workflow_graph_schema import (
    GLOBAL_DOMAIN_ID,
    EdgeKind,
    NodeIdFactory,
    NodeKind,
    ToolKind,
    WorkflowEdge,
    WorkflowNode,
)

_TOOL_NAME_TO_ENUM = {t.value: t for t in ToolKind}
_TOOL_NAME_LOWER = {t.value.lower(): t for t in ToolKind}

# Scientific-measurement fields forwarded verbatim to memory nodes so
# the Knowledge / Board cards can render them without a second PG hop.
_MEMORY_SCIENTIFIC_KEYS = tuple(
    (
        "heat_base arousal emotional_valence dominant_emotion importance "
        "surprise_score confidence access_count useful_count replay_count "
        "reconsolidation_count plasticity stability excitability "
        "hippocampal_dependency schema_match_score schema_id separation_index "
        "interference_score encoding_strength hours_in_stage stage_entered_at "
        "last_accessed no_decay is_protected is_stale is_benchmark is_global "
        "store_type compression_level compressed"
    ).split()
)


def _require(rec: dict, key: str, ctx: str):
    if key not in rec or rec[key] is None:
        raise ValueError(f"{ctx}: missing key {key!r} in {rec!r}")
    return rec[key]


def _as_tool(name: str) -> ToolKind:
    if name in _TOOL_NAME_TO_ENUM:
        return _TOOL_NAME_TO_ENUM[name]
    low = name.lower()
    if low in _TOOL_NAME_LOWER:
        return _TOOL_NAME_LOWER[low]
    raise ValueError(f"unknown ToolKind: {name!r}")


def _ingest_tool_event(b, ev):
    tool = _as_tool(str(_require(ev, "tool", "tool_event")))
    dom = b._assign_domain(ev.get("domain"))
    b._ensure_domain(dom)
    b._build_tool_hubs(dom, [tool])
    path = ev.get("file_path")
    if not path:
        return
    count = int(ev.get("count") or 1)
    b._file_tool_counts[path][tool] += count
    b._file_domains[path].add(dom)
    b._track_file_timestamp(path, tool, ev)
    b._edges.append(
        WorkflowEdge(
            source=NodeIdFactory.tool_hub_id(dom, tool),
            target=NodeIdFactory.file_id(path),
            kind=EdgeKind.TOOL_USED_FILE,
            weight=float(count),
        )
    )

def _track_file_timestamp(b, path: str, tool: ToolKind, ev: dict) -> None:
    """Accumulate per-file first_seen / last_accessed / last_modified.

    first_seen  = earliest access of any kind.
    last_accessed = latest access of any kind (incl. Read/Grep/Glob).
    last_modified = latest Edit or Write access only.
    """
    first_ts = ev.get("first_ts")
    last_ts = ev.get("last_ts")
    if not first_ts and not last_ts:
        return
    slot = b._file_timestamps.setdefault(
        path,
        {"first_seen": None, "last_accessed": None, "last_modified": None},
    )
    if first_ts and (slot["first_seen"] is None or first_ts < slot["first_seen"]):
        slot["first_seen"] = first_ts
    if last_ts and (
        slot["last_accessed"] is None or last_ts > slot["last_accessed"]
    ):
        slot["last_accessed"] = last_ts
    if tool in (ToolKind.EDIT, ToolKind.WRITE) and last_ts:
        if slot["last_modified"] is None or last_ts > slot["last_modified"]:
            slot["last_modified"] = last_ts

def _finalize_files(b):
    for path, tc in b._file_tool_counts.items():
        cluster = classify_primary_tool(dict(tc))
        fid = NodeIdFactory.file_id(path)
        doms = sorted(b._file_domains[path])
        if not doms:
            raise ValueError(f"file {path} has no domain membership")
        ts = b._file_timestamps.get(path, {})
        node = WorkflowNode(
            id=fid,
            kind=NodeKind.FILE,
            label=path.rsplit("/", 1)[-1] or path,
            color=primary_tool_color(cluster),
            domain_id=doms[0],
            size=1.5,
            primary_cluster=cluster,
            path=path,
            extra_domain_ids=doms[1:],
            first_seen=ts.get("first_seen"),
            last_accessed=ts.get("last_accessed"),
            last_modified=ts.get("last_modified"),
        )
        b._nodes[fid] = node
        b._node_order.append(node)
        for d in doms:
            b._edges.append(b._in_domain(fid, d))

def _ingest_memory(b, mem):
    pg_id = _require(mem, "id", "memory")
    dom = b._assign_domain(mem.get("domain"))
    b._ensure_domain(dom)
    stage = mem.get("consolidation_stage") or mem.get("stage") or "episodic"
    heat = float(mem.get("heat") or mem.get("heat_base") or 0.0)
    content = mem.get("content") or ""
    tags = mem.get("tags") if isinstance(mem.get("tags"), list) else []
    science = {
        k: mem[k]
        for k in _MEMORY_SCIENTIFIC_KEYS
        if k in mem and mem[k] is not None
    }
    b._add_child(
        NodeIdFactory.memory_id(pg_id),
        NodeKind.MEMORY,
        content[:60].replace("\n", " ") or f"memory {pg_id}",
        MEMORY_STAGE_COLORS.get(stage, MEMORY_STAGE_COLORS["episodic"]),
        dom,
        1.0 + min(3.0, heat * 3.0),
        stage=stage,
        body=content[:4000] if content else None,
        heat=heat,
        tags=[str(t) for t in tags][:20],
        created_at=mem.get("created_at"),
        **science,
    )

def _ingest_discussion(b, dc):
    sid = str(_require(dc, "session_id", "discussion"))
    dom = b._assign_domain(dc.get("domain"))
    b._ensure_domain(dom)
    mc = int(dc.get("message_count") or 0)
    b._add_child(
        f"discussion:{sid}",
        NodeKind.DISCUSSION,
        dc.get("title") or sid[:8],
        DISCUSSION_COLOR,
        dom,
        1.0 + min(3.0, mc * 0.02),
        session_id=sid,
        count=mc,
        started_at=dc.get("started_at"),
        last_activity=dc.get("last_activity"),
        duration_ms=dc.get("duration_ms"),
    )

def _ingest_skill(b, sk):
    name = str(_require(sk, "name", "skill"))
    path = str(_require(sk, "path", "skill"))
    doms = [b._assign_domain(d) for d in (sk.get("domains") or [])] or [
        GLOBAL_DOMAIN_ID
    ]
    for d in doms:
        b._ensure_domain(d)
    node_id = NodeIdFactory.skill_id(name)
    b._add_child(
        node_id,
        NodeKind.SKILL,
        name,
        SKILL_COLOR,
        doms[0],
        2.0,
        path=path,
        extra_domain_ids=doms[1:],
        body=sk.get("body"),
    )
    for d in doms:
        b._edges.append(
            WorkflowEdge(
                source=d,
                target=node_id,
                kind=EdgeKind.INVOKED_SKILL,
            )
        )

def _ingest_hook(b, hk):
    event = str(_require(hk, "event", "hook"))
    cmd = str(_require(hk, "command", "hook"))
    dom = b._assign_domain(hk.get("domain"))
    b._ensure_domain(dom)
    node_id = NodeIdFactory.hook_id(event, cmd)
    matcher = hk.get("matcher") or ""
    label = f"{event}:{matcher}" if matcher else event
    if not b._add_child(
        node_id, NodeKind.HOOK, label, HOOK_COLOR, dom, 1.5, path=cmd, event=event
    ):
        return
    b._edges.append(
        WorkflowEdge(
            source=dom,
            target=node_id,
            kind=EdgeKind.TRIGGERED_HOOK,
            label=event,
        )
    )

def _ingest_agent(b, ag):
    sub = str(_require(ag, "subagent_type", "agent"))
    dom = b._assign_domain(ag.get("domain"))
    b._ensure_domain(dom)
    b._build_tool_hubs(dom, [ToolKind.TASK])
    hub = NodeIdFactory.tool_hub_id(dom, ToolKind.TASK)
    node_id = NodeIdFactory.agent_id(dom, sub)
    count = int(ag.get("count") or 1)
    b._add_child(
        node_id,
        NodeKind.AGENT,
        sub,
        AGENT_COLOR,
        dom,
        2.0,
        subagent_type=sub,
        count=count,
    )
    b._edges.append(
        WorkflowEdge(
            source=hub,
            target=node_id,
            kind=EdgeKind.SPAWNED_AGENT,
            weight=float(count),
        )
    )

def _ingest_command(b, cm):
    cmd = str(_require(cm, "cmd", "command"))
    h = str(_require(cm, "cmd_hash", "command"))
    dom = b._assign_domain(cm.get("domain"))
    b._ensure_domain(dom)
    b._build_tool_hubs(dom, [ToolKind.BASH])
    hub = NodeIdFactory.tool_hub_id(dom, ToolKind.BASH)
    node_id = NodeIdFactory.command_id(h)
    count = int(cm.get("count") or 1)
    if not b._add_child(
        node_id,
        NodeKind.COMMAND,
        cmd[:80],
        COMMAND_COLOR,
        dom,
        1.0 + min(3.0, count * 0.1),
        body=cmd,
        count=count,
        first_seen=cm.get("first_ts"),
        last_accessed=cm.get("last_ts"),
    ):
        return
    # Bash hub → command containment. Uses COMMAND_IN_HUB (not
    # TOOL_USED_FILE) so workflow_graph_panel.js renderToolHub's
    # "Files touched" counter isn't inflated by the command count.
    b._edges.append(
        WorkflowEdge(
            source=hub,
            target=node_id,
            kind=EdgeKind.COMMAND_IN_HUB,
            weight=float(count),
        )
    )