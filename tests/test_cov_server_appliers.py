"""Behaviour tests for graph_appliers, graph_build and build_process."""

from __future__ import annotations

import copy
import os
import pickle
import queue as _queue
import threading
import time
import types

import pytest

from cortex_viz.server import build_process, graph_build
from cortex_viz.server import graph_appliers as ga
from cortex_viz.server import graph_cache_state as state


@pytest.fixture(autouse=True)
def _isolated_state():
    saved = {
        "cache": state._graph_cache,
        "epoch": state._SERVER_EPOCH,
        "phases": copy.deepcopy(state.PHASES),
        "payloads": copy.deepcopy(state._phase_payloads),
        "progress": copy.deepcopy(state._build_progress),
        "fp": state._graph_roster_fingerprint,
        "sink": state._SINK_Q,
        "be": state._BUILD_EPOCH,
    }
    yield
    state._graph_cache = saved["cache"]
    state._SERVER_EPOCH = saved["epoch"]
    state._BUILD_EPOCH = saved["be"]
    state.PHASES.clear()
    state.PHASES.update(saved["phases"])
    state._phase_payloads.clear()
    state._phase_payloads.update(saved["payloads"])
    state._build_progress.clear()
    state._build_progress.update(saved["progress"])
    state._graph_roster_fingerprint = saved["fp"]
    state._SINK_Q = saved["sink"]
    state._applied_node_ids.clear()
    state._node_index.clear()
    state._adjacency.clear()


def _n(i, kind="file", **kw):
    return {"id": i, "kind": kind, "label": i.upper(), **kw}


def test_begin_epoch_resets_everything():
    state.PHASES["L6:x"] = {"deps": [], "ready": True, "label": "x"}
    state._phase_payloads["L6:x"] = {"nodes": [1], "edges": []}
    state.PHASES["L0"]["ready"] = True
    state._phase_payloads["L0"] = {"nodes": [1], "edges": [2]}
    state._node_index["a"] = {}
    ga.begin_epoch(7)
    assert state._SERVER_EPOCH == 7
    assert state._graph_cache["data"]["nodes"] == []
    assert "L6:x" not in state.PHASES and "L6:x" not in state._phase_payloads
    assert state.PHASES["L0"]["ready"] is False
    assert state._phase_payloads["L0"] == {"nodes": [], "edges": []}
    assert not state._node_index
    assert state._build_progress["phase"] == "starting"
    assert state._build_progress["phases"]["L0"] is False


def test_apply_progress_drops_stale_and_server_clock_fields():
    ga.begin_epoch(1)
    ga.apply_progress(2, {"phase": "nope"})
    assert state._build_progress["phase"] == "starting"
    state._build_progress["phase_seq"] = 5
    ga.apply_progress(
        1,
        {
            "phase": "loading",
            "started_at": -1.0,
            "phase_started_at": -2.0,
            "phase_seq": 3,
        },
    )
    assert state._build_progress["phase"] == "loading"
    assert state._build_progress["started_at"] != -1.0
    assert state._build_progress["phase_started_at"] != -2.0
    ga.apply_progress(1, {"phase_seq": 9})
    assert state._build_progress["phase_seq"] == 9


def test_apply_delta_appends_dedups_and_indexes():
    ga.begin_epoch(1)
    ga.apply_delta(99, "L3", "baseline", [_n("a")], [])  # stale
    assert state._graph_cache["data"]["nodes"] == []
    nodes = [_n("a"), _n("b"), _n("a"), {"kind": "x"}]
    edges = [{"source": "a", "target": "b", "kind": "calls"}, {"kind": "loose"}]
    ga.apply_delta(1, "L3", "baseline", nodes, edges)
    data = state._graph_cache["data"]
    assert [n["id"] for n in data["nodes"]] == ["a", "b"]
    assert data["links"] is data["edges"]
    assert data["meta"]["node_count"] == 2 and data["meta"]["edge_count"] == 2
    assert state._node_index["a"]["label"] == "A"
    assert state._adjacency["a"] == [("b", "calls", "out")]
    assert state._adjacency["b"] == [("a", "calls", "in")]
    assert len(state._phase_payloads["L3"]["nodes"]) == 2
    ga.apply_delta(1, None, "baseline", [_n("a"), _n("c")], [])
    assert [n["id"] for n in data["nodes"]] == ["a", "b", "c"]


