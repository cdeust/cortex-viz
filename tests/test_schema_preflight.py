"""Unit tests for the read-only graph-build schema preflight.

``check_schema`` (cortex_viz.infrastructure.schema_preflight) issues ONE
catalog SELECT; these tests fake ``pg_store.query`` to return canned
result rows shaped like that SELECT's boolean columns, exercising the
Python-side contract (all-present, one-missing, all-missing, empty
result) without a live database.
"""

from __future__ import annotations

from cortex_viz.infrastructure.schema_preflight import (
    _REQUIREMENTS,
    SchemaPreflightResult,
    check_schema,
)


class _FakeStore:
    """Records the SQL/params it was called with; returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, tuple, bool]] = []

    def query(self, sql, params=None, *, batch=False):
        self.calls.append((sql, params, batch))
        return self._rows


_ALL_PRESENT_ROW = {col: True for col, _, _ in _REQUIREMENTS}


def test_all_present_reports_ok_with_no_missing():
    store = _FakeStore(rows=[dict(_ALL_PRESENT_ROW)])
    result = check_schema(store)
    assert result == SchemaPreflightResult(ok=True, missing=())


def test_single_missing_object_is_reported():
    row = dict(_ALL_PRESENT_ROW)
    row["has_supersedes_id"] = False
    store = _FakeStore(rows=[row])
    result = check_schema(store)
    assert result.ok is False
    assert len(result.missing) == 1
    assert "memories.supersedes_id" in result.missing[0]
    assert "memory_supersede.py" in result.missing[0]


def test_all_missing_reports_every_requirement():
    row = {col: False for col, _, _ in _REQUIREMENTS}
    store = _FakeStore(rows=[row])
    result = check_schema(store)
    assert result.ok is False
    assert len(result.missing) == len(_REQUIREMENTS)


def test_empty_result_treated_as_all_missing_not_a_crash():
    store = _FakeStore(rows=[])
    result = check_schema(store)
    assert result.ok is False
    assert len(result.missing) == len(_REQUIREMENTS)


def test_query_is_read_only_single_round_trip():
    store = _FakeStore(rows=[dict(_ALL_PRESENT_ROW)])
    check_schema(store)
    assert len(store.calls) == 1
    sql, params, batch = store.calls[0]
    assert params == ()
    assert batch is False
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()
    assert "DROP" not in sql.upper()
    # Function-existence checks must never attempt to parse a typed
    # argument list (see module docstring) — that would error instead
    # of degrading gracefully on a schema missing the `memories` table.
    assert "to_regprocedure" not in sql


def test_missing_messages_name_every_requirement_column():
    """Every _REQUIREMENTS entry's description + loader must appear in
    the message when that column is false — guards against the SQL
    and the _REQUIREMENTS table drifting apart."""
    for col, desc, loader in _REQUIREMENTS:
        row = dict(_ALL_PRESENT_ROW)
        row[col] = False
        store = _FakeStore(rows=[row])
        result = check_schema(store)
        assert len(result.missing) == 1
        assert desc in result.missing[0]
        assert loader in result.missing[0]
