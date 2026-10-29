"""Tests for layout_pg_store, lod_pg_store and memory_browse over fake PG."""

from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest

from cortex_viz.infrastructure import layout_pg_store as lps
from cortex_viz.infrastructure import lod_pg_store as lod
from cortex_viz.infrastructure import memory_browse as mb


class FakePG:
    """Tiny in-memory emulation of the two layout tables behind a pool."""

    def __init__(self):
        self.layout: dict[str, tuple] = {}
        self.lod: dict[tuple, tuple] = {}
        self.commits = 0
        self.ddl = 0
        self.rowcount = 0

    # pool protocol
    def connection(self):
        return _Conn(self)

    @property
    def batch_pool(self):
        return self


class _Conn:
    def __init__(self, pg):
        self.pg = pg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cur(self.pg)

    def commit(self):
        self.pg.commits += 1


class _Cur:
    def __init__(self, pg):
        self.pg = pg
        self._rows: list = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        pg = self.pg
        if sql.strip().startswith("CREATE TABLE"):
            pg.ddl += 1
        elif sql == "DELETE FROM workflow_graph_layout":
            pg.layout.clear()
        elif sql.startswith("SELECT layout_version AS v"):
            if not pg.layout:
                self._rows = []
                return
            newest = max(r[5] for r in pg.layout.values())
            sel = [r for r in pg.layout.values() if r[5] == newest]
            self._rows = [{"v": newest, "fp": sel[0][4], "n": len(sel)}]
        elif sql.startswith(
            "SELECT node_id, x, y, kind FROM workflow_graph_layout WHERE node_id >"
        ):
            last, page = params
            ids = sorted(k for k in pg.layout if k > last)[:page]
            self._rows = [self._layout_row(i) for i in ids]
        elif sql.startswith(
            "SELECT node_id, x, y, kind FROM workflow_graph_layout WHERE x BETWEEN"
        ):
            x0, x1, y0, y1, *lim = params
            rows = [
                self._layout_row(i)
                for i, r in sorted(pg.layout.items())
                if x0 <= r[1] <= x1 and y0 <= r[2] <= y1
            ]
            self._rows = rows[: lim[0]] if lim else rows
        elif sql.startswith("SELECT 1 FROM workflow_graph_layout_lod"):
            self._rows = [{"one": 1}] if any(k[0] == params[0] for k in pg.lod) else []
        elif sql.startswith(
            "DELETE FROM workflow_graph_layout_lod WHERE topology_fingerprint = "
        ):
            for k in [k for k in pg.lod if k[0] == params[0]]:
                del pg.lod[k]
        elif sql.startswith(
            "DELETE FROM workflow_graph_layout_lod WHERE topology_fingerprint !="
        ):
            gone = [k for k in pg.lod if k[0] != params[0]]
            for k in gone:
                del pg.lod[k]
            self.rowcount = len(gone)
        elif sql.startswith("SELECT ('lod:'"):
            fp, level, x0, x1, y0, y1 = params
            self._rows = [
                {
                    "node_id": f"lod:{k[1]}:{k[2]}:{k[3]}",
                    "x": v[0],
                    "y": v[1],
                    "kind": v[3],
                }
                for k, v in pg.lod.items()
                if k[0] == fp
                and k[1] == level
                and x0 <= v[0] <= x1
                and y0 <= v[1] <= y1
            ]
        else:
            raise AssertionError(f"unexpected SQL {sql!r}")

    def _layout_row(self, nid):
        r = self.pg.layout[nid]
        return {"node_id": nid, "x": r[1], "y": r[2], "kind": r[3]}

    def executemany(self, sql, rows):
        pg = self.pg
        if "workflow_graph_layout_lod" in sql:
            for fp, level, cx, cy, xbar, ybar, count, dom in rows:
                pg.lod[(fp, level, cx, cy)] = (xbar, ybar, count, dom)
        else:
            for r in rows:
                pg.layout[r[0]] = r

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_conn_requires_batch_pool():
    with pytest.raises(AttributeError):
        lps._conn(object())
    with pytest.raises(AttributeError):
        lod._conn(object())


def test_write_layout_replaces_and_reads_back():
    pg = FakePG()
    assert lps.read_layout_version(pg) is None
    v0 = lps.write_layout(pg, [], {}, topology_fingerprint="f0")
    assert v0 > 0 and pg.layout == {} and pg.commits == 0
    pg.layout["old"] = ("old", 0, 0, "k", "x", 1)
    v = lps.write_layout(
        pg,
        [("a", 1, 2), ("b", 3, 4)],
        {"a": "memory"},
        topology_fingerprint="fp1",
    )
    assert set(pg.layout) == {"a", "b"}
    assert pg.layout["a"][3] == "memory" and pg.layout["b"][3] == "unknown"
    assert lps.read_layout_version(pg) == {
        "version": v,
        "fingerprint": "fp1",
        "count": 2,
    }