def test_apply_delta_without_seeded_cache():
    ga.begin_epoch(1)
    state._graph_cache = None
    ga.apply_delta(1, None, "s", [_n("z")], [])
    assert state._graph_cache["data"]["nodes"][0]["id"] == "z"


def test_apply_phase_ready_creates_and_flips():
    ga.begin_epoch(1)
    ga.apply_phase_ready(5, "L0", 4)
    assert state.PHASES["L0"]["ready"] is False
    ga.apply_phase_ready(1, "L0", 4)
    assert state.PHASES["L0"]["ready"] is True
    ga.apply_phase_ready(1, "L6:new", 2)
    assert state.PHASES["L6:new"]["ready"] is True
    assert state._build_progress["phase_seq"] == 4
    assert state._build_progress["phases"]["L6:new"] is True


def test_apply_done_statuses():
    ga.begin_epoch(1)
    ga.apply_done(2, "ok")
    assert state._build_progress["full_ready"] is False
    ga.apply_done(1, "ok")
    assert state._build_progress["phase"] == "full_ready"
    assert state._build_progress["full_ready"] is True
    ga.apply_done(1, "killed")
    assert state._build_progress["phase"] == "killed"
    assert state._build_progress["message"] == "build killed"
    assert state._build_progress["full_ready"] is False


def test_apply_graph_replace_and_neighbors():
    ga.begin_epoch(1)
    data = {
        "nodes": [_n("a"), _n("b", kind="symbol"), {"kind": "noid"}],
        "edges": [
            {"source": "a", "target": "b", "kind": "defined_in"},
            {"source": "a", "target": "c", "kind": "calls"},
            {"kind": "x"},
        ],
    }
    ga.apply_graph_replace(3, data)
    assert state._graph_cache["data"]["nodes"] == []
    ga.apply_graph_replace(1, data)
    assert ga.get_node_record("a")["label"] == "A"
    assert ga.get_node_record("zz") is None
    page = ga.get_node_neighbors("a", offset=0, limit=1)
    assert page["total"] == 2 and page["next_offset"] == 1
    assert page["neighbors"] == [["b", "symbol", "B", "defined_in", "out"]]
    page = ga.get_node_neighbors("a", offset=-5, limit=0)
    assert page["offset"] == 0 and page["next_offset"] == 1
    last = ga.get_node_neighbors("a", offset=1, limit=10)
    assert last["next_offset"] is None
    assert last["neighbors"] == [["c", None, None, "calls", "out"]]


def test_get_build_progress_derives_elapsed():
    with state._build_progress_lock:
        state._build_progress["started_at"] = time.monotonic() - 10
        state._build_progress["phase_started_at"] = time.monotonic() - 3
    snap = ga.get_build_progress()
    assert snap["elapsed"] >= 10
    assert 3 <= snap["phase_elapsed"] < 10
    assert "phase_started_at" not in snap
    assert "phase_started_at" in state._build_progress


def test_get_graph_slice_pages():
    ga.begin_epoch(1)
    ga.apply_delta(1, None, "s", [_n(f"n{i}") for i in range(5)], [])
    s = ga.get_graph_slice(offset=0, limit=2)
    assert len(s["nodes"]) == 2 and s["node_total"] == 5 and s["done"] is False
    s = ga.get_graph_slice(offset=-1, limit=100)
    assert s["done"] is True and s["offset"] == 0
    state._graph_cache = None
    assert ga.get_graph_slice()["node_total"] == 0


def test_get_phase_payload_derives_from_cache_by_kind():
    ga.begin_epoch(1)
    nodes = [
        _n("d1", "domain"),
        _n("d2", "domain"),
        _n("f1", "file"),
        {"id": "m", "type": "memory"},
    ]
    edges = [
        {"source": "d1", "target": "d2", "kind": "k"},
        {"source": "d1", "target": "f1", "kind": "in_domain"},
    ]
    ga.apply_delta(1, None, "baseline", nodes, edges)
    out = ga.get_phase_payload("L0")
    assert out["node_total"] == 2 and out["edge_total"] == 1
    assert out["limit"] is None and out["done"] is True
    assert out["ready"] is True and out["deps"] == []
    out = ga.get_phase_payload("L5")
    assert [n["id"] for n in out["nodes"]] == ["m"]
    paged = ga.get_phase_payload("L0", offset=0, limit=1)
    assert len(paged["nodes"]) == 1 and paged["done"] is False
    unk = ga.get_phase_payload("nope")
    assert unk["ready"] is False and unk["deps"] == [] and unk["node_total"] == 0
    assert ga.get_phase_payload("L4")["node_total"] == 0
    state._graph_cache = {"nodes": [_n("q", "skill")], "edges": []}
    assert ga.get_phase_payload("L1")["node_total"] == 1
    state._graph_cache = None
    assert ga.get_phase_payload("L1")["node_total"] == 0


