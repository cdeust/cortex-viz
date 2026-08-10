"""#135: the end-of-build SSE terminator must report a failure, never mask it.

``run_build``'s ``finally`` sends the single ``close()`` end-of-stream call.
#134 made ``close()`` resolve again; this file exercises what happens the
*next* time it raises — the handler must not re-raise (a failure here must
not mask the build outcome being unwound) but must no longer swallow the
failure silently either, since that silence is exactly why #134's
``AttributeError`` on this call survived a release unnoticed.

``run_build`` is exercised directly (not through a fake), forcing the very
first line of its ``try`` to fail so the test reaches ``finally`` without
depending on a real store or database.

``graph_event_stream.close`` is patched with ``raising=False``: this file is
independent of #134 (a separate PR against the same base), and whether the
module-level ``close`` forwarder exists yet depends on merge order, not on
this fix. Both scenarios must behave identically here — the finally block
must report a failing terminator regardless of *why* it failed.
"""

from __future__ import annotations

from cortex_viz.server import graph_build_run
from cortex_viz.server import graph_cache_state as state
from cortex_viz.server import graph_event_stream as ges


def _run_with_forced_early_failure(monkeypatch):
    """Force ``run_build``'s try block to fail on its first statement, so
    the finally block is reached without a store, a DB, or a real build."""

    def _boom():
        raise RuntimeError("forced early build failure")

    monkeypatch.setattr(graph_build_run, "_roster_fingerprint", _boom)
    state._graph_build_lock.acquire()
    graph_build_run.run_build(store=object(), domain_filter=None)


def test_terminator_failure_is_reported_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(
        ges,
        "close",
        lambda: (_ for _ in ()).throw(AttributeError("no attribute 'close'")),
        raising=False,
    )

    _run_with_forced_early_failure(monkeypatch)

    err = capsys.readouterr().err
    assert "end-of-stream terminator failed" in err
    assert "AttributeError" in err
    assert "no attribute 'close'" in err
    # Not re-raised: the build's own error report is still the thing that
    # reaches stderr as the build outcome, and the lock is still released.
    assert "background build error" in err
    assert not state._graph_build_lock.locked()


def test_nominal_terminator_stays_quiet(monkeypatch, capsys):
    closed = []
    monkeypatch.setattr(ges, "close", lambda: closed.append(True), raising=False)

    _run_with_forced_early_failure(monkeypatch)

    err = capsys.readouterr().err
    assert closed == [True]
    assert "end-of-stream terminator failed" not in err
    assert not state._graph_build_lock.locked()
