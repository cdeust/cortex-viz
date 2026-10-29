"""Tests for workflow_graph_source_pg loaders over a fake MemoryReader."""

from __future__ import annotations

from datetime import datetime

from cortex_viz.infrastructure import workflow_graph_source_pg as src


def tool_from_tags(tags):
    for t in tags:
        if t.startswith("tool:"):
            return t[5:].capitalize()
    return None


def domain_from_directory(d):
    return "dom-" + d.strip("/") if d else None


def cmd_hash(c):
    return "h-" + c


def first_line(t):
    return t.splitlines()[0].strip() if t.strip() else ""


class FakeReader:
    def __init__(self, memories=None):
        self.memories = memories or []
        self.search_calls = []
        self.query_calls = []

    def search_by_tag_vector(self, **kw):
        self.search_calls.append(kw)
        return self.memories

    def query(self, sql, params=None, **kw):
        self.query_calls.append((sql, params, kw))
        return []


def test_iso():
    assert src._iso(None) is None
    assert src._iso(datetime(2026, 1, 1)) == "2026-01-01T00:00:00"
    assert src._iso("raw") == "raw"
    assert src._iso(5) == "5"

    class Bad:
        def isoformat(self):
            raise ValueError("bad")

    assert src._iso(Bad()) is None


def test_load_tool_events_buckets_and_orders_timestamps():
    t1, t2, t3 = "2026-01-01T00:00:00", "2026-01-02T00:00:00", "2026-01-03T00:00:00"
    mems = [
        {
            "tags": ["tool:edit"],
            "domain": "core",
            "content": "**File:** `/a/b.py`",
            "created_at": t2,
        },
        {
            "tags": ["tool:edit"],
            "domain": "core",
            "content": "**File:** `/a/b.py`",
            "created_at": t1,
        },
        {
            "tags": ["tool:edit"],
            "domain": "core",
            "content": "**File:** `/a/b.py`",
            "created_at": t3,
        },
        {
            "tags": ["tool:edit"],
            "domain": "core",
            "content": "**File:** `/a/b.py`",
            "created_at": None,
        },
        {
            "tags": ["tool:grep"],
            "directory_context": "/x",
            "content": "**Grep:** `foo` in `/src`",
        },
        {"tags": ["tool:glob"], "content": "**Glob:** `*.py` (root=`/g`)"},
        {"tags": ["tool:bash"], "content": "**Command:** `ls`"},
        {"tags": ["tool:read"], "content": "no path here"},
        {"tags": ["other"], "content": "x"},
        {"tags": None, "content": None},
    ]
    r = FakeReader(mems)
    out = src.load_tool_events(
        r, tool_from_tags, domain_from_directory, cmd_hash, first_line
    )
    assert r.search_calls[0]["tag"] == "auto-captured"
    by = {(o["tool"], o["file_path"], o["domain"]): o for o in out}
    edit = by[("Edit", "/a/b.py", "core")]
    assert edit["count"] == 4 and edit["first_ts"] == t1 and edit["last_ts"] == t3
    assert by[("Grep", "/src", "dom-x")]["count"] == 1
    assert ("Glob", "/g", "") in by
    assert ("Bash", None, "") in by and ("Read", None, "") in by


def test_load_command_events():
    mems = [
        {
            "content": "**Command:** `git status\nsecond`",
            "domain": "d",
            "created_at": "2026-01-02",
        },
        {
            "content": "**Command:** `git status`",
            "domain": "d",
            "created_at": "2026-01-01",
        },
        {
            "content": "**Command:** `git status`",
            "domain": "d",
            "created_at": "2026-01-05",
        },
        {"content": "**Command:** `ls`", "directory_context": "/w"},
        {"content": "**Command:** `   `"},
        {"content": "no command"},
        {"content": None},
    ]
    out = src.load_command_events(
        FakeReader(mems), domain_from_directory, cmd_hash, first_line
    )
    by = {o["cmd"]: o for o in out}
    g = by["git status"]
    assert (
        g["count"] == 3
        and g["first_ts"] == "2026-01-01"
        and g["last_ts"] == "2026-01-05"
    )
    assert g["cmd_hash"] == "h-git status"
    assert by["ls"]["domain"] == "dom-w" and len(out) == 2


