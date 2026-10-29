"""Behaviour tests for MemoryReader over a fake psycopg pool boundary."""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from cortex_viz.infrastructure import memory_read as mr
from cortex_viz.infrastructure.memory_read import MemoryReader, _MaterializedCursor


class FakeCursor:
    def __init__(self, rows, rowcount=None, raises=None):
        self._rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount
        self._raises = raises

    def fetchall(self):
        if self._raises:
            raise self._raises
        return list(self._rows)


class FakeConn:
    def __init__(self, router, log):
        self._router = router
        self._log = log

    def execute(self, query, params=None):
        self._log.append((query, params))
        out = self._router(query, params)
        if isinstance(out, Exception):
            raise out
        return FakeCursor(out)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakePool:
    def __init__(self, router, log, name):
        self.router = router
        self.log = log
        self.name = name
        self.closed = False

    def connection(self):
        self.log.append(("POOL", self.name))
        return FakeConn(self.router, self.log)

    def close(self):
        self.closed = True


def make_reader(router):
    r = MemoryReader("postgresql://x/y")
    log: list = []
    r._interactive_pool = FakePool(router, log, "interactive")
    r._batch_pool = FakePool(router, log, "batch")
    r.log = log
    return r


def sqls(r):
    return [q for q, _ in r.log if q != "POOL"]


def boom(q, p):
    return RuntimeError("relation does not exist")


def empty(q, p):
    return []


def const(rows):
    return lambda q, p: rows


def test_goal_tokens_and_fm_error():
    assert mr._goal_tokens("The Deploy and fix of API-9 for it") == [
        "deploy",
        "fix",
        "api",
    ]
    assert mr._fm_mean_abs_error([1.0]) == 0.0
    assert mr._fm_mean_abs_error([0.0, 1.0, 1.0, 1.01]) > 0
    assert mr._fm_mean_abs_error([0.5, 0.5, 0.5]) == 0.0


def test_resolve_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/db")
    assert mr._resolve_database_url() == "postgresql://h/db"
    monkeypatch.setenv("DATABASE_URL", "${DB}")
    assert mr._resolve_database_url() == mr._DEFAULT_DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", "  ")
    assert mr._resolve_database_url() == mr._DEFAULT_DATABASE_URL
    assert MemoryReader().url == mr._DEFAULT_DATABASE_URL
    assert MemoryReader("postgresql://a/b").url == "postgresql://a/b"


def test_materialized_cursor():
    c = _MaterializedCursor(FakeCursor([{"a": 1}, {"a": 2}, {"a": 3}], rowcount=3))
    assert c.rowcount == 3
    assert c.fetchone() == {"a": 1}
    assert list(c) == [{"a": 2}, {"a": 3}]
    assert c.fetchone() is None
    assert c.fetchall() == []
    c2 = _MaterializedCursor(FakeCursor([{"a": 1}, {"a": 2}]))
    assert c2.fetchall() == [{"a": 1}, {"a": 2}]
    bad = _MaterializedCursor(FakeCursor([], raises=psycopg.ProgrammingError("x")))
    assert bad.fetchall() == []
    bad2 = _MaterializedCursor(FakeCursor([], raises=TypeError("x")))
    assert bad2.fetchone() is None


def test_pools_are_opened_lazily_with_settings(monkeypatch):
    calls = []

    class P:
        def __init__(self, **kw):
            calls.append(kw)

        def close(self):
            raise RuntimeError("already closed")

    monkeypatch.setattr(mr, "ConnectionPool", P)
    r = MemoryReader("postgresql://z/z")
    p1 = r.interactive_pool
    assert r.interactive_pool is p1
    b1 = r.batch_pool
    assert r.batch_pool is b1
    assert len(calls) == 2
    for kw in calls:
        assert kw["conninfo"] == "postgresql://z/z"
        assert kw["kwargs"]["autocommit"] is True
        assert kw["open"] is True
    r.close()  # swallows teardown errors
    MemoryReader("postgresql://z/z").close()


def test_close_closes_pools():
    r = make_reader(empty)
    ip, bp = r._interactive_pool, r._batch_pool
    r.close()
    assert ip.closed and bp.closed


def test_query_and_execute_pool_routing():
    r = make_reader(const([{"x": 1}]))
    assert r.query("SELECT 1") == [{"x": 1}]
    assert ("POOL", "interactive") in r.log
    r.query("SELECT 1", batch=True)
    assert ("POOL", "batch") in r.log


