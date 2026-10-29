"""Layout authority integrator, log, wire, scheduler and bench harness."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import pytest

from cortex_viz.server import bench_layout_authority as bench
from cortex_viz.server import layout_authority as la
from cortex_viz.server import layout_authority_log as log
from cortex_viz.server import layout_authority_scheduler as sch
from cortex_viz.server import layout_authority_wire as wire
from cortex_viz.server.layout_authority_protocol import EdgeDelta, NodeDelta


@pytest.fixture(autouse=True)
def _fresh_log():
    log.reset()
    yield
    log.reset()


@dataclass
class Slot:
    node_id: str = "n1"
    x: float = 1.26
    y: float = -2.0
    kind: str = "file"
    domain_id: str = "d"


# ── wire ──


def test_wire_slot_edge_done_frames_roundtrip():
    frame = wire.format_slot(7, Slot())
    assert frame == b"id: 7\nevent: slot\ndata: n1|1.3|-2.0|file|d\n\n"
    data = frame.split(b"data: ")[1].strip()
    assert wire.parse_slot(data) == ("n1", pytest.approx(1.3), -2.0, "file", "d")
    e = wire.format_edge(8, EdgeDelta("a", "b", "calls"))
    assert e == b"id: 8\nevent: edge\ndata: a|b|calls\n\n"
    assert wire.parse_edge(b"a|b|calls") == ("a", "b", "calls")
    assert wire.format_done(9, 3, 4) == b"id: 9\nevent: done\ndata: 3|4\n\n"
    assert wire.format_keepalive() == b": ping\n\n"
    assert wire.format_terminator() == b"0\r\n\r\n"
    assert wire.chunk_wrap(b"x" * 17) == b"11\r\n" + b"x" * 17 + b"\r\n"


@pytest.mark.parametrize(
    "slot",
    [
        Slot(node_id="a|b"),
        Slot(domain_id="a\nb"),
        Slot(kind="k|x"),
        Slot(kind="k" * 40),
        Slot(x=float("nan")),
        Slot(y=float("inf")),
    ],
)
def test_wire_rejects_bad_slots(slot):
    with pytest.raises(ValueError):
        wire.format_slot(1, slot)


def test_wire_rejects_bad_edge_done_chunk_and_parse():
    with pytest.raises(ValueError):
        wire.format_edge(1, EdgeDelta("a\r", "b", "calls"))
    with pytest.raises(ValueError):
        wire.format_edge(1, EdgeDelta("a", "b|", "calls"))
    with pytest.raises(ValueError):
        wire.format_done(1, -1, 0)
    with pytest.raises(ValueError):
        wire.chunk_wrap(b"")
    with pytest.raises(ValueError):
        wire.parse_slot(b"a|b")
    with pytest.raises(ValueError):
        wire.parse_edge(b"a|b")


def test_wire_benchmark_reports_positive_rates():
    mb, ns = wire._benchmark(200)
    assert mb > 0 and ns > 0


# ── log ──


def test_log_emit_replay_and_gap():
    s1 = log.emit("slot", b"a")
    s2 = log.emit("edge", b"b")
    assert s2 == s1 + 1
    events, oldest = log.replay_since(s1)
    assert events == [(s2, "edge", b"b")] and oldest == s1
    events, oldest = log.replay_since(s1 - 5)
    assert events == [] and oldest == s1  # gap: requested before oldest-1
    st = log.stats()
    assert st["size"] == 2 and st["newest_seq"] == s2 and st["oldest_seq"] == s1
    log.reset()
    assert log.replay_since(0) == ([], 0)
    assert log.stats()["size"] == 0


def test_log_ring_overflow_counts_drops(monkeypatch):
    import collections

    monkeypatch.setattr(log, "_event_log", collections.deque(maxlen=2))
    for _ in range(4):
        log.emit("slot", b"x")
    assert log.stats()["drops"] == 2 and log.stats()["size"] == 2


def test_subscribe_receives_and_unsubscribe_is_idempotent():
    q = log.subscribe()
    seq = log.emit("slot", b"p")
    assert q.get_nowait() == (seq, "slot", b"p")
    assert log.stats()["subscribers"] == 1
    log.unsubscribe(q)
    log.unsubscribe(q)
    log.emit("slot", b"p2")
    assert q.empty()


def test_dead_subscriber_is_reaped_after_threshold(monkeypatch):
    monkeypatch.setattr(log, "_SUBSCRIBER_QUEUE_CAP", 1)
    q = log.subscribe()
    assert q.maxsize == 1
    log.emit("slot", b"fill")
    for _ in range(log._DEAD_QUEUE_MISS_THRESHOLD):
        log.emit("slot", b"x")
    assert log.stats()["subscribers"] == 1  # misses == threshold: still alive
    log.emit("slot", b"x")
    assert log.stats()["subscribers"] == 0
    # reaping an already-removed queue is harmless
    log._reap([q])


def test_miss_counter_tolerates_locked_queue():
    class Locked(queue.Queue):
        def __setattr__(self, k, v):
            if k == "_cortex_misses":
                raise AttributeError("locked")
            super().__setattr__(k, v)

    q = Locked()
    assert log._record_miss(q) == 1
    log._clear_misses(q)


# ── scheduler ──


def test_priority_mapping():
    assert sch.priority_for_node("domain") == 0
    assert sch.priority_for_node("tool_hub") == 1
    assert sch.priority_for_node("file") == 2
    assert sch.priority_for_node("symbol") == 4
    assert sch.priority_for_node("memory") == 3
    assert sch.priority_for_edge() == 5


def test_scheduler_submit_pop_strict_priority_and_stats():
    s = sch.PriorityScheduler()
    with pytest.raises(ValueError):
        s.submit(99, "x")
    assert s.submit(4, "sym") and s.submit(0, "dom") and s.submit(5, "edge")
    assert s.total_pending() == 3
    assert s.pop(timeout=0) == (0, "dom")
    assert s.pop(timeout=0) == (4, "sym")
    assert s.pop(timeout=0) == (5, "edge")
    assert s.pop(timeout=0) is None
    assert s.pop(timeout=0.01) is None
    st = s.stats()
    assert st["queued"][0] == 1 and st["lengths"][5] == 0 and st["caps"][0] == 1000


def test_scheduler_drops_at_cap_and_overload(monkeypatch):
    monkeypatch.setitem(sch.QUEUE_SIZES, 2, 2)
    s = sch.PriorityScheduler()
    assert not s.is_overloaded()
    assert s.submit(2, "a") and s.submit(2, "b")
    assert s.submit(2, "c") is False
    assert s.stats()["dropped"][2] == 1
    assert s.is_overloaded()
    assert not s.is_overloaded(threshold=2.0)


def test_scheduler_coalesce_subtree(monkeypatch):
    s = sch.PriorityScheduler()
    assert s.coalesce_subtree("d1") is True
    assert s.coalesce_subtree("d1") is False
    assert s.stats()["lengths"][6] == 1
    monkeypatch.setitem(sch.QUEUE_SIZES, 6, 1)
    assert s.coalesce_subtree("d2") is False
    assert s.stats()["dropped"][6] == 1
    assert s.pop(timeout=0) == (6, "d1")


def test_scheduler_pop_blocks_until_submit():
    s = sch.PriorityScheduler()
    out = []
    t = threading.Thread(target=lambda: out.append(s.pop()))
    t.start()
    time.sleep(0.05)
    s.submit(3, "late")
    t.join(2)
    assert out == [(3, "late")]
    # bounded wait that wakes then succeeds
    out2 = []
    t2 = threading.Thread(target=lambda: out2.append(s.pop(timeout=2)))
    t2.start()
    time.sleep(0.05)
    s.submit(1, "hub")
    t2.join(3)
    assert out2 == [(1, "hub")]


# ── LayoutAuthority ──


def _events():
    evs, _ = (
        log.replay_since(0)
        if log.stats()["oldest_seq"] <= 1
        else (log.replay_since(log.stats()["oldest_seq"] - 1))
    )
    return evs


def _auth():
    return la.build_authority()


def test_validation_errors():
    a = _auth()
    bad_nodes = [
        NodeDelta("x", "nope", "d"),
        NodeDelta("", "file", "d"),
        NodeDelta("x", "file", ""),
        NodeDelta("domain:a", "domain", "domain:b"),
        NodeDelta("h", "tool_hub", "d"),
        NodeDelta("s", "symbol", "d"),
    ]
    for nd in bad_nodes:
        with pytest.raises(ValueError):
            a.add_node(nd)
    for ed in (EdgeDelta("a", "b", "zzz"), EdgeDelta("", "b", "calls")):
        with pytest.raises(ValueError):
            a.add_edge(ed)
    assert a.stats()["slots_emitted"] == 0


def test_slots_edges_and_symbol_buffering():
    a = _auth()
    q = a.subscribe()
    a.add_node(NodeDelta("d", "domain", "d"))
    a.add_node(NodeDelta("hub", "tool_hub", "d", tool_name="Edit"))
    # symbol before its file: buffered; edge to it: buffered
    a.add_node(NodeDelta("sym", "symbol", "d", parent_id="file"))
    a.add_edge(EdgeDelta("sym", "file", "defined_in"))
    a.add_edge(EdgeDelta("sym", "file", "defined_in"))  # dedup of pending key
    st = a.stats()
    assert st["pending_symbols"] == 1 and st["pending_edges"] == 1
    a.add_node(NodeDelta("file", "file", "d", parent_id="hub"))
    st = a.stats()
    assert st["pending_symbols"] == 0 and st["pending_edges"] == 0
    assert st["slots_emitted"] == 4 and st["edges_emitted"] == 1
    assert st["domains"] == 1
    kinds = []
    while not q.empty():
        kinds.append(q.get_nowait())
    ids = [wire_id for _, k, wire_id in kinds if k == "slot"]
    assert len(ids) == 4
    assert [e[1] for e in kinds].count("edge") == 1
    seqs = [e[0] for e in kinds]
    assert seqs == sorted(seqs)
    # direct edge when both endpoints known
    a.add_edge(EdgeDelta("file", "hub", "tool_used_file"))
    assert a.stats()["edges_emitted"] == 2
    a.unsubscribe(q)


def test_other_kinds_and_file_without_hub():
    a = _auth()
    a.add_node(NodeDelta("d", "domain", "d"))
    for i, k in enumerate(("skill", "memory", "entity", "discussion", "mcp")):
        a.add_node(NodeDelta(f"n{i}", k, "d"))
    a.add_node(NodeDelta("f", "file", "d"))
    assert a.stats()["slots_emitted"] == 7
    # a node whose domain was never announced still gets an anchor
    a.add_node(NodeDelta("m2", "memory", "other"))
    assert a.stats()["domains"] == 2


def test_domain_registry_grows_past_reservation():
    a = _auth()
    for i in range(la._DEFAULT_DOMAIN_RESERVATION + 3):
        a.add_node(NodeDelta(f"d{i}", "domain", f"d{i}"))
    assert a.stats()["domains"] == la._DEFAULT_DOMAIN_RESERVATION + 3
    reg = a._registry
    assert reg.total() == 2 * la._DEFAULT_DOMAIN_RESERVATION
    first = reg.anchor("d0")
    assert reg.anchor("d0") == first  # frozen anchors
    assert reg.base_r() > 0


def test_request_subtree_reemits_known_domain_only():
    a = _auth()
    a.request_subtree("nope")
    a.add_node(NodeDelta("d", "domain", "d"))
    a.add_node(NodeDelta("m", "memory", "d"))
    before = log.stats()["newest_seq"]
    a.request_subtree("unknown")
    assert log.stats()["newest_seq"] == before
    a.request_subtree("d")
    assert log.stats()["newest_seq"] == before + 2
    assert a.stats()["slots_emitted"] == 4


def test_done_closes_authority():
    a = _auth()
    a.add_node(NodeDelta("d", "domain", "d"))
    a.done()
    last = log.replay_since(log.stats()["oldest_seq"] - 1)[0][-1]
    assert last[1] == "done" and b"data: 1|0" in last[2]
    n = log.stats()["newest_seq"]
    a.done()
    a.add_node(NodeDelta("m", "memory", "d"))
    a.add_edge(EdgeDelta("d", "m", "in_domain"))
    a.request_subtree("d")
    assert log.stats()["newest_seq"] == n
    assert a.stats()["slots_emitted"] == 1


def test_pending_caps(monkeypatch):
    monkeypatch.setattr(la, "_PENDING_EDGES_CAP", 2)
    monkeypatch.setattr(la, "_PENDING_SYMBOLS_CAP_PER_FILE", 1)
    a = _auth()
    for i in range(4):
        a.add_edge(EdgeDelta(f"s{i}", f"t{i}", "calls"))
    st = a.stats()
    assert st["pending_edges"] == 2 and st["edges_dropped"] == 2
    a.add_node(NodeDelta("d", "domain", "d"))
    a.add_node(NodeDelta("s1", "symbol", "d", parent_id="f"))
    a.add_node(NodeDelta("s2", "symbol", "d", parent_id="f"))
    assert a.stats()["pending_symbols"] == 1
    a.add_node(NodeDelta("f", "file", "d"))
    assert a.stats()["pending_symbols"] == 0
    assert a.stats()["slots_emitted"] == 3  # domain, file, one symbol


# ── bench ──

SPEC = bench.WorkloadSpec(
    n_total=120,
    n_domains=3,
    n_tool_hubs=5,
    n_files=20,
    n_symbols=30,
    n_memories=20,
    n_entities=10,
    n_discussions=10,
)


def test_synthesize_kinds_mix_and_padding():
    kinds = bench.synthesize_kinds(SPEC)
    assert len(kinds) == 120
    assert kinds.count("domain") == 3 and kinds.count("symbol") == 30
    assert SPEC.padding() == 120 - 98
    assert {"skill", "hook", "command", "agent", "mcp"} <= set(kinds)
    assert bench.WorkloadSpec(n_total=5).padding() == 0
    assert len(bench.synthesize_kinds(bench.WorkloadSpec(n_total=5))) == 5


def test_precompute_anchors_shapes():
    anchors, outwards, base_r, cx, cy = bench.precompute_anchors(4)
    assert len(anchors) == len(outwards) == 4
    assert (cx, cy) == (500.0, 500.0) and base_r > 0


def test_measure_handles_zero_ops():
    r = bench._measure("x", 0, lambda: None)
    assert r["ns_per_op"] == float("inf")
    r = bench._measure("x", 10, lambda: None)
    assert r["n"] == 10 and r["ns_per_op"] >= 0


def test_component_benchmarks_return_results():
    for fn, label in (
        (bench.bench_geometry, "geometry.compute_slot"),
        (bench.bench_scheduler, "scheduler.submit+pop"),
        (bench.bench_log, "log.emit+replay_since"),
    ):
        r = fn(SPEC)
        assert r["label"] == label and r["elapsed_ns"] >= 0
    assert bench.bench_scheduler(SPEC)["n"] == 120 + 480
    assert bench.bench_log(SPEC)["n"] == 120


def test_integration_benchmark_emits_to_log():
    r = bench.bench_integration(SPEC)
    assert r["label"] == "pipeline.scheduler+log+wire"
    assert r["n"] == 600
    assert r["log_retained"] == 601 and r["sched_dropped"] == 0


def test_bench_main_prints_report(capsys):
    assert bench.main(["--n", "200"]) == 0
    out = capsys.readouterr().out
    assert "Workload: N=200 nodes, 800 edges" in out
    assert "Component bottleneck:" in out
    assert "log retained" in out
    line = bench._fmt({"label": "L", "n": 1000, "ns_per_op": 2.5, "ops_per_sec": 4e8})
    assert "1,000" in line and "2.5 ns/op" in line
