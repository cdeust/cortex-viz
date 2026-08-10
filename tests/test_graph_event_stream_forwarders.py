"""Contract for the module-level ``emit``/``close``/``reset`` forwarders.

Issue #134: a prior revision deleted these three forwarders on the premise
that they had no caller in this repository's history. They had four —
``graph_build_run.py`` and ``graph_build_merge.py`` import this module ONCE
(as ``_events`` / ``events``) and call the module-level functions for the
lifetime of a build. Every existing merge test (``test_graph_build_coverage_
contracts.py``) substitutes a ``SimpleNamespace(emit=MagicMock())`` for that
import, which is exactly why the deletion went unnoticed: the defect was in
how the caller *obtains* the stream, not in what it does with it. The last
test below exercises the real binding instead of a fake, so a call site and
this module cannot drift apart the same way again.
"""

from __future__ import annotations

from cortex_viz.server import graph_event_stream as ges


def _fresh_singleton():
    """Leave the process-wide singleton clean for the next test."""
    ges.reset()


class _FakeStream:
    """Stands in for the process singleton. ``GraphEventStream`` declares
    ``__slots__`` (no ``__dict__``), so its methods cannot be monkeypatched
    on a live instance — swap the module-level ``_stream`` binding instead,
    which is exactly what each forwarder reads through ``get_stream()``."""

    def __init__(self):
        self.emit_calls = []
        self.close_calls = 0
        self.reset_calls = 0

    def emit(self, *args, **kwargs):
        self.emit_calls.append((args, kwargs))
        return 3

    def close(self):
        self.close_calls += 1

    def reset(self):
        self.reset_calls += 1


def test_emit_forwarder_delegates_to_the_process_singleton(monkeypatch):
    fake = _FakeStream()
    monkeypatch.setattr(ges, "_stream", fake)
    result = ges.emit(
        "label", [{"id": "n1"}], [{"id": "e1"}], chunk=7, event_meta={"k": 1}
    )
    assert result == 3
    assert fake.emit_calls == [
        (
            ("label", [{"id": "n1"}], [{"id": "e1"}]),
            {"chunk": 7, "event_meta": {"k": 1}},
        )
    ]
    assert ges.get_stream() is fake


def test_close_forwarder_delegates_to_the_process_singleton(monkeypatch):
    fake = _FakeStream()
    monkeypatch.setattr(ges, "_stream", fake)
    ges.close()
    assert fake.close_calls == 1


def test_reset_forwarder_delegates_to_the_process_singleton(monkeypatch):
    fake = _FakeStream()
    monkeypatch.setattr(ges, "_stream", fake)
    ges.reset()
    assert fake.reset_calls == 1


def test_emit_forwarder_carries_event_meta_through_to_the_subscriber():
    """The exact drift that started the incident: `emit` grew `event_meta`
    and the forwarder silently dropped it. This must fail against the
    pre-#134-fix forwarder — which had no `event_meta` parameter at all —
    and pass against the restored one."""
    _fresh_singleton()
    try:
        ges.emit("activity", [{"id": "n1"}], [], event_meta={"activity_id": 42})

        (_idx, event) = next(iter(ges.get_stream().subscribe(since=0, timeout=0.01)))
        assert event["activity_id"] == 42
        assert event["label"] == "activity"
    finally:
        _fresh_singleton()


def test_production_build_binding_reaches_the_real_singleton():
    """Reproduces exactly what ``graph_build_run.py`` does: import this
    module once, then drive it through ``reset`` -> ``emit`` -> ``close`` as
    module-level calls (never touching ``get_stream()`` directly, the way a
    fake-stream test would). If a future change breaks the module/singleton
    binding, this is the test that catches it."""
    from cortex_viz.server import graph_event_stream as _events

    _events.reset()
    emitted = _events.emit("baseline", [{"id": "n1"}], [], chunk=1000)
    assert emitted == 1

    stream = _events.get_stream()
    events = [event for _idx, event in stream.subscribe(since=0, timeout=0.01)]
    assert len(events) == 1
    assert events[0]["label"] == "baseline"

    _events.close()
    assert stream.stats()["closed"] is True

    _events.reset()
    assert stream.stats() == {"count": 0, "closed": False}
