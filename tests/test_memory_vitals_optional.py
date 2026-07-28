"""Tests for the optional memory-vital reads on the /api/stats path.

``_compute_memory_vitals`` reports a dozen metrics that only newer stores can
answer. Each read used to be ``try: x = store.count_x() / except Exception:
pass``, which gave the same silent default for two unrelated situations: a
store that predates the metric (expected) and a store that has it but failed
the query (a live fault). The dashboard rendered both as a confident zero.

``_optional_vital`` splits them. These tests pin both arms *and* the signal
itself — a fallback that stopped logging would otherwise still pass every
value assertion, which is exactly how the original defect stayed invisible.
"""

from __future__ import annotations

import logging

import pytest

from cortex_viz.server.graph_discussions import _optional_vital


class _StoreWithout:
    """A store predating the vital — the attribute simply is not there."""


class _StoreWorking:
    def count_thing(self, **kw):
        return 42

    def takes_args(self, min_proficiency=None, limit=None):
        return [{"is_habitual": True}, {"is_habitual": False}]


class _StoreBroken:
    """A store that DEFINES every vital but fails answering it.

    Resolution goes through ``__getattr__`` so the double is not silently
    exercising the absent-method path when a test names a different vital —
    the distinction between "absent" and "present but failing" is the whole
    point of the helper, so the double has to get it right.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, name: str):
        def _raise(*args, **kwargs):
            raise self._exc

        return _raise


# ── arm 1: the store predates the vital ──────────────────────────────


def test_absent_method_returns_default(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        assert _optional_vital(_StoreWithout(), "count_thing", 0) == 0


def test_absent_method_is_silent(caplog) -> None:
    """The expected path must stay quiet, or the log fills with noise on
    every older store and real faults stop standing out (§13.1 F1)."""
    with caplog.at_level(logging.DEBUG):
        _optional_vital(_StoreWithout(), "count_thing", 0)
    assert caplog.records == []


@pytest.mark.parametrize(
    "default", [0, 0.0, {"nrem": 0, "rem": 0}, {"cue": None}, [], False]
)
def test_absent_method_returns_the_default_object_unchanged(default) -> None:
    """Defaults are dicts and lists as often as scalars; the fallback must
    hand back exactly what it was given."""
    assert _optional_vital(_StoreWithout(), "count_thing", default) == default


# ── arm 2: the store answers ─────────────────────────────────────────


def test_present_method_returns_its_value() -> None:
    assert _optional_vital(_StoreWorking(), "count_thing", 0) == 42


def test_present_method_is_silent(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        _optional_vital(_StoreWorking(), "count_thing", 0)
    assert caplog.records == []


def test_extra_args_reach_the_store_method() -> None:
    """``list_procedural_skills`` is read with keyword arguments; they must
    survive the indirection."""
    rows = _optional_vital(
        _StoreWorking(), "takes_args", [], min_proficiency=0.0, limit=1000
    )
    assert len(rows) == 2


# ── arm 3: the store has it and fails ────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("connection already closed"),
        ValueError("column heat_base does not exist"),
        KeyError("nrem"),
        TypeError("unhashable"),
    ],
)
def test_failing_method_falls_back_to_default(exc: Exception) -> None:
    assert _optional_vital(_StoreBroken(exc), "count_thing", 7) == 7


def test_failing_method_emits_a_warning(caplog) -> None:
    """The emission is the point of the fix, so it is asserted directly and
    not via a downstream effect (§13.1 F1)."""
    with caplog.at_level(logging.WARNING):
        _optional_vital(
            _StoreBroken(RuntimeError("connection already closed")),
            "count_thing",
            0,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING


def test_warning_names_the_method_the_cause_and_the_fallback(caplog) -> None:
    """A degraded reading has to be traceable from the log line alone: which
    vital degraded, what was reported instead, and why."""
    with caplog.at_level(logging.WARNING):
        _optional_vital(
            _StoreBroken(RuntimeError("connection already closed")),
            "count_habituated_repeats",
            0,
        )
    msg = caplog.text
    assert "count_habituated_repeats" in msg
    assert "RuntimeError" in msg
    assert "connection already closed" in msg


def test_a_failing_vital_does_not_abort_the_whole_payload() -> None:
    """One broken metric must not take the other eleven with it — the
    endpoint degrades per-metric, not wholesale."""
    broken = _StoreBroken(RuntimeError("boom"))
    assert _optional_vital(broken, "count_thing", 0) == 0
    assert _optional_vital(_StoreWorking(), "count_thing", 0) == 42
