"""Tests for the CORTEX_MCP_CALL_TIMEOUT_S override.

The override guards against an unbounded ``tools/call`` wait, so a rejected
value silently falling back to the 600s default is a configuration error the
operator cannot see: they set the variable, the process ignored it, and
nothing said so. These tests pin the fallback value AND the warning that
makes it visible.
"""

from __future__ import annotations

import logging

import pytest

from cortex_viz.infrastructure.mcp_call_timeout import (
    _DEFAULT_CALL_TIMEOUT_S,
    _ENV_VAR,
    default_call_timeout_s,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)


# ── the honoured path ────────────────────────────────────────────────


def test_unset_returns_the_default(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        assert default_call_timeout_s() == _DEFAULT_CALL_TIMEOUT_S
    assert caplog.records == [], "an absent override is not a misconfiguration"


@pytest.mark.parametrize("raw,expected", [("1", 1.0), ("0.5", 0.5), ("900", 900.0)])
def test_valid_override_is_honoured_quietly(raw, expected, monkeypatch, caplog) -> None:
    monkeypatch.setenv(_ENV_VAR, raw)
    with caplog.at_level(logging.DEBUG):
        assert default_call_timeout_s() == expected
    assert caplog.records == []


def test_empty_string_is_treated_as_unset(monkeypatch, caplog) -> None:
    """An exported-but-empty variable is how shells spell "not set"; it must
    not be reported as a bad value."""
    monkeypatch.setenv(_ENV_VAR, "")
    with caplog.at_level(logging.DEBUG):
        assert default_call_timeout_s() == _DEFAULT_CALL_TIMEOUT_S
    assert caplog.records == []


# ── the rejected paths ───────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["6OO", "600s", "abc", "1,5", "--"])
def test_unparseable_override_falls_back_and_warns(raw, monkeypatch, caplog) -> None:
    monkeypatch.setenv(_ENV_VAR, raw)
    with caplog.at_level(logging.WARNING):
        assert default_call_timeout_s() == _DEFAULT_CALL_TIMEOUT_S
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING


@pytest.mark.parametrize("raw", ["0", "-1", "-600"])
def test_non_positive_override_falls_back_and_warns(raw, monkeypatch, caplog) -> None:
    """Zero or negative would disable the ceiling — the exact unbounded wait
    this module exists to prevent — so it is refused, loudly."""
    monkeypatch.setenv(_ENV_VAR, raw)
    with caplog.at_level(logging.WARNING):
        assert default_call_timeout_s() == _DEFAULT_CALL_TIMEOUT_S
    assert len(caplog.records) == 1


def test_warning_echoes_the_offending_value(monkeypatch, caplog) -> None:
    """The typo has to appear in the log line, or the operator still cannot
    tell which of their exports is wrong."""
    monkeypatch.setenv(_ENV_VAR, "6OO")
    with caplog.at_level(logging.WARNING):
        default_call_timeout_s()
    assert "6OO" in caplog.text
    assert _ENV_VAR in caplog.text


def test_warning_distinguishes_the_two_rejection_causes(monkeypatch, caplog) -> None:
    """ "not a number" and "not positive" are different operator mistakes and
    need different fixes, so the message must not collapse them."""
    monkeypatch.setenv(_ENV_VAR, "abc")
    with caplog.at_level(logging.WARNING):
        default_call_timeout_s()
    unparseable = caplog.text
    caplog.clear()

    monkeypatch.setenv(_ENV_VAR, "-1")
    with caplog.at_level(logging.WARNING):
        default_call_timeout_s()
    non_positive = caplog.text

    assert unparseable != non_positive