def test_load_command_files():
    mems = [
        {"content": "**Command:** `cat /a/known.py && cat /a/other.py`"},
        {"content": "**Command:** `vim /a/known.py,`"},
        {"content": "**Command:** `echo hi`"},
        {"content": "**Command:** `  `"},
        {"content": "nothing"},
    ]
    r = FakeReader(mems)
    assert src.load_command_files(r, [], cmd_hash, first_line) == []
    assert r.search_calls == []
    out = src.load_command_files(r, ["/a/known.py"], cmd_hash, first_line)
    assert {(o["cmd_hash"], o["file_path"], o["count"]) for o in out} == {
        ("h-cat /a/known.py && cat /a/other.py", "/a/known.py", 1),
        ("h-vim /a/known.py,", "/a/known.py", 1),
    }


def test_project_memory_row():
    row = src._project_memory_row(
        {
            "id": 1,
            "domain": None,
            "consolidation_stage": None,
            "heat_base": 0.3,
            "content": None,
            "arousal": 0.5,
            "importance": None,
            "ignored": 1,
        }
    )
    assert row == {
        "id": 1,
        "domain": "",
        "consolidation_stage": "episodic",
        "heat": 0.3,
        "content": "",
        "heat_base": 0.3,
        "arousal": 0.5,
    }
    assert src._project_memory_row({"heat": 0.9, "heat_base": 0.1})["heat"] == 0.9


class HotReader:
    def get_hot_memories(self, **kw):
        self.kw = kw
        return [{"id": 1, "heat": 0.5}]

    def iter_hot_memories_chunked(self, **kw):
        self.chunk_kw = kw
        yield [{"id": 1, "heat_base": 0.4}]
        yield [{"id": 2}]


def test_load_memories_and_chunked():
    r = HotReader()
    assert src.load_memories(r, 0.2, 5)[0]["id"] == 1
    assert r.kw == {"min_heat": 0.2, "limit": 5, "include_benchmarks": True}
    chunks = list(src.iter_memories_chunked(r, chunk_size=10, limit=3))
    assert [c[0]["id"] for c in chunks] == [1, 2]
    assert (
        r.chunk_kw["hard_limit"] == 3
        and r.chunk_kw["columns"] == src._GRAPH_MEMORY_COLUMNS
    )
    list(src.iter_memories_chunked(r))
    assert r.chunk_kw["hard_limit"] is None
    assert "embedding" not in src._GRAPH_MEMORY_COLUMNS


def test_load_entities():
    class R:
        def get_all_entities(self, **kw):
            self.kw = kw
            return [
                {"id": 1, "name": "A", "type": None, "domain": None, "heat": None},
                {"id": None, "name": "B"},
                {"id": 3, "name": ""},
                {"id": 4, "name": "D", "type": "tool", "domain": "x", "heat": 0.5},
            ]

    r = R()
    out = src.load_entities(r)
    assert r.kw == {"min_heat": 0.0, "include_archived": True}
    assert out == [
        {"id": 1, "name": "A", "type": "concept", "domain": "", "heat": 0.0},
        {"id": 4, "name": "D", "type": "tool", "domain": "x", "heat": 0.5},
    ]
    assert src.load_entities(object()) == []


def test_memory_entity_edges():
    class R:
        def list_memory_entity_edges(self):
            return [{"memory_id": 1, "entity_id": 2}]

    assert src.load_memory_entity_edges(R()) == [{"memory_id": 1, "entity_id": 2}]
    assert src.load_memory_entity_edges(object()) == []


def test_delegating_loaders_use_batch_queries():
    r = FakeReader()
    assert src.load_supersede_edges(r) == []
    assert src.load_wiki_pages(r) == []
    assert src.load_wiki_links(r) == []
    assert src.load_wiki_memory_links(r) == []
    assert src.load_wiki_page_sources(r) == []
    assert src.load_wiki_session_links(r) == []
    assert src.load_co_entity_associations(r, top_k=3) == []
    assert src.load_memory_associations(r, top_k=3) == []
    assert len(r.query_calls) >= 6
    assert all(c[2].get("batch") for c in r.query_calls)