def test_normalize_memory_row():
    r = make_reader(empty)
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    out = r._normalize_memory_row(
        {
            "id": 1,
            "heat_base": 0.4,
            "embedding": b"zz",
            "tags": '["a","b"]',
            "created_at": ts,
            "last_accessed": ts,
        }
    )
    assert out["heat"] == 0.4
    assert "embedding" not in out
    assert out["tags"] == ["a", "b"]
    assert out["created_at"] == ts.isoformat()
    out = r._normalize_memory_row({"heat": 0.9, "heat_base": 0.1, "tags": "{bad"})
    assert out["heat"] == 0.9 and out["tags"] == []


def test_hot_memories_limited_and_full_scan():
    rows = [{"id": 1, "heat": 0.9, "heat_base": 0.9, "tags": "[]"}]
    r = make_reader(const(rows))
    res = r.get_hot_memories(min_heat=0.5, limit=5)
    assert res[0]["tags"] == [] and res[0]["id"] == 1
    assert "LIMIT %s" in sqls(r)[0] and "is_benchmark" in sqls(r)[0]
    assert r.log[-1][1] == (0.5, 5) and ("POOL", "interactive") in r.log
    r2 = make_reader(const(rows))
    r2.get_hot_memories(limit=0, include_benchmarks=True)
    assert "is_benchmark" not in sqls(r2)[0]
    assert ("POOL", "batch") in r2.log


def test_recent_and_get_memory():
    r = make_reader(const([{"id": 7, "heat_base": 0.3}]))
    assert r.get_recent_memories(3)[0]["heat"] == 0.3
    assert r.get_memory(7)["id"] == 7
    assert make_reader(empty).get_memory(7) is None


def test_count_memories_and_empty():
    r = make_reader(const([{"total": 3, "raw_total": 4}]))
    assert r.count_memories() == {"total": 3, "raw_total": 4}
    assert make_reader(empty).count_memories() == {}


def test_avg_heat():
    assert make_reader(const([{"avg_heat": 0.25}])).get_avg_heat() == 0.25
    assert make_reader(const([{"avg_heat": None}])).get_avg_heat() == 0.0
    assert make_reader(empty).get_avg_heat() == 0.0


def test_stage_provenance_domain_counts():
    r = make_reader(
        const(
            [
                {"stage": "labile", "c": 2},
                {"stage": None, "c": 5},
                {"stage": "consolidated", "c": "3"},
            ]
        )
    )
    assert r.get_stage_counts() == {"labile": 2, "consolidated": 3}
    r = make_reader(const([{"attr": "told", "c": 4}]))
    assert r.get_provenance_counts() == {"told": 4}
    assert make_reader(boom).get_provenance_counts() == {}
    r = make_reader(const([{"d": "core", "c": 9}]))
    assert r.get_domain_counts() == {"core": 9}


def test_entities():
    r = make_reader(const([{"id": 1, "name": "e"}]))
    assert r.get_entity_by_id(1) == {"id": 1, "name": "e"}
    assert make_reader(empty).get_entity_by_id(1) is None
    assert r.get_top_entities_for_domain("d", 2) == [{"id": 1, "name": "e"}]
    assert r.get_all_entities() == [{"id": 1, "name": "e"}]
    assert "NOT archived" in sqls(r)[-1]
    r.get_all_entities(include_archived=True)
    assert "NOT archived" not in sqls(r)[-1]
    assert ("POOL", "batch") in r.log


def test_simple_counts():
    r = make_reader(const([{"c": 6}]))
    assert r.count_entities() == 6
    assert r.count_relationships() == 6
    assert r.count_active_triggers() == 6
    assert r.count_procedural_skills() == 6
    e = make_reader(empty)
    assert e.count_entities() == 0
    assert e.count_relationships() == 0
    assert e.count_active_triggers() == 0
    assert e.count_procedural_skills() == 0
    assert make_reader(boom).count_procedural_skills() == 0


def test_relationships_and_consolidation():
    r = make_reader(const([{"id": 1}]))
    assert r.get_all_relationships() == [{"id": 1}]
    ts = datetime(2026, 3, 1)
    r = make_reader(const([{"timestamp": ts}]))
    assert r.get_last_consolidation() == ts.isoformat()
    assert make_reader(empty).get_last_consolidation() is None


