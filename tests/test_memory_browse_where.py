"""Tests for the memory-browse WHERE assembly.

``_build_where`` returns a (clauses, args) pair that the caller joins into a
parameterised query. The pair carries one invariant that nothing else checks:
the number of ``%s`` placeholders across the clauses must equal ``len(args)``.
A mismatch is not a wrong result — psycopg refuses the query outright, so a
single malformed filter takes down the whole request.
"""

from __future__ import annotations

import pytest

from cortex_viz.infrastructure.memory_browse import _build_where


def _placeholders(clauses: list[str]) -> int:
    return " AND ".join(clauses).count("%s")


def _assert_balanced(params: dict) -> tuple[list[str], list]:
    clauses, args = _build_where(params)
    assert _placeholders(clauses) == len(args), (
        f"placeholder/arg mismatch for {params!r}: "
        f"{_placeholders(clauses)} placeholders vs {len(args)} args "
        f"-- clauses={clauses!r} args={args!r}"
    )
    return clauses, args


# ── the regression ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["abc", "", "  ", "1.2.3", "NaN%", "1e", "-"])
def test_unparseable_min_heat_leaves_the_pair_balanced(bad: str) -> None:
    """A min_heat that float() rejects must drop the filter entirely.

    Fails on the pre-fix code for every value float() rejects: the clause was
    appended before the conversion, so the WHERE kept a %s the args list had
    no value for.
    """
    _assert_balanced({"min_heat": bad})


def test_unparseable_min_heat_drops_the_heat_clause() -> None:
    """Dropping means dropping — not a clause with a missing arg, and not a
    filter silently coerced to some default threshold."""
    clauses, args = _build_where({"min_heat": "abc"})
    assert not any("heat_base" in c for c in clauses)
    assert args == []


def test_valid_min_heat_is_applied() -> None:
    clauses, args = _assert_balanced({"min_heat": "0.5"})
    assert any("heat_base" in c for c in clauses)
    assert args == [0.5]


@pytest.mark.parametrize("value,expected", [("0.5", 0.5), (0.75, 0.75), (2, 2.0)])
def test_min_heat_accepts_str_and_numeric_forms(value, expected) -> None:
    """Query params arrive as strings; internal callers pass numbers."""
    _, args = _assert_balanced({"min_heat": value})
    assert args == [expected]


# ── the invariant across every filter combination ────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"domain": "proj"},
        {"domain": "proj", "include_global": "0"},
        {"stage": "Early_ltp"},
        {"search": "login bug"},
        {"emotion": "urgent"},
        {"emotion": "not-a-bucket"},
        {"protected": "1"},
        {"global": "1"},
        {"min_heat": "0.5"},
        {"min_heat": "abc"},
        {
            "domain": "proj",
            "stage": "Early_ltp",
            "search": "x",
            "min_heat": "abc",
            "emotion": "urgent",
            "protected": "1",
            "global": "1",
        },
    ],
)
def test_placeholders_always_match_args(params: dict) -> None:
    """Every filter combination, including the malformed ones, keeps the
    placeholder/arg pair balanced."""
    _assert_balanced(params)


def test_no_filters_still_excludes_stale() -> None:
    """The one unconditional clause carries no placeholder."""
    clauses, args = _assert_balanced({})
    assert clauses == ["NOT is_stale"]
    assert args == []