# graph_build


def test_ensure_build_started_noop_when_cache_populated(monkeypatch):
    state._graph_cache = {"data": {"nodes": [_n("a")]}}
    called = []
    monkeypatch.setattr(build_process, "start_build", lambda *a: called.append(a))
    graph_build.ensure_build_started(types.SimpleNamespace(_url="pg://x"))
    assert called == []


def test_ensure_build_started_spawns_with_url(monkeypatch):
    state._graph_cache = None
    called = []
    monkeypatch.setattr(build_process, "start_build", lambda *a: called.append(a))
    monkeypatch.setattr(graph_build, "_roster_fingerprint", lambda: (("p", 1, 2),))
    graph_build.ensure_build_started(types.SimpleNamespace(_url="pg://x"))
    assert called == [("pg://x", None)]
    assert state._graph_roster_fingerprint == (("p", 1, 2),)


def test_ensure_build_started_degrades_without_url():
    state._graph_cache = None
    graph_build.ensure_build_started(types.SimpleNamespace())
    assert state._build_progress["phase"] == "degraded"
    assert "no DB url" in state._build_progress["message"]


def test_kick_background_build_runs_once(monkeypatch):
    ran = threading.Event()
    seen = []

    def fake(store, df):
        seen.append((store, df))
        ran.set()

    monkeypatch.setattr(graph_build, "run_build", fake)
    assert state._graph_build_lock.acquire(blocking=False)
    try:
        graph_build._kick_background_build("S", "d")  # lock held: no-op
        assert not ran.wait(0.2)
    finally:
        state._graph_build_lock.release()
    graph_build._kick_background_build("S", "d")
    assert ran.wait(2)
    assert seen == [("S", "d")]
    state._graph_build_lock.release()


# build_process


class FakeProc:
    def __init__(self, alive=True, stubborn=False):
        self.alive = alive
        self.stubborn = stubborn
        self.calls = []

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.calls.append("terminate")
        if not self.stubborn:
            self.alive = False

    def kill(self):
        self.calls.append("kill")
        self.alive = False

    def join(self, timeout=None):
        self.calls.append("join")


@pytest.fixture
def clean_proc():
    old = build_process._proc, build_process._epoch
    build_process._proc = None
    yield
    build_process._proc, build_process._epoch = old


def test_kill_current_build_variants(clean_proc):
    build_process.kill_current_build()
    assert build_process._proc is None
    dead = FakeProc(alive=False)
    build_process._proc = dead
    build_process.kill_current_build()
    assert build_process._proc is None and dead.calls == []
    p = FakeProc()
    build_process._proc = p
    build_process.kill_current_build()
    assert p.calls[0] == "terminate" and "kill" not in p.calls
    assert build_process._proc is None
    s = FakeProc(stubborn=True)
    build_process._proc = s
    build_process.kill_current_build()
    assert "kill" in s.calls and build_process._proc is None


def test_start_build_refuses_while_alive(clean_proc):
    build_process._proc = FakeProc()
    assert build_process.start_build("pg://x", None) is False


def test_start_build_spawns_child_and_drain(clean_proc, monkeypatch):
    started = {}

    class Ctx:
        def Queue(self, maxsize):  # noqa: N802 - mimics the mp context API
            started["maxsize"] = maxsize
            return "Q"

        def Process(self, target, args, name, daemon):  # noqa: N802 - mp API
            started["args"] = args
            started["name"] = name
            return types.SimpleNamespace(start=lambda: started.setdefault("go", True))

    monkeypatch.setattr(build_process.mp, "get_context", lambda m: Ctx())
    drained = threading.Event()
    monkeypatch.setattr(build_process, "_drain", lambda *a: drained.set())
    assert build_process.start_build("pg://x", "dom") is True
    assert drained.wait(2)
    assert started["go"] and started["maxsize"] == 4096
    assert started["args"][1:] == ("pg://x", "dom", build_process._epoch)
    assert build_process._epoch == state._SERVER_EPOCH
    assert build_process._proc is not None


def _drain_with(msgs, alive=False, epoch=1):
    ga.begin_epoch(epoch)
    q = _queue.Queue()
    for m in msgs:
        q.put(m)
    proc = FakeProc(alive=alive)
    build_process._proc = proc
    build_process._drain(q, proc, epoch)
    return proc