def test_count_provenance():
    r = make_reader(
        const(
            [
                {"a": "told", "c": 2},
                {"a": None, "c": 1},
                {"a": "weird", "c": 3},
            ]
        )
    )
    assert r.count_provenance() == {
        "perceived": 0,
        "told": 2,
        "inferred": 0,
        "unknown": 4,
    }
    assert make_reader(boom).count_provenance()["unknown"] == 0


@pytest.mark.parametrize(
    "method,ok_rows,expected",
    [
        ("count_crystallized_confabulations", [{"c": 3}], 3),
        ("count_extinguished", [{"c": 4}], 4),
        ("count_conflicting_claim_pairs", [{"c": 5}], 5),
    ],
)
def test_single_count_signals(method, ok_rows, expected):
    assert getattr(make_reader(const(ok_rows)), method)() == expected
    assert getattr(make_reader(empty), method)() == 0
    assert getattr(make_reader(boom), method)() == 0


def test_zero_count_confab_and_extinguished():
    assert make_reader(const([{"c": 0}])).count_crystallized_confabulations() == 0
    assert make_reader(const([{"c": 0}])).count_extinguished(0.2) == 0


def test_habituated_repeats():
    r = make_reader(const([{"s": "a", "c": 3}, {"s": "b", "c": 2}]))
    assert r.count_habituated_repeats() == 3
    assert make_reader(boom).count_habituated_repeats() == 0


def test_attentional_salience():
    neutral = {
        "sampled": 0,
        "focus_share": 0.0,
        "mean_salience": 0.0,
        "max_salience": 0.0,
    }
    row = {
        "sampled": 10,
        "total_sal": 5.0,
        "focus_sal": 2.5,
        "mean_sal": 0.5,
        "max_sal": 0.9,
    }
    out = make_reader(const([row])).count_attentional_salience()
    assert out == {
        "sampled": 10,
        "focus_share": 0.5,
        "mean_salience": 0.5,
        "max_salience": 0.9,
    }
    zero = dict(row, total_sal=0.0, focus_sal=0.0)
    out = make_reader(const([zero])).count_attentional_salience()
    assert out["focus_share"] == 0.0
    assert make_reader(empty).count_attentional_salience() == neutral
    nosample = make_reader(const([dict(row, sampled=0)]))
    assert nosample.count_attentional_salience() == neutral
    assert make_reader(boom).count_attentional_salience() == neutral


def test_familiarity_resolvable():
    row = {"sampled": 4, "resolvable": 1, "mean_top_sim": 0.81234}
    out = make_reader(const([row])).count_familiarity_resolvable()
    assert out == {"sampled": 4, "resolvable": 1, "share": 0.25, "mean_top_sim": 0.8123}
    zero = {"sampled": 0, "resolvable": 0, "share": 0.0, "mean_top_sim": 0.0}
    assert make_reader(empty).count_familiarity_resolvable() == zero
    assert make_reader(boom).count_familiarity_resolvable() == zero
    r = make_reader(const([{"sampled": 0, "resolvable": 0, "mean_top_sim": None}]))
    assert r.count_familiarity_resolvable()["share"] == 0.0


def test_sleep_phase_outputs():
    def router(q, p):
        return [{"c": 2 if "sleep-compute" in q else 7}]

    assert make_reader(router).count_sleep_phase_outputs() == {"nrem": 2, "rem": 7}
    zero = {"nrem": 0, "rem": 0}
    assert make_reader(empty).count_sleep_phase_outputs() == zero
    assert make_reader(boom).count_sleep_phase_outputs() == zero


def test_targeted_reactivation_and_stress():
    r = make_reader(const([{"tmr_cue": "auth", "tmr_cued_replayed": 3}]))
    assert r.count_targeted_reactivation() == {"cue": "auth", "cued_replayed": 3}
    r = make_reader(const([{"tmr_cue": "", "tmr_cued_replayed": None}]))
    assert r.count_targeted_reactivation() == {"cue": None, "cued_replayed": 0}
    assert make_reader(boom).count_targeted_reactivation() == {
        "cue": None,
        "cued_replayed": 0,
    }
    r = make_reader(const([{"stress_scalar": 0.91234, "consolidation_gain": 0.6}]))
    assert r.count_stress_modulation() == {
        "stress": 0.9123,
        "gain": 0.6,
        "is_impairing": True,
    }
    r = make_reader(const([{"stress_scalar": None, "consolidation_gain": None}]))
    assert r.count_stress_modulation() == {
        "stress": 0.0,
        "gain": 1.0,
        "is_impairing": False,
    }
    assert make_reader(boom).count_stress_modulation()["gain"] == 1.0
    assert make_reader(empty).count_stress_modulation()["stress"] == 0.0


