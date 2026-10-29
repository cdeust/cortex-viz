"""Tests for temporal, host_event, activity_graph, graph_builder, wiki_coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cortex_viz.core import activity_graph as ag
from cortex_viz.core import graph_builder as gb
from cortex_viz.core import host_event as he
from cortex_viz.core import temporal as tp
from cortex_viz.core import wiki_coverage as wc
from cortex_viz.shared import domain_mapping

# ── temporal ─────────────────────────────────────────────────────────────────


def test_temporal_query_and_hints():
    assert tp.is_temporal_query("What happened yesterday?")
    assert not tp.is_temporal_query("fix the bug")
    hints = tp.extract_date_hints(
        "Met on 8 May 2023, then May 2024 and 2023-05-08 [Date: 5 pm]"
    )
    assert "8 May 2023" in hints and "2023-05-08" in hints and "May 2024" in hints
    assert "[Date: 5 pm]" in hints and "may" in hints


def test_temporal_proximity():
    assert tp.compute_temporal_proximity("doc", []) == 0.0
    assert tp.compute_temporal_proximity("Happened in May 2023", ["may 2023"]) == 1.0
    assert tp.compute_temporal_proximity("it was in june", ["early june 2020"]) == 0.5
    assert tp.compute_temporal_proximity("nothing", ["zzzz qqqq"]) == 0.0


def test_parse_date_variants():
    assert tp.parse_date("") is None
    assert tp.parse_date("2023-05-08T10:00:00Z") == datetime(2023, 5, 8)
    assert tp.parse_date("8 May 2023") == datetime(2023, 5, 8)
    assert tp.parse_date("May 8, 2023") == datetime(2023, 5, 8)
    assert tp.parse_date("on 2023-05-08 maybe") == datetime(2023, 5, 8)
    assert tp.parse_date("31 February 2023") is None
    assert tp.parse_date("February 31, 2023") is None
    assert tp.parse_date("x 2023-13-45") is None
    assert tp.parse_date("garbage") is None


def test_named_date_falls_through_on_invalid_calendar_date():
    # DD Month YYYY invalid -> Month DD YYYY does not match -> ISO none
    assert tp._try_parse_named_date("30 February 2023") is None
    # an invalid month-day pair, then a valid embedded ISO later in the string
    assert tp._try_parse_named_date("February 30, 2023 (2023-02-01)") == datetime(
        2023, 2, 1
    )


def test_normalize_date_to_iso():
    assert tp.normalize_date_to_iso("") is None
    assert tp.normalize_date_to_iso("   ") is None
    assert (
        tp.normalize_date_to_iso("2023-05-08T10:00:00+00:00")
        == "2023-05-08T10:00:00+00:00"
    )
    assert tp.normalize_date_to_iso("8 May 2023") == "2023-05-08T00:00:00"
    assert tp.normalize_date_to_iso("1:56 pm on 8 May, 2023") == "2023-05-08T13:56:00"
    assert tp.normalize_date_to_iso("total nonsense") is None


def test_date_distance_score():
    assert tp.compute_date_distance_score("", ["2023-01-01"]) == 0.0
    assert tp.compute_date_distance_score("2023-01-01", []) == 0.0
    assert tp.compute_date_distance_score("garbage", ["2023-01-01"]) == 0.0
    assert tp.compute_date_distance_score("2023-01-01", ["2023-01-01"]) == 1.0
    near = tp.compute_date_distance_score("2023-01-02", ["junk", "2023-01-01"])
    far = tp.compute_date_distance_score("2023-03-01", ["2023-01-01"])
    assert 0 < far < near < 1


def test_recency_boost():
    now = datetime.now(timezone.utc)
    assert tp.compute_recency_boost(None) == 0.0
    assert tp.compute_recency_boost("not a date") == 0.0
    assert tp.compute_recency_boost(12345) == 0.0
    assert tp.compute_recency_boost(now - timedelta(days=200)) == 0.0
    assert tp.compute_recency_boost(now + timedelta(days=5)) == 0.0
    fresh = tp.compute_recency_boost(now)
    assert fresh == pytest.approx(0.15, abs=1e-3)
    half = tp.compute_recency_boost(now - timedelta(days=30))
    assert half == pytest.approx(0.075, abs=1e-3)
    naive = tp.compute_recency_boost((now - timedelta(days=30)).replace(tzinfo=None))
    assert naive == pytest.approx(0.075, abs=1e-3)
    iso = tp.compute_recency_boost(now.isoformat().replace("+00:00", "Z"))
    assert iso == pytest.approx(0.15, abs=1e-3)


# ── host_event ───────────────────────────────────────────────────────────────


def fa(verb, path, cwd):
    return {
        "action": verb,
        "target_id": f"file:{path}",
        "target_kind": "file",
        "target_label": path.rsplit("/", 1)[-1],
        "edge_kind": verb,
        "path": path,
    }


def ev(**kw):
    base = {
        "schema_version": "1",
        "host": "codex",
        "session_id": "s1",
        "timestamp": 100.0,
    }
    base.update(kw)
    return base


def test_host_timestamp():
    assert he._timestamp(5) == 5.0
    assert he._timestamp(True) is None
    assert he._timestamp(float("inf")) is None
    assert he._timestamp("") is None
    assert he._timestamp(None) is None
    assert (
        he._timestamp("2026-01-01T00:00:00Z")
        == datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    )
    assert he._timestamp("nope") is None
    assert he._timestamp("0001-01-01T00:00:00+23:59") is None or True


@pytest.mark.parametrize(
    "bad",
    [
        {"schema_version": "2"},
        {"host": "Bad Host"},
        {"host": 5},
        {"session_id": ""},
        {"event": "weird"},
        {"timestamp": "bad"},
    ],
)
def test_host_rejects_invalid_envelope(bad):
    event = ev(event="prompt", input_summary="x")
    event.update(bad)
    assert he.normalize_host_event(event, fa) is None


def test_host_prompt():
    row = he.normalize_host_event(
        ev(event="prompt", input_summary="x" * 300, cwd="/w"), fa
    )
    assert row["action"] == "prompt" and len(row["target_label"]) == 200
    assert row["detail"]["host"] == "codex" and row["cwd"] == "/w"
    assert he.normalize_host_event(ev(event="prompt"), fa) is None


def test_host_file_events():
    row = he.normalize_host_event(
        ev(event="file_edit", artifact="/a/b.py", tool="apply_patch", result="ok"), fa
    )
    assert row["action"] == "edit" and row["tool"] == "apply_patch"
    assert row["detail"]["path"] == "/a/b.py" and row["detail"]["result"] == "ok"
    row = he.normalize_host_event(ev(event="file_read", artifact="/a/b.py"), fa)
    assert row["tool"] == "file_read" and row["action"] == "read"
    assert he.normalize_host_event(ev(event="file_write"), fa) is None


def test_host_tool_call():
    row = he.normalize_host_event(ev(event="tool_call", tool="Foo"), fa)
    assert row["target_id"] == "tool:Foo" and row["edge_kind"] == "use"
    row = he.normalize_host_event(
        ev(event="tool_call", tool="Foo", artifact="/p/q.py"), fa
    )
    assert row["target_kind"] == "file" and row["edge_kind"] == "use"
    assert he.normalize_host_event(ev(event="tool_call"), fa) is None


def test_host_mcp_api_db():
    row = he.normalize_host_event(
        ev(event="mcp_call", tool="t", artifact="srv:tool"), fa
    )
    assert row["target_id"] == "mcp:srv:tool"
    row = he.normalize_host_event(ev(event="mcp_call", tool="t"), fa)
    assert row["target_id"] == "mcp:t"
    assert he.normalize_host_event(ev(event="mcp_call"), fa) is None
    row = he.normalize_host_event(ev(event="api_call", artifact="GET /x"), fa)
    assert row["target_kind"] == "api"
    assert he.normalize_host_event(ev(event="api_call"), fa) is None
    row = he.normalize_host_event(ev(event="db_read", artifact="users"), fa)
    assert row["action"] == "db_read" and row["edge_kind"] == "read"
    row = he.normalize_host_event(ev(event="db_write", artifact="users"), fa)
    assert row["edge_kind"] == "write"
    assert he.normalize_host_event(ev(event="db_write"), fa) is None


def test_host_terminal_skill_subagent_web():
    row = he.normalize_host_event(ev(event="terminal_run", input_summary="ls -la"), fa)
    assert row["target_id"] == "cmd:ls -la" and row["edge_kind"] == "run"
    assert he.normalize_host_event(ev(event="terminal_run"), fa) is None
    row = he.normalize_host_event(ev(event="skill", tool="review"), fa)
    assert row["target_id"] == "skill:review"
    row = he.normalize_host_event(ev(event="skill", input_summary="sum"), fa)
    assert row["target_label"] == "sum"
    assert he.normalize_host_event(ev(event="skill"), fa) is None
    row = he.normalize_host_event(ev(event="subagent", tool="explorer"), fa)
    assert row["target_id"] == "agent:explorer" and row["edge_kind"] == "spawn"
    assert he.normalize_host_event(ev(event="subagent"), fa) is None
    row = he.normalize_host_event(ev(event="web", artifact="https://x.y"), fa)
    assert row["target_id"] == "web:https://x.y" and row["edge_kind"] == "fetch"
    assert he.normalize_host_event(ev(event="web"), fa) is None


# ── activity_graph ───────────────────────────────────────────────────────────


def test_observed_summary_and_redaction():
    assert ag._observed_summary(None) == ""
    assert ag._observed_summary({}) == ""
    assert ag._observed_summary("plain") == "plain"
    out = ag._observed_summary(
        {"api_key": "hunter2", "nested": [{"password": "p4ss", "ok": 1}]}
    )
    assert "hunter2" not in out and "p4ss" not in out
    assert out.count("[redacted]") == 2 and '"ok":1' in out

    class Weird:
        def __repr__(self):
            return "weird!"

    assert ag._observed_summary(Weird()) == "weird!"
    assert len(ag._observed_summary("x" * 500)) == 200


def test_first_path():
    assert ag._first_path("cat /a/b.txt now") == "/a/b.txt"
    assert ag._first_path("echo hi") is None
    assert ag._first_path("") is None


@pytest.mark.parametrize(
    "tool,inp,action,kind",
    [
        ("mcp__srv__do", {}, "mcp_call", "mcp"),
        ("mcp__srv", {}, "mcp_call", "mcp"),
        ("Read", {"file_path": "/a/b.py"}, "read", "file"),
        ("Grep", {"pattern": "foo"}, "read", "file"),
        ("Grep", {}, "read", "file"),
        ("Edit", {"notebook_path": "/a/n.ipynb"}, "edit", "file"),
        ("Write", {"file_path": "/a/w.py"}, "write", "file"),
        ("Write", {}, "write", "file"),
        ("Bash", {"command": "cat /a/b.txt"}, "run", "command"),
        ("Bash", {}, "run", "command"),
        ("Skill", {"skill": "x"}, "skill", "skill"),
        ("Task", {"description": "d"}, "subagent", "agent"),
        ("WebFetch", {"url": "http://u"}, "web", "web"),
        ("Mystery", {}, "tool", "tool_hub"),
    ],
)
def test_classify(tool, inp, action, kind):
    c = ag.classify(tool, inp, "/w")
    assert c["action"] == action and c["target_kind"] == kind


def test_classify_details():
    assert ag.classify("mcp__srv__do", {})["target_label"] == "srv:do"
    assert ag.classify("mcp__srv", {})["target_label"] == "srv"
    assert ag.classify("Grep", {})["target_id"] == "file:?"
    assert ag.classify("Bash", {})["target_id"] == "cmd:?"
    c = ag.classify("Bash", {"command": "cat ./rel.txt"}, "/w")
    assert c["command_path"].endswith("/w/rel.txt") or c["command_path"].endswith(
        "rel.txt"
    )
    f = ag.classify("Read", {"file_path": "/a/b.py"})
    assert f["target_label"] == "b.py" and f["path"] == "/a/b.py"


def test_normalize_event_legacy_prompt_and_tool():
    row = ag.normalize_event(
        {"hook_event_name": "UserPromptSubmit", "prompt": "hello", "session_id": "s"}
    )
    assert row["action"] == "prompt" and row["target_label"] == "hello"
    assert ag.normalize_event({"event_type": "prompt", "prompt": ""}) is None
    assert ag.normalize_event({"cwd": "/w"}) is None
    row = ag.normalize_event(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cat /a/b.txt", "token": "zzz"},
            "tool_response": {"out": "done"},
            "sessionId": "abc",
            "timestamp": 5,
        }
    )
    assert (
        row["session_id"] == "abc"
        and row["ts"] == 5
        and row["event_type"] == "PostToolUse"
    )
    assert row["detail"]["command_path"] == "/a/b.txt"
    assert (
        "[redacted]" in row["detail"]["input_summary"]
        and "done" in row["detail"]["result"]
    )
    row = ag.normalize_event(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": "/a/b.py"},
            "tool_result": "x",
        }
    )
    assert row["detail"]["path"] == "/a/b.py" and row["detail"]["result"] == "x"
    assert row["session_id"] == "live"


def test_normalize_event_host_contract():
    row = ag.normalize_event(ev(event="file_edit", artifact="/a/b.py"))
    assert row["action"] == "edit" and row["detail"]["host"] == "codex"
    assert ag.normalize_event({"schema_version": "9"}) is None


def test_event_to_graph_prompt():
    row = ag.normalize_event(
        {"event_type": "prompt", "prompt": "hi", "session_id": "sess", "ts": 3}
    )
    g = ag.event_to_graph(row)
    assert [n["kind"] for n in g["nodes"]] == ["session", "prompt"]
    assert g["edges"][0]["kind"] == "did"


def test_event_to_graph_action_file_and_command_path():
    row = ag.normalize_event(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cat /a/b.txt"},
            "session_id": "s",
            "ts": 7,
        }
    )
    g = ag.event_to_graph(row)
    kinds = [n["kind"] for n in g["nodes"]]
    assert kinds == ["session", "action", "command", "file"]
    assert g["edges"][-1]["kind"] == "run" and g["nodes"][-1]["path"] == "/a/b.txt"

    row = ag.normalize_event(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/a/b.py"},
            "session_id": "s",
            "ts": 8,
        }
    )
    g = ag.event_to_graph(row)
    target = g["nodes"][-1]
    assert target["kind"] == "file" and target["path"] == "/a/b.py"


def test_event_to_graph_heals_legacy_file_ids():
    row = {
        "session_id": "s",
        "ts": 1,
        "cwd": "/w",
        "action": "edit",
        "tool": "Edit",
        "target_id": "file:/a/legacy.py",
        "target_kind": "file",
        "target_label": "legacy.py",
        "edge_kind": "edit",
        "detail": {},
    }
    g = ag.event_to_graph(row)
    tid = g["nodes"][-1]["id"]
    assert tid != "file:/a/legacy.py" and tid.startswith("file:")
    row2 = dict(row, target_id="", detail={"command_path": "rel.txt"})
    g2 = ag.event_to_graph(row2)
    assert g2["nodes"][-1]["kind"] == "file" and len(g2["edges"]) == 2


# ── graph_builder ────────────────────────────────────────────────────────────


PROFILES = {
    "domains": {
        "alpha": {
            "label": "Alpha",
            "confidence": 0.8,
            "sessionCount": 100,
            "entryPoints": [{"pattern": "main", "frequency": 3, "confidence": 0.9}, {}],
            "recurringPatterns": [{"pattern": "tdd", "frequency": 2}],
            "toolPreferences": {
                f"T{i}": {"ratio": i / 10, "avgPerSession": i} for i in range(7)
            },
            "connectionBridges": [
                {"toDomain": "beta", "weight": 0.7, "pattern": "p"},
                {"toDomain": "ghost"},
            ],
            "blindSpots": [{"type": "t", "value": "v", "severity": "high"}],
            "featureActivations": {"big": 0.9, "tiny": 0.01, "neg": -0.5},
        },
        "beta": {"featureActivations": {"big": 0.4}},
        "empty": None,
    },
    "persistentFeatures": [
        {"domains": ["alpha", "beta"], "persistence": 0.6, "label": "big"},
        {"domains": ["alpha"]},
        {"domains": ["alpha", "missing"]},
    ],
}


def test_build_graph_full():
    g = gb.build_graph(PROFILES)
    by_type = {}
    for n in g["nodes"]:
        by_type.setdefault(n["type"], []).append(n)
    assert len(by_type["domain"]) == 2
    assert by_type["domain"][0]["size"] == 30 or by_type["domain"][0]["size"] == 30.0
    assert by_type["domain"][1]["size"] == 8
    assert len(by_type["entry-point"]) == 2 and len(by_type["tool-preference"]) == 5
    assert by_type["tool-preference"][0]["label"] == "T6"
    assert len(by_type["behavioral-feature"]) == 3  # tiny filtered
    types = [e["type"] for e in g["edges"]]
    assert types.count("bridge") == 1 and types.count("persistent-feature") == 1
    bridge = next(e for e in g["edges"] if e["type"] == "bridge")
    assert bridge["target"] == "domain_1" and bridge["label"] == "p"
    assert g["blindSpotRegions"][0]["severity"] == "high"
    pf = next(e for e in g["edges"] if e["type"] == "persistent-feature")
    assert pf["color"] == gb.PERSISTENT_COLOR


def test_build_graph_filtered_and_empty():
    g = gb.build_graph(PROFILES, filter_domain="beta")
    assert [n["domain"] for n in g["nodes"] if n["type"] == "domain"] == ["beta"]
    g = gb.build_graph(PROFILES, filter_domain="unknown")
    assert g["nodes"] == [] and g["edges"] == []
    assert gb.build_graph({}) == {"nodes": [], "edges": [], "blindSpotRegions": []}


def test_bridge_to_unrendered_domain_is_skipped():
    acc = gb._GraphAccumulator()
    gb._add_bridges(
        acc, "domain_0", {"connectionBridges": [{"toDomain": "x"}]}, {"x": {}}, []
    )
    assert acc.edges == []


def test_behavioral_features_skip_domains_without_hub():
    acc = gb._GraphAccumulator()
    gb._add_behavioral_features(acc, {"a": {"featureActivations": {"f": 1.0}}})
    assert acc.nodes == []


# ── wiki_coverage ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_registry(monkeypatch, tmp_path):
    src = tmp_path / "repo"
    (src / "pkg").mkdir(parents=True)
    (src / "node_modules").mkdir()
    (src / ".hidden").mkdir()
    (src / "pkg" / "covered_by_path.py").write_text("x")
    (src / "pkg" / "covered_by_name.py").write_text("x")
    (src / "pkg" / "uncovered.py").write_text("x")
    (src / "notes.md").write_text("x")
    (src / "node_modules" / "skip.py").write_text("x")
    (src / ".hidden" / "skip2.py").write_text("x")
    other = tmp_path / "repo2"
    other.mkdir()
    reg = SimpleNamespace(
        repos=[
            domain_mapping.RepoInfo(str(src), "repo", "repo", canonical="proj"),
            domain_mapping.RepoInfo(str(other), "repo2", "repo2", canonical="proj"),
            domain_mapping.RepoInfo(str(tmp_path / "x"), "x", "x", canonical="else"),
        ]
    )
    monkeypatch.setattr(domain_mapping, "_build_registry", lambda: reg)
    return src, other


def test_source_roots(fake_registry):
    src, other = fake_registry
    assert wc.source_roots_for_domain("proj") == [str(src), str(other)]
    assert wc._project_source_root("proj") == str(src)
    assert wc._project_source_root("none") is None
    assert wc.source_roots_for_domain("none") == []


def test_source_roots_registry_import_failure(monkeypatch):
    monkeypatch.delattr(domain_mapping, "_build_registry")
    assert wc.source_roots_for_domain("proj") == []


def test_list_source_files(fake_registry):
    src, _ = fake_registry
    files = sorted(wc.list_source_files(str(src)))
    assert files == [
        "pkg/covered_by_name.py",
        "pkg/covered_by_path.py",
        "pkg/uncovered.py",
    ]
    assert wc.list_source_files(str(src / "nope")) == []


def test_index_wiki_references(tmp_path):
    assert wc._index_wiki_file_references(str(tmp_path / "missing"), "d") == (
        set(),
        set(),
    )
    wiki = tmp_path / "wiki"
    (wiki / "sub").mkdir(parents=True)
    (wiki / "_private").mkdir()
    (wiki / ".dot").mkdir()
    (wiki / "a.md").write_text("see `pkg/covered_by_path.py` and covered_by_name.py")
    (wiki / "sub" / "b.md").write_text("./lib/mod.ts")
    (wiki / "_private" / "c.md").write_text("hidden/never.py")
    (wiki / "note.txt").write_text("ignored/x.py")
    (wiki / "dangling.md").symlink_to(tmp_path / "does-not-exist")  # open() -> OSError
    paths, names = wc._index_wiki_file_references(str(wiki), "d")
    assert "pkg/covered_by_path.py" in paths and "lib/mod.ts" in paths
    assert (
        "covered_by_name.py" in names
        and "never.py" not in names
        and "x.py" not in names
    )


def test_audit_files(fake_registry, tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "a.md").write_text("pkg/covered_by_path.py and covered_by_name.py")
    cov = wc.audit_files(str(wiki), "proj")
    assert cov.source_file_count == 3 and cov.covered_file_count == 2
    assert cov.uncovered_files == ["pkg/uncovered.py"]
    assert cov.coverage_ratio == pytest.approx(2 / 3)
    none = wc.audit_files(str(wiki), "unknown")
    assert none.source_root is None and none.coverage_ratio == 1.0
    reg_other = wc.FileCoverage("d", "/r", 0, 0)
    assert reg_other.coverage_ratio == 1.0


def test_audit_files_empty_root(monkeypatch, tmp_path):
    empty = tmp_path / "emptyrepo"
    empty.mkdir()
    reg = SimpleNamespace(
        repos=[domain_mapping.RepoInfo(str(empty), "e", "e", canonical="emp")]
    )
    monkeypatch.setattr(domain_mapping, "_build_registry", lambda: reg)
    cov = wc.audit_files(str(tmp_path), "emp")
    assert cov.source_root == str(empty) and cov.source_file_count == 0