def test_upsert_positions_and_bbox():
    pg = FakePG()
    assert lps.upsert_positions(pg, [], layout_version=5) == 0
    n = lps.upsert_positions(
        pg,
        [("a", 1, 1, "memory"), ("b", 5, 5, "entity"), ("c", 9, 9, "memory")],
        layout_version=5,
    )
    assert n == 3 and pg.layout["a"][4] == "stream"
    lps.upsert_positions(
        pg, [("a", 2, 2, "memory")], layout_version=6, topology_fingerprint="z"
    )
    assert pg.layout["a"][1] == 2.0 and pg.layout["a"][5] == 6
    got = lps.read_positions_in_bbox(pg, min_x=0, min_y=0, max_x=6, max_y=6)
    assert got == [("a", 2.0, 2.0, "memory"), ("b", 5.0, 5.0, "entity")]
    capped = lps.read_positions_in_bbox(pg, min_x=0, min_y=0, max_x=6, max_y=6, limit=1)
    assert len(capped) == 1


def test_iter_positions_chunked_pages():
    pg = FakePG()
    lps.upsert_positions(pg, [(f"n{i}", i, i, "k") for i in range(5)], layout_version=1)
    chunks = list(lps.iter_positions_chunked(pg, chunk_size=2))
    assert [len(c) for c in chunks] == [2, 2, 1]
    assert chunks[0][0] == ("n0", 0.0, 0.0, "k")
    # exact multiple -> trailing empty page ends the stream
    chunks = list(lps.iter_positions_chunked(pg, chunk_size=5))
    assert [len(c) for c in chunks] == [5]
    assert list(lps.iter_positions_chunked(FakePG())) == []


def test_build_lod_builds_skips_and_prunes():
    pg = FakePG()
    lps.upsert_positions(
        pg,
        [("a", 0, 0, "memory"), ("b", 1, 1, "memory"), ("c", 900, 900, "entity")],
        layout_version=1,
    )
    pg.lod[("stale", 0, 0, 0)] = (0.0, 0.0, 1, "memory")
    res = lod.build_lod(pg, fingerprint="fp", max_level=2)
    assert res["skipped"] is False and res["cells"] == len(
        [k for k in pg.lod if k[0] == "fp"]
    )
    assert pg.ddl == 1
    assert all(k[0] == "fp" for k in pg.lod)  # stale pruned
    again = lod.build_lod(pg, fingerprint="fp", max_level=2)
    assert again == {"status": "ok", "skipped": True, "fingerprint": "fp"}
    # coarsest level holds all points
    lvl0 = lod.read_lod_in_bbox(
        pg, fingerprint="fp", level=0, min_x=-1e9, min_y=-1e9, max_x=1e9, max_y=1e9
    )
    assert sum(1 for _ in lvl0) >= 1
    assert lvl0[0][0].startswith("lod:0:")


def test_build_lod_empty_layout_has_no_cells():
    pg = FakePG()
    res = lod.build_lod(pg, fingerprint="e")
    assert res["cells"] == 0 and res["max_level"] == 7


def test_prune_other_fingerprints():
    pg = FakePG()
    pg.lod[("a", 0, 0, 0)] = (0, 0, 1, "k")
    pg.lod[("b", 0, 0, 0)] = (0, 0, 1, "k")
    assert lod.prune_other_fingerprints(pg, keep_fingerprint="a") == 1
    assert list(pg.lod) == [("a", 0, 0, 0)]


# ── memory_browse ────────────────────────────────────────────────────────────


class FakeStore:
    def __init__(self, rows=None, agg=None, domains=None):
        self.calls = []
        self.rows = rows or []
        self.agg = agg
        self.domains = domains or []

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        if sql.lstrip().startswith("SELECT COALESCE(NULLIF"):
            return self.domains
        if "FILTER" in sql:
            return self.agg if self.agg is not None else []
        return self.rows


def test_truthy_and_cursor_roundtrip():
    assert mb._truthy("YES") and mb._truthy(1) and not mb._truthy("0")
    assert not mb._truthy(None)
    cur = mb._encode_cursor(0.5, 9)
    assert mb._decode_cursor(cur) == (0.5, 9)
    assert mb._decode_cursor(None) is None
    assert mb._decode_cursor("!!!") is None
    bad = base64.urlsafe_b64encode(json.dumps({"k": 1}).encode()).decode()
    assert mb._decode_cursor(bad) is None