def _trig(t, cond, target=None):
    return {
        "trigger_type": t,
        "trigger_condition": cond,
        "target_directory": target,
    }


def test_active_goal():
    rows = [
        _trig("keyword_match", "Deploy the Cortex build"),
        _trig("entity_match", "Foo"),
        _trig("entity_match", ""),
        _trig("directory_match", "", "/src"),
        _trig("directory_match", None),
    ]
    out = make_reader(const(rows)).count_active_goal()
    assert out["active"] is True and out["triggers"] == 5
    assert out["keywords"] == 3 and out["label"] == "build cortex deploy"
    out = make_reader(const([rows[1]])).count_active_goal()
    assert out["active"] and out["label"] is None
    out = make_reader(const([rows[2], rows[4]])).count_active_goal()
    assert out["active"] is False and out["triggers"] == 2
    out = make_reader(boom).count_active_goal()
    assert out == {"active": False, "triggers": 0, "keywords": 0, "label": None}


def test_forward_model():
    rows = [{"heat_base": 0.9}, {"heat_base": 0.5}, {"heat_base": 0.1}]
    out = make_reader(const(rows)).count_forward_model()
    assert out["sampled"] == 3 and out["mean_error"] > 0
    assert make_reader(boom).count_forward_model() == {
        "sampled": 0,
        "mean_error": 0.0,
    }


def test_list_procedural_skills():
    r = make_reader(const([{"skill_id": 1}]))
    assert r.list_procedural_skills(0.5, 10) == [{"skill_id": 1}]
    assert make_reader(boom).list_procedural_skills() == []


def test_iter_hot_memories_chunked_keyset_pagination():
    data = [
        {"id": 5, "heat_base": 0.9, "heat": 0.9},
        {"id": 4, "heat_base": 0.8, "heat": 0.8},
        {"id": 3, "heat_base": 0.7, "heat": 0.7},
    ]

    def router(q, p):
        if "(heat_base, id) <" in q:
            assert p == (0.0, 0.8, 4)
            return data[2:]
        return data[:2]

    r = make_reader(router)
    chunks = list(r.iter_hot_memories_chunked(chunk_size=2, include_benchmarks=False))
    assert [[m["id"] for m in c] for c in chunks] == [[5, 4], [3]]
    assert "is_benchmark" in sqls(r)[0]


def test_iter_hot_memories_chunked_hard_limit_and_empty():
    data = [{"id": 2, "heat_base": 0.9}, {"id": 1, "heat_base": 0.8}]
    r = make_reader(const(data))
    chunks = list(r.iter_hot_memories_chunked(chunk_size=2, hard_limit=2))
    assert len(chunks) == 1  # cap reached after first full page
    assert list(make_reader(empty).iter_hot_memories_chunked()) == []


def test_iter_hot_memories_chunked_hits_cap_between_pages():
    pages = iter(
        [
            [{"id": 3, "heat_base": 0.9}, {"id": 2, "heat_base": 0.8}],
            [{"id": 1, "heat_base": 0.7}, {"id": 0, "heat_base": 0.6}],
        ]
    )
    r = make_reader(lambda q, p: next(pages))
    out = list(r.iter_hot_memories_chunked(chunk_size=2, hard_limit=4))
    assert sum(len(c) for c in out) == 4


def test_memory_entity_edges():
    r = make_reader(
        const(
            [
                {"memory_id": 1, "entity_id": 2},
                {"memory_id": None, "entity_id": 2},
                {"memory_id": 3, "entity_id": None},
            ]
        )
    )
    assert r.list_memory_entity_edges() == [{"memory_id": 1, "entity_id": 2}]


def test_search_by_tag_vector():
    r = make_reader(const([{"id": 1, "heat_base": 0.2, "tags": '["t"]'}]))
    out = r.search_by_tag_vector(None, "t", domain="d")
    assert out[0]["tags"] == ["t"]
    assert r.log[-1][1] == ('["t"]', 0.01, "d", "d", 3)
    with pytest.raises(NotImplementedError):
        r.search_by_tag_vector(b"x", "t")
