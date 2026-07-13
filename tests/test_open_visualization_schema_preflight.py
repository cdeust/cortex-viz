"""Unit tests for the schema preflight wired into ``open_visualization``.

Exercises ``_ensure_schema_ready`` (and the ``handler`` short-circuit it
drives) with the reader, ``check_schema`` and ``run_schema_migration``
call sites monkeypatched — no live Postgres connection, no real
subprocess, no browser/server launch.
"""

from __future__ import annotations

import asyncio

import pytest

import cortex_viz.handlers.open_visualization as ov
from cortex_viz.infrastructure.schema_migrate import MigrationResult
from cortex_viz.infrastructure.schema_preflight import SchemaPreflightResult


class _FakeReader:
    """Stands in for MemoryReader — `.url`, `.close()`, and nothing a
    real Postgres connection needs."""

    def __init__(self, url: str = "postgresql://fake") -> None:
        self.url = url
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _no_real_browser_or_server(monkeypatch):
    """Every test in this module stays below the schema-preflight gate
    or explicitly re-enables these; block accidental real launches."""

    def _fail_launch(*a, **kw):
        raise AssertionError("launch_server must not run past a preflight failure")

    def _fail_browser(*a, **kw):
        raise AssertionError("open_in_browser must not run past a preflight failure")

    monkeypatch.setattr(ov, "launch_server", _fail_launch)
    monkeypatch.setattr(ov, "open_in_browser", _fail_browser)


def test_schema_ok_returns_none_no_migration_attempted(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(
        ov, "check_schema", lambda store: SchemaPreflightResult(ok=True)
    )

    def _fail_migration(*a, **kw):
        raise AssertionError("migration must not run when schema is already ok")

    monkeypatch.setattr(ov, "run_schema_migration", _fail_migration)

    assert ov._ensure_schema_ready() is None
    assert reader.closed is True


def test_migration_succeeds_and_reschema_is_ok(monkeypatch):
    reader = _FakeReader()
    calls = {"n": 0}

    def _check(store):
        calls["n"] += 1
        # First call: missing. Second call (post-migration): fixed.
        if calls["n"] == 1:
            return SchemaPreflightResult(
                ok=False, missing=("table entities — required by memory_read.py",)
            )
        return SchemaPreflightResult(ok=True)

    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(ov, "check_schema", _check)
    monkeypatch.setattr(
        ov,
        "run_schema_migration",
        lambda url: MigrationResult(
            plugin_found=True, exit_code=0, stderr="", timed_out=False
        ),
    )

    assert ov._ensure_schema_ready() is None
    assert calls["n"] == 2


def test_plugin_not_found_produces_support_message(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(
        ov,
        "check_schema",
        lambda store: SchemaPreflightResult(
            ok=False, missing=("table entities — required by memory_read.py",)
        ),
    )
    monkeypatch.setattr(
        ov,
        "run_schema_migration",
        lambda url: MigrationResult(
            plugin_found=False, exit_code=None, stderr="", timed_out=False
        ),
    )

    result = ov._ensure_schema_ready()
    assert result is not None
    assert result["error"] == "schema_preflight_failed"
    assert "table entities" in result["message"]
    assert "aucune installation du plugin Cortex trouvée" in result["message"]
    assert ov._SUPPORT_INSTRUCTION in result["message"]


def test_migration_nonzero_exit_clips_stderr_into_message(monkeypatch):
    reader = _FakeReader()
    long_stderr = "E" * 5000
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(
        ov,
        "check_schema",
        lambda store: SchemaPreflightResult(
            ok=False,
            missing=("function effective_heat(...) — required by memory_read.py",),
        ),
    )
    monkeypatch.setattr(
        ov,
        "run_schema_migration",
        lambda url: MigrationResult(
            plugin_found=True, exit_code=1, stderr=long_stderr, timed_out=False
        ),
    )

    result = ov._ensure_schema_ready()
    assert result is not None
    assert "code de sortie 1" in result["message"]
    assert len(result["migration_failure"]) < len(long_stderr)
    assert ov._SUPPORT_INSTRUCTION in result["message"]


def test_migration_timeout_reported(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(
        ov,
        "check_schema",
        lambda store: SchemaPreflightResult(
            ok=False, missing=("table memories — required by memory_read.py",)
        ),
    )
    monkeypatch.setattr(
        ov,
        "run_schema_migration",
        lambda url: MigrationResult(
            plugin_found=True,
            exit_code=None,
            stderr="migration timed out after 120s",
            timed_out=True,
        ),
    )

    result = ov._ensure_schema_ready()
    assert result is not None
    assert "timed out" in result["message"]


def test_migration_exit_zero_but_still_broken_reports_incomplete(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(
        ov,
        "check_schema",
        lambda store: SchemaPreflightResult(
            ok=False, missing=("table memories — required by memory_read.py",)
        ),
    )
    monkeypatch.setattr(
        ov,
        "run_schema_migration",
        lambda url: MigrationResult(
            plugin_found=True, exit_code=0, stderr="", timed_out=False
        ),
    )

    result = ov._ensure_schema_ready()
    assert result is not None
    assert "reste incomplet après la migration" in result["message"]


def test_reader_always_closed_even_on_failure(monkeypatch):
    reader = _FakeReader()
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)
    monkeypatch.setattr(
        ov,
        "check_schema",
        lambda store: SchemaPreflightResult(
            ok=False, missing=("table memories — required by memory_read.py",)
        ),
    )
    monkeypatch.setattr(
        ov,
        "run_schema_migration",
        lambda url: MigrationResult(
            plugin_found=False, exit_code=None, stderr="", timed_out=False
        ),
    )

    ov._ensure_schema_ready()
    assert reader.closed is True


def test_handler_short_circuits_before_launch_on_schema_failure(monkeypatch):
    monkeypatch.setattr(
        ov,
        "_ensure_schema_ready",
        lambda: {"error": "schema_preflight_failed", "message": "nope"},
    )
    result = asyncio.run(ov.handler({}))
    assert result == {"error": "schema_preflight_failed", "message": "nope"}