def test_emotion_clause():
    assert mb._emotion_clause("urgent") == "importance >= 0.75"
    assert "emotional_valence >= 0.25" in mb._emotion_clause("positive")
    assert "<= -0.25" in mb._emotion_clause("negative")
    assert "AND importance < 0.75" in mb._emotion_clause("neutral")
    assert mb._emotion_clause("zzz") is None


def test_build_where_all_filters():
    clauses, args = mb._build_where(
        {
            "domain": "core",
            "stage": "labile",
            "search": "foo",
            "min_heat": "0.4",
            "emotion": "urgent",
            "protected": "true",
            "global": "1",
        }
    )
    assert "(domain = %s OR is_global = TRUE)" in clauses
    assert "is_protected = TRUE" in clauses and "is_global = TRUE" in clauses
    assert "importance >= 0.75" in clauses
    assert args == ["core", "labile", "foo", 0.4]
    clauses, args = mb._build_where(
        {"domain": "d", "include_global": "0", "min_heat": "abc"}
    )
    assert clauses == ["NOT is_stale", "domain = %s"] and args == ["d"]


def test_excerpt_iso_and_row_to_item():
    assert mb._excerpt("a  b\nc") == "a b c"
    assert mb._excerpt("x" * 100).endswith("…") and len(mb._excerpt("x" * 100)) == 90
    ts = datetime(2026, 1, 1, 12)
    assert (
        mb._iso(ts) == ts.isoformat() and mb._iso("s") == "s" and mb._iso(None) is None
    )
    item = mb._row_to_item(
        {
            "id": 3,
            "content": "hello",
            "domain": "core",
            "tags": '["x"]',
            "heat_base": 0.7,
            "consolidation_stage": "labile",
            "created_at": ts,
            "is_protected": 1,
        }
    )
    assert item["id"] == "memory:3" and item["domain_id"] == "domain:core"
    assert item["tags"] == ["x"] and item["createdAt"] == ts.isoformat()
    assert item["isProtected"] is True and item["isGlobal"] is False
    item = mb._row_to_item({"id": 4, "tags": "{bad", "domain": None, "content": None})
    assert item["tags"] == [] and item["domain_id"] == "" and item["label"] == ""


def test_list_memories_page_first_and_next_page():
    ts = datetime(2026, 5, 5)
    rows = [
        {"id": 2, "content": "b", "heat_base": 0.8, "created_at": ts},
        {"id": 1, "content": "a", "heat_base": None, "created_at": ts},
    ]
    store = FakeStore(rows=rows)
    page = mb.list_memories_page(store, {"limit": "2"})
    assert page["page_count"] == 2 and page["sort"] == "heat"
    cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    assert cursor == {"k": 0.0, "id": 1}
    sql, args = store.calls[-1]
    assert "ORDER BY COALESCE(heat_base, 0) DESC, id DESC" in sql and args[-1] == 2

    page2 = mb.list_memories_page(
        store, {"limit": "2", "cursor": page["next_cursor"], "sort": "oldest"}
    )
    sql, args = store.calls[-1]
    assert "(created_at, id) > (%s, %s)" in sql
    assert page2["sort"] == "oldest"
    assert (
        json.loads(base64.urlsafe_b64decode(page2["next_cursor"]))["k"]
        == ts.isoformat()
    )

    mb.list_memories_page(
        store, {"sort": "recent", "cursor": page["next_cursor"], "limit": "x"}
    )
    sql, args = store.calls[-1]
    assert "(created_at, id) < (%s, %s)" in sql and args[-1] == 50

    short = mb.list_memories_page(FakeStore(rows=rows[:1]), {"limit": 5})
    assert short["next_cursor"] is None
    unknown = mb.list_memories_page(FakeStore(), {"sort": "bogus"})
    assert unknown["items"] == [] and unknown["next_cursor"] is None


def test_memory_facets():
    agg = [
        {
            "total": 10,
            "s_labile": 1,
            "s_early": 2,
            "s_late": 3,
            "s_cons": 4,
            "s_recon": 0,
            "n_global": 2,
            "n_protected": 1,
            "n_hot": 5,
            "e_urgent": 1,
            "e_pos": 2,
            "e_neg": 3,
            "e_neutral": 4,
        }
    ]
    store = FakeStore(agg=agg, domains=[{"dom": "core", "c": 7}])
    out = mb.memory_facets(store)
    assert out["total"] == 10 and out["domains"] == [{"name": "core", "count": 7}]
    assert out["stages"]["consolidated"] == 4
    assert out["emotions"] == {"urgent": 1, "positive": 2, "negative": 3, "neutral": 4}
    assert (out["global"], out["protected"], out["hot"]) == (2, 1, 5)
    empty = mb.memory_facets(FakeStore())
    assert empty["total"] == 0 and empty["stages"]["labile"] == 0
