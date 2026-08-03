"""Graph response, discussion, persistence, and pure clustering contracts."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from cortex_viz.core import community_detection, session_trace
from cortex_viz.infrastructure import (
    activity_store,
    config,
    conversation_reader,
    profile_store,
    scanner,
)
from cortex_viz.server import (
    build_process,
    graph_cache_state,
    graph_discussions,
    graph_response,
)
from cortex_viz.shared import entity_canonical, project_ids


class _Cursor:
    def __init__(self, *, one=None, many=()):
        self.one = one
        self.many = list(many)
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *args):
        self.executions.append(args)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def _store(cursor):
    connection = _Connection(cursor)
    pool = SimpleNamespace(connection=lambda: connection)
    return SimpleNamespace(batch_pool=pool), connection


def test_activity_store_connection_schema_record_and_queries(monkeypatch):
    with pytest.raises(AttributeError, match="batch_pool"):
        activity_store._conn(object())

    cursor = _Cursor(one={"id": "8"})
    store, connection = _store(cursor)
    activity_store._ensure_table(store)
    assert connection.commits == 1
    monkeypatch.setattr(activity_store, "_ensure_table", lambda _store: None)
    row = {
        "session_id": "",
        "ts": "now",
        "event_type": "tool",
        "tool": "Read",
        "action": "read",
        "target_id": "file:1",
        "target_kind": "file",
        "target_label": "a.py",
        "edge_kind": "reads",
        "cwd": "/repo",
        "detail": {"path": "/repo/a.py"},
    }
    assert activity_store.record_activity(store, row) == 8
    assert connection.commits == 2
    assert json.loads(cursor.executions[-1][1][-1]) == {"path": "/repo/a.py"}

    cursor.many = [{"id": 2, "detail": {}}, {"id": 3, "detail": {}}]
    recent = activity_store.read_recent(store, limit=5, since_id=1)
    assert [item["seq"] for item in recent] == [2, 3]
    assert activity_store.find_by_target_ids(store, []) == []
    assert [
        item["id"] for item in activity_store.find_by_target_ids(store, ["file:1"])
    ] == [
        2,
        3,
    ]

    assert activity_store.find_abs_path_by_label(store, "") is None
    cursor.one = {"abs_path": "/repo/a.py"}
    assert activity_store.find_abs_path_by_label(store, "a.py") == "/repo/a.py"
    assert activity_store.find_abs_path_by_suffix(store, "") is None
    assert activity_store.find_abs_path_by_suffix(store, "src/a_%.py") == "/repo/a.py"
    assert [item["id"] for item in activity_store.scan_legacy_file_rows(store)] == [
        2,
        3,
    ]


def _patch_profile_paths(tmp_path, monkeypatch):
    methodology = tmp_path / "methodology"
    monkeypatch.setattr(profile_store, "METHODOLOGY_DIR", methodology)
    monkeypatch.setattr(profile_store, "DOMAINS_DIR", methodology / "domains")
    monkeypatch.setattr(profile_store, "INDEX_PATH", methodology / "index.json")
    monkeypatch.setattr(profile_store, "PROFILES_PATH", methodology / "profiles.json")
    monkeypatch.setattr(
        profile_store,
        "LEGACY_BACKUP_PATH",
        methodology / "profiles.json.v1_backup",
    )
    return methodology


def test_profile_store_migrates_loads_and_performs_bounded_writes(
    tmp_path, monkeypatch
):
    methodology = _patch_profile_paths(tmp_path, monkeypatch)
    methodology.mkdir()
    legacy = {
        "version": 1,
        "updatedAt": "old",
        "globalStyle": {"tone": "direct"},
        "domains": {"b": {"n": 2}, "a": {"n": 1}, "../bad": {"n": 0}, 3: []},
    }
    profile_store.write_json(profile_store.PROFILES_PATH, legacy)
    loaded = profile_store.load_profiles()
    assert loaded["domains"] == {"a": {"n": 1}, "b": {"n": 2}}
    assert profile_store.LEGACY_BACKUP_PATH.exists()
    assert profile_store.load_profile("../bad") is None
    assert profile_store._domain_path("safe") == profile_store.DOMAINS_DIR / "safe.json"
    with pytest.raises(ValueError, match="unsafe"):
        profile_store._domain_path("bad/name")

    profile_store.save_profile("c", {"n": 3})
    profile_store.save_profile("c", {"n": 4})
    assert profile_store.load_profile("c") == {"n": 4}
    index = profile_store.read_json(profile_store.INDEX_PATH)
    assert index["domain_ids"] == ["a", "b", "c"]
    assert index["updatedAt"].endswith("Z")

    profiles = {
        "version": 2,
        "globalStyle": None,
        "domains": {"ok": {"x": 1}, "bad/name": {"x": 2}, "skip": []},
    }
    profile_store.save_profiles(profiles)
    assert profile_store.load_profiles()["domains"] == {"ok": {"x": 1}}
    profile_store.save_profiles({"domains": []})
    assert profile_store.read_json(profile_store.INDEX_PATH)["domain_ids"] == []

    profile_store.write_json(profile_store.PROFILES_PATH, ["invalid"])
    assert not profile_store._migrate_legacy_if_present()
    profile_store.write_json(profile_store.PROFILES_PATH, {"domains": ["invalid"]})
    assert not profile_store._migrate_legacy_if_present()


class _VitalsStore:
    def count_memories(self):
        return {"total": 5, "episodic": 2, "semantic": 3}

    def get_stage_counts(self):
        return {"consolidated": 4}

    def get_provenance_counts(self):
        return {"perceived": 2, "inferred": 1, "novel": 2}

    def get_avg_heat(self):
        return 1.23456

    def list_procedural_skills(self, **kwargs):
        assert kwargs == {"min_proficiency": 0.0, "limit": 1000}
        return [{"is_habitual": True}, {"is_habitual": False}]

    def count_crystallized_confabulations(self):
        return 2

    def count_habituated_repeats(self):
        raise RuntimeError("degraded")


def test_discussion_params_vitals_cache_page_and_detail(tmp_path, monkeypatch, caplog):
    assert graph_discussions.parse_discussion_params("/api/discussions") == {
        "project": None,
        "batch": 0,
        "batch_size": 500,
    }
    params = graph_discussions.parse_discussion_params(
        "/api/discussions?project=p&batch=bad&batch=2&batch_size=bad&batch_size=1"
    )
    assert params == {"project": "p", "batch": 2, "batch_size": 1}
    assert graph_discussions._optional_vital(object(), "missing", 7) == 7

    vitals = graph_discussions._compute_memory_vitals(_VitalsStore())
    assert vitals["mean_heat"] == 1.2346
    assert vitals["procedural_skills"] == 2
    assert vitals["habitual_skills"] == 1
    assert vitals["provenance"]["unknown"] == 2
    assert vitals["habituated_repeats"] == 0
    assert "degraded" in caplog.text

    conversations = [
        {"sessionId": "s1", "project": "p", "startedAt": "2026-01-02"},
        {"sessionId": "s2", "project": "q", "startedAt": "2026-01-01"},
    ]
    cache = {"value": None, "ts": 0.0}
    monkeypatch.setattr(
        graph_discussions,
        "get_cached_conversations_state",
        lambda: (cache["value"], cache["ts"]),
    )
    monkeypatch.setattr(
        graph_discussions,
        "set_cached_conversations_state",
        lambda value, ts: cache.update(value=value, ts=ts),
    )
    monkeypatch.setattr(scanner, "discover_conversations", lambda: conversations)
    monkeypatch.setattr(graph_discussions.time, "time", lambda: 100.0)
    assert graph_discussions._get_cached_conversations() is conversations
    assert graph_discussions._get_cached_conversations() is conversations

    from cortex_viz.core import graph_builder_discussions

    monkeypatch.setattr(
        graph_builder_discussions,
        "build_discussion_nodes",
        lambda page, hubs: ([item["sessionId"] for item in page], list(hubs)),
    )
    graph_cache_state._cached_domain_hub_ids = {"domain:p"}
    page = graph_discussions.build_discussions_response(
        "/api/discussions?project=p&batch_size=1"
    )
    assert page["nodes"] == ["s1"]
    assert page["meta"]["total"] == 1

    project = tmp_path / "projects/p"
    project.mkdir(parents=True)
    transcript = project / "s1.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    (tmp_path / "projects/file").write_text("skip", encoding="utf-8")
    monkeypatch.setattr(config, "CLAUDE_DIR", tmp_path)
    assert graph_discussions._find_session_file("missing") is None
    assert graph_discussions._find_session_file("s1") == transcript
    assert graph_discussions.build_discussion_detail("absent")["error"] == (
        "Discussion not found"
    )
    monkeypatch.setattr(graph_discussions, "_find_session_file", lambda _sid: None)
    assert graph_discussions.build_discussion_detail("s1")["error"] == (
        "Session file not found"
    )
    monkeypatch.setattr(
        graph_discussions, "_find_session_file", lambda _sid: transcript
    )
    monkeypatch.setattr(
        conversation_reader, "read_full_conversation", lambda _path: [1]
    )
    monkeypatch.setattr(
        conversation_reader,
        "format_conversation_messages",
        lambda raw: ["formatted", *raw],
    )
    detail = graph_discussions.build_discussion_detail("s1")
    assert detail["messages"] == ["formatted", 1]


def test_graph_response_cache_progress_and_child_process_routing(monkeypatch):
    assert graph_response.parse_graph_query("/api/graph") == {
        "domain_filter": None,
        "batch": 0,
        "batch_size": 0,
    }
    assert graph_response.parse_graph_query(
        "/api/graph?domain=x&batch=bad&batch_size=bad&batch=2&batch_size=3"
    ) == {"domain_filter": "x", "batch": 2, "batch_size": 3}
    monkeypatch.setattr(graph_response, "_roster_fingerprint", lambda: "current")
    monkeypatch.setattr(graph_response, "get_build_progress", lambda: {"pct": 0.5})
    starts = []
    kills = []
    monkeypatch.setattr(build_process, "start_build", lambda *args: starts.append(args))
    monkeypatch.setattr(build_process, "kill_current_build", lambda: kills.append(1))

    graph_cache_state._graph_roster_fingerprint = "current"
    graph_cache_state._graph_cache = None
    monkeypatch.setattr(build_process, "_is_alive", lambda: True)
    placeholder = graph_response.get_graph_response(
        SimpleNamespace(_url="db"), "/api/graph"
    )
    assert placeholder["meta"]["stage"] == "building"

    data = {"nodes": [1], "edges": []}
    graph_cache_state._graph_cache = {"data": data}
    assert (
        graph_response.get_graph_response(SimpleNamespace(_url="db"), "/api/graph")
        is data
    )

    monkeypatch.setattr(build_process, "_is_alive", lambda: False)
    graph_cache_state._graph_roster_fingerprint = "old"
    assert (
        graph_response.get_graph_response(
            SimpleNamespace(_url="db"), "/api/graph?domain=x"
        )
        is data
    )
    assert kills == [1] and starts == [("db", "x")]

    graph_cache_state._graph_cache = None
    graph_cache_state._graph_roster_fingerprint = "current"
    graph_response.get_graph_response(SimpleNamespace(_url=None), "/api/graph")
    assert starts == [("db", "x")]


def test_community_detection_resolution_degrade_partition_and_singletons(
    monkeypatch,
):
    assert community_detection._resolve_resolution(0.1) == 0.1
    monkeypatch.setenv("CORTEX_VIZ_COMMUNITY_RESOLUTION", "bad")
    assert community_detection._resolve_resolution(None) == (
        community_detection.DEFAULT_RESOLUTION
    )
    monkeypatch.setitem(sys.modules, "igraph", None)
    monkeypatch.setitem(sys.modules, "leidenalg", None)
    assert community_detection.detect_communities([("a", "b", 1.0)]) == {}

    class Graph:
        def __init__(self, *, n, directed):
            assert (n, directed) == (2, False)
            self.edges = []

        def add_edges(self, edges):
            self.edges.extend(edges)

    fake_igraph = SimpleNamespace(Graph=Graph)
    fake_leiden = SimpleNamespace(
        CPMVertexPartition=object(),
        find_partition=lambda graph, _kind, **kwargs: SimpleNamespace(
            membership=[0, 0]
        ),
    )
    monkeypatch.setitem(sys.modules, "igraph", fake_igraph)
    monkeypatch.setitem(sys.modules, "leidenalg", fake_leiden)
    mapping = community_detection.detect_communities(
        [("b", "a", 0), ("a", "a", 1), (None, "b", 1)],
        node_ids=["a", "b", "isolated"],
        resolution=0.2,
        seed=7,
    )
    assert mapping == {"a": 0, "b": 0, "isolated": 1}


def test_session_trace_extract_labels_chain_branches_and_continuation(tmp_path):
    assert session_trace.extract_file_refs(
        "Read", {"file_path": "a.py", "path": "b.py"}
    ) == [
        ("read", "a.py"),
        ("read", "b.py"),
    ]
    assert session_trace.extract_file_refs(
        "Bash", {"command": "cat ./a.py && head ./a.py; tail /tmp/b.py"}
    ) == [("run", "./a.py"), ("run", "/tmp/b.py")]
    assert (
        session_trace._action_label("Task", {"subagent_type": "explorer"}) == "explorer"
    )
    assert session_trace._action_label("Unknown", {}) == "Unknown"
    file_node = session_trace._file_node("./a.py", str(tmp_path))
    assert file_node["label"] == "a.py" and file_node["drillable"]

    events = [
        {"kind": "unknown"},
        {"kind": "prompt", "text": ""},
        {"kind": "prompt", "text": "Question", "ts": "1"},
        {
            "kind": "action",
            "tool": "Read",
            "input": {"file_path": "./a.py"},
            "cwd": str(tmp_path),
            "ts": "2",
        },
        {"kind": "discussion", "text": "Explanation", "ts": "3"},
        {"kind": "discussion", "text": "", "ts": "3"},
        {"kind": "memory", "op": "recall", "text": "history", "ts": "4"},
        {"kind": "action", "name": "Bash", "input": {}, "ts": "5"},
    ]
    chain = session_trace.build_chain(events, "sid")
    assert chain["next_since"] == 5
    assert {node["kind"] for node in chain["nodes"]} == {
        "prompt",
        "action",
        "file",
        "discussion",
        "memory",
    }
    assert {edge["kind"] for edge in chain["edges"]} >= {
        "step",
        "next",
        "read",
        "discusses",
        "remembers",
    }
    tail = session_trace.build_chain(events, "sid", since=3)
    assert all(node.get("seq", 3) >= 3 for node in tail["nodes"])


def test_cross_platform_project_ids_and_entity_canonicalization():
    assert project_ids._is_windows_path("C:\\Users\\Ada")
    assert not project_ids._is_windows_path("/Users/Ada")
    assert project_ids._gitbash_to_windows("/c/users/Ada") == "c:/users/Ada"
    assert project_ids._gitbash_to_windows("/Users/Ada") is None
    assert project_ids._windows_slug("C:\\Users\\Ada") == "c--users-ada"
    assert project_ids.cwd_to_project_id(None) is None
    assert project_ids.cwd_to_project_id("C:\\Users\\Ada") == "c--users-ada"
    assert project_ids.cwd_to_project_id("/c/users/Ada") == "c--users-ada"
    assert project_ids.cwd_to_project_id("/Users/Ada/repo") == "-Users-Ada-repo"
    assert project_ids.cwd_to_project_id("-Users-Ada-repo") == "-Users-Ada-repo"
    assert project_ids.project_id_to_label(None) == "Unknown"
    assert project_ids.project_id_to_label("-Users-Ada-Developments-Cortex") == "Cortex"
    assert project_ids.project_id_to_label("---") == "---"
    assert project_ids.domain_id_from_label(None) == ""
    assert project_ids.domain_id_from_label(" Cortex & Viz ") == "cortex-viz"

    expected = {
        "": "",
        "   ": "",
        "42": "42",
        "HTTP": "HTTP",
        "OUTPUT": "Output",
        "HTTP_CLIENT": "Http_Client",
        "FilePath": "FilePath",
        "file_path": "file_path",
    }
    assert {
        name: entity_canonical.canonicalize_entity_name(name) for name in expected
    } == expected
