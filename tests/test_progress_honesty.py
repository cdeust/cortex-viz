"""The build must not assert progress it does not measure (issue #90).

``pct`` for the DrL bake is a literal set BEFORE one opaque blocking call.
Measured on a 128k-node baseline it held 0.29 for ~13 minutes while the
client rendered a frozen bar and users read it as a crash. The contract is
now: a phase that cannot measure itself sets ``indeterminate``, and
``get_build_progress`` derives a per-phase elapsed clock at READ time so
successive polls return a CHANGING number.
"""

from __future__ import annotations

from cortex_viz.server import graph_cache_state as state
from cortex_viz.server.graph_appliers import get_build_progress
from cortex_viz.server.graph_build_helpers import _set_progress


def _reset() -> None:
    with state._build_progress_lock:
        state._build_progress.clear()


def test_phase_transition_stamps_a_phase_clock() -> None:
    _reset()
    _set_progress(phase="loading memories", pct=0.1)
    snap = get_build_progress()
    assert "phase_elapsed" in snap
    assert snap["phase_elapsed"] >= 0


def test_phase_elapsed_advances_between_polls_within_one_phase() -> None:
    """The signal that distinguishes "working" from "hung"."""
    _reset()
    _set_progress(phase="layout bake (DrL)", pct=0.29, indeterminate=True)
    first = get_build_progress()["phase_elapsed"]
    # Busy-wait on the same monotonic clock the helper uses, so the test
    # measures the derivation and not a sleep's scheduling accuracy.
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.01:
        pass
    second = get_build_progress()["phase_elapsed"]
    assert second > first


def test_phase_clock_restarts_on_the_next_phase() -> None:
    _reset()
    _set_progress(phase="layout bake (DrL)", pct=0.29, indeterminate=True)
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.02:
        pass
    aged = get_build_progress()["phase_elapsed"]
    _set_progress(phase="layout", pct=0.97)
    fresh = get_build_progress()["phase_elapsed"]
    assert fresh < aged


def test_indeterminate_does_not_leak_into_the_next_phase() -> None:
    """A later phase that CAN measure itself must not inherit the flag."""
    _reset()
    _set_progress(phase="layout bake (DrL)", pct=0.29, indeterminate=True)
    assert get_build_progress()["indeterminate"] is True
    _set_progress(phase="layout", pct=0.97)
    assert get_build_progress()["indeterminate"] is False


def test_same_phase_repeated_does_not_reset_its_clock() -> None:
    """Only a TRANSITION restarts the clock; a message update must not.

    Otherwise a phase that refreshes its message would keep resetting its
    own elapsed and never look like it was progressing.
    """
    _reset()
    _set_progress(phase="loading memories", pct=0.2)
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.02:
        pass
    before = get_build_progress()["phase_elapsed"]
    _set_progress(phase="loading memories", message="still going")
    after = get_build_progress()["phase_elapsed"]
    assert after >= before


def test_phase_started_at_is_not_leaked_on_the_wire() -> None:
    """The client gets a duration, not a server-local monotonic origin."""
    _reset()
    _set_progress(phase="loading memories", pct=0.2)
    assert "phase_started_at" not in get_build_progress()