def test_drain_applies_all_message_kinds(clean_proc, tmp_path):
    path = tmp_path / "g.pkl"
    graph = {"nodes": [_n("g1")], "edges": []}
    path.write_bytes(pickle.dumps(graph))
    stale_path = tmp_path / "stale.pkl"
    stale_path.write_bytes(b"x")
    gone = str(tmp_path / "missing.pkl")
    msgs = [
        ("progress", 1, {"phase": "loading"}),
        ("delta", 1, "L3", "baseline", [_n("d1")], []),
        ("phase_ready", 1, "L3", 2),
        ("progress", 2, {"phase": "stale"}),
        ("graph_file", 2, str(stale_path)),
        ("graph_file", 2, gone),
        ("graph_file", 1, str(path)),
        ("done", 2, "ok"),
    ]
    proc = _drain_with(msgs)
    assert not stale_path.exists() and not path.exists()
    assert state._build_progress["phase"] == "loading"
    assert state.PHASES["L3"]["ready"] is True
    assert [n["id"] for n in state._graph_cache["data"]["nodes"]] == ["g1"]
    assert build_process._proc is None
    assert "join" in proc.calls


def test_drain_done_default_status_is_ok(clean_proc):
    _drain_with([("done", 1)])
    assert state._build_progress["phase"] == "full_ready"


def test_drain_watchdog_marks_killed(clean_proc):
    class EmptyQ:
        def __init__(self):
            self.n = 0

        def get(self, timeout):
            self.n += 1
            raise _queue.Empty

    ga.begin_epoch(1)
    proc = FakeProc(alive=True)
    q = EmptyQ()
    orig = proc.is_alive

    def flip():
        r = orig()
        proc.alive = False
        return r

    proc.is_alive = flip
    build_process._drain(q, proc, 1)
    assert q.n == 2
    assert state._build_progress["phase"] == "killed"


def test_drain_exits_on_eof_and_survives_apply_error(clean_proc):
    class BadQ:
        def get(self, timeout):
            raise EOFError

    build_process._drain(BadQ(), FakeProc(), 1)
    ga.begin_epoch(1)
    q = _queue.Queue()
    q.put(("graph_file", 1, "/nonexistent/file.pkl"))
    q.put(("done", 1, "ok"))
    build_process._drain(q, FakeProc(), 1)
    assert state._build_progress["phase"] == "full_ready"


def test_drain_join_failure_ignored(clean_proc):
    class P(FakeProc):
        def join(self, timeout=None):
            raise RuntimeError("reaped")

    ga.begin_epoch(1)
    q = _queue.Queue()
    q.put(("done", 1, "ok"))
    build_process._drain(q, P(), 1)
    assert state._build_progress["phase"] == "full_ready"


def test_worker_success_and_error(monkeypatch):
    from cortex_viz.infrastructure import memory_read
    from cortex_viz.server import http_standalone

    monkeypatch.setattr(http_standalone, "_auto_enable_ap", lambda: None)
    monkeypatch.setattr(memory_read, "MemoryReader", lambda url: ("reader", url))
    kicked = []
    monkeypatch.setattr(
        graph_build, "_kick_background_build", lambda s, d: kicked.append((s, d))
    )
    q = _queue.Queue()
    state._graph_cache = {"data": {"nodes": [_n("w")], "edges": []}}
    build_process._worker(q, "pg://u", "dom", 4)
    assert kicked == [(("reader", "pg://u"), "dom")]
    assert state._BUILD_EPOCH == 4 and state._SINK_Q is q
    kind, ep, path = q.get_nowait()
    assert (kind, ep) == ("graph_file", 4)
    with open(path, "rb") as fh:
        assert pickle.load(fh)["nodes"][0]["id"] == "w"
    os.unlink(path)
    assert q.get_nowait() == ("done", 4, "ok")

    state._graph_cache = None
    q2 = _queue.Queue()
    build_process._worker(q2, "pg://u", None, 5)
    assert q2.get_nowait() == ("done", 5, "ok")

    def boom(s, d):
        raise RuntimeError("kaput")

    monkeypatch.setattr(graph_build, "_kick_background_build", boom)
    q3 = _queue.Queue()
    build_process._worker(q3, "pg://u", None, 6)
    assert q3.get_nowait() == ("progress", 6, {"phase": "error", "message": "kaput"})
    assert q3.get_nowait() == ("done", 6, "error")
