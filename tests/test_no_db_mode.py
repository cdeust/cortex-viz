"""Unit tests for the no-DB (Trace-only) startup path.

Covers the three layers the mode crosses:

  * ``infrastructure.db_probe``     — env opt-out + the startup probe's
    connect-failure fallback (psycopg failure monkeypatched, no real
    Postgres, no real pool).
  * ``server.http_standalone``      — the composition root's store
    resolution (``--no-db`` must never construct a reader; the probe
    path must be delegated to ``db_probe``).
  * ``server.http_standalone_nodb`` + routes — DB-backed routes answer
    an honest 503, Trace routes still dispatch, ``/api/capabilities``
    reports the degradation the frontend greys tabs from.
  * ``handlers.open_visualization`` — the schema preflight is skipped on
    explicit opt-out and degrades (returns ``None`` → launch proceeds)
    when the database is unreachable.
"""

from __future__ import annotations

import io

import psycopg
import pytest

import cortex_viz.handlers.open_visualization as ov
import cortex_viz.infrastructure.db_probe as db_probe
import cortex_viz.server.http_standalone as standalone
from cortex_viz.server.http_standalone_nodb import (
    requires_store,
    serve_capabilities,
    serve_db_unavailable,
)
from cortex_viz.server.http_standalone_routes import _route_unified_get


class _FakeHandler:
    """Minimal BaseHTTPRequestHandler stand-in: records status/headers/body."""

    def __init__(self, path: str = "/") -> None:
        self.path = path
        self.status: int | None = None
        self.headers: dict[str, str] = {}  # request headers (CORS origin read)
        self.headers_sent: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.headers_sent[key] = value

    def end_headers(self) -> None:
        pass

    def body_json(self):
        import json

        return json.loads(self.wfile.getvalue().decode())


# ── db_probe: env opt-out ────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_no_db_requested_truthy(monkeypatch, value):
    monkeypatch.setenv(db_probe.NO_DB_ENV, value)
    assert db_probe.no_db_requested() is True


@pytest.mark.parametrize("value", ["", "0", "false", "off"])
def test_no_db_requested_falsy(monkeypatch, value):
    monkeypatch.setenv(db_probe.NO_DB_ENV, value)
    assert db_probe.no_db_requested() is False


def test_no_db_requested_unset(monkeypatch):
    monkeypatch.delenv(db_probe.NO_DB_ENV, raising=False)
    assert db_probe.no_db_requested() is False


# ── db_probe: startup probe fallback ─────────────────────────────────


class _ProbeReader:
    """MemoryReader stand-in whose probe query raises (or succeeds)."""

    def __init__(self, exc: BaseException | None = None) -> None:
        self._exc = exc
        self.closed = False
        self.url = "postgresql://viz:secret@127.0.0.1:5432/cortex"

    def query(self, sql, params=None, *, batch=False):
        if self._exc is not None:
            raise self._exc
        return [{"?column?": 1}]

    def close(self) -> None:
        self.closed = True


def test_open_store_or_none_falls_back_on_psycopg_failure(monkeypatch, capsys):
    reader = _ProbeReader(psycopg.OperationalError("connection refused"))
    monkeypatch.setattr(
        "cortex_viz.infrastructure.memory_read.MemoryReader", lambda: reader
    )

    assert db_probe.open_store_or_none() is None
    assert reader.closed is True
    err = capsys.readouterr().err
    # Exactly one actionable line: names the mode, the fix, and the opt-out.
    assert err.count("\n") == 1
    assert "no-DB mode" in err
    assert "--no-db" in err
    assert db_probe.NO_DB_ENV in err
    # Password never reaches the log.
    assert "secret" not in err
    assert "viz:***@" in err


def test_open_store_or_none_returns_probed_reader(monkeypatch, capsys):
    reader = _ProbeReader()
    monkeypatch.setattr(
        "cortex_viz.infrastructure.memory_read.MemoryReader", lambda: reader
    )

    assert db_probe.open_store_or_none() is reader
    assert reader.closed is False
    assert capsys.readouterr().err == ""


def test_open_store_or_none_propagates_non_connectivity_errors(monkeypatch):
    reader = _ProbeReader(ValueError("a bug, not a down database"))
    monkeypatch.setattr(
        "cortex_viz.infrastructure.memory_read.MemoryReader", lambda: reader
    )

    with pytest.raises(ValueError):
        db_probe.open_store_or_none()


# ── composition root: _get_store ─────────────────────────────────────


def test_get_store_no_db_never_probes(monkeypatch, capsys):
    def _fail_probe():
        raise AssertionError("--no-db must skip the DB probe entirely")

    monkeypatch.setattr(
        "cortex_viz.infrastructure.db_probe.open_store_or_none", _fail_probe
    )

    assert standalone._get_store(no_db=True) is None
    assert "no-DB mode (explicit)" in capsys.readouterr().err


def test_get_store_delegates_to_probe(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        "cortex_viz.infrastructure.db_probe.open_store_or_none", lambda: sentinel
    )

    assert standalone._get_store(no_db=False) is sentinel


def test_no_db_cli_flag_parses():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=["unified"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--no-db", action="store_true")
    args = parser.parse_args(["--type", "unified", "--port", "0", "--no-db"])
    assert args.no_db is True


# ── route classification + degradation responses ─────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/dashboard",
        "/api/graph",
        "/api/graph/full",
        "/api/graph/full/stream",
        "/api/graph/progress",
        "/api/graph/events",
        "/api/memories",
        "/api/memories/facets",
        "/api/skills",
        "/api/stats",
        "/api/sankey",
        "/api/file-diff",
        "/api/recompute_layout",
        "/api/quadtree",
        "/api/activity/stream",
        "/api/wiki/list",
        "/api/tile/0/0/0.png",
    ],
)
def test_requires_store_db_backed(path):
    assert requires_store(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/trace/domains",
        "/api/trace/sessions",
        "/api/trace/chain",
        "/api/trace/file",
        "/api/trace/impact",
        "/api/discussions",
        "/api/discussion/abc",
        "/api/graph/node",
        "/api/prd",
        "/api/capabilities",
        "/",
        "/js/trace.js",
    ],
)
def test_requires_store_db_free(path):
    assert requires_store(path) is False


def test_serve_db_unavailable_is_actionable_503():
    handler = _FakeHandler("/api/stats")
    serve_db_unavailable(handler, "/api/stats")
    assert handler.status == 503
    body = handler.body_json()
    assert body["error"] == "db_unavailable"
    assert body["feature"] == "/api/stats"
    assert "github.com/cdeust/Cortex" in body["detail"]


def test_serve_capabilities_no_db():
    handler = _FakeHandler("/api/capabilities")
    serve_capabilities(handler, store=None)
    assert handler.status == 200
    body = handler.body_json()
    assert body["db"] is False
    assert body["mode"] == "trace-only"
    assert body["views"]["trace"] is True
    assert all(
        body["views"][v] is False
        for v in ("graph", "brain", "knowledge", "wiki", "board")
    )


def test_serve_capabilities_full_mode():
    handler = _FakeHandler("/api/capabilities")
    serve_capabilities(handler, store=object())
    body = handler.body_json()
    assert body["db"] is True
    assert body["mode"] == "full"
    assert all(body["views"].values())


# ── route dispatch with store=None ───────────────────────────────────


def test_route_guard_503s_db_backed_route_without_store(tmp_path):
    handler = _FakeHandler("/api/stats")
    _route_unified_get(
        handler,
        store=None,
        js_dir=tmp_path,
        css_dir=tmp_path,
        html_path=tmp_path / "unified-viz.html",
        vendor_dir=None,
    )
    assert handler.status == 503
    assert handler.body_json()["error"] == "db_unavailable"


def test_route_guard_still_dispatches_trace_without_store(monkeypatch, tmp_path):
    dispatched = {}

    def _fake_trace_domains(handler):
        dispatched["hit"] = True
        handler.send_response(200)
        handler.end_headers()

    monkeypatch.setattr(
        "cortex_viz.server.http_standalone_trace.serve_trace_domains",
        _fake_trace_domains,
    )
    handler = _FakeHandler("/api/trace/domains")
    _route_unified_get(
        handler,
        store=None,
        js_dir=tmp_path,
        css_dir=tmp_path,
        html_path=tmp_path / "unified-viz.html",
        vendor_dir=None,
    )
    assert dispatched.get("hit") is True
    assert handler.status == 200


def test_route_capabilities_served_without_store(tmp_path):
    handler = _FakeHandler("/api/capabilities")
    _route_unified_get(
        handler,
        store=None,
        js_dir=tmp_path,
        css_dir=tmp_path,
        html_path=tmp_path / "unified-viz.html",
        vendor_dir=None,
    )
    assert handler.status == 200
    assert handler.body_json()["db"] is False


# ── Trace enrichment endpoints degrade instead of throwing ───────────


def test_graph_node_endpoint_degrades_without_store():
    """/api/graph/node is the Trace detail panel's enrichment path — with
    no store it must answer found:false, never raise."""
    from cortex_viz.server.http_standalone_endpoints import serve_graph_node

    handler = _FakeHandler("/api/graph/node?id=memory:42")
    serve_graph_node(handler, store=None)
    assert handler.status == 200
    body = handler.body_json()
    assert body["found"] is False
    assert body["record"] == {}


def test_graph_node_lod_cell_degrades_without_store():
    from cortex_viz.server.http_standalone_endpoints import serve_graph_node

    handler = _FakeHandler("/api/graph/node?id=lod:3:1:2")
    serve_graph_node(handler, store=None)
    assert handler.status == 200
    body = handler.body_json()
    assert body["found"] is False
    assert body["members"] == []


def test_activity_ingest_drops_event_without_store():
    """POST /api/activity keeps the hook's fire-and-forget contract."""
    from cortex_viz.server.http_standalone_activity import serve_activity_ingest

    handler = _FakeHandler("/api/activity")
    serve_activity_ingest(handler, store=None)
    assert handler.status == 204


# ── open_visualization preflight ─────────────────────────────────────


def test_preflight_skipped_when_no_db_env_set(monkeypatch):
    monkeypatch.setenv(db_probe.NO_DB_ENV, "1")

    def _fail_reader():
        raise AssertionError("no reader may be built in explicit no-DB mode")

    monkeypatch.setattr(ov, "_build_memory_reader", _fail_reader)
    assert ov._ensure_schema_ready() is None


def test_preflight_degrades_when_db_unreachable(monkeypatch):
    monkeypatch.delenv(db_probe.NO_DB_ENV, raising=False)
    reader = _ProbeReader()
    monkeypatch.setattr(ov, "_build_memory_reader", lambda: reader)

    def _unreachable(store):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(ov, "check_schema", _unreachable)

    def _fail_migration(*a, **kw):
        raise AssertionError("migration must not run when the DB is down")

    monkeypatch.setattr(ov, "run_schema_migration", _fail_migration)

    assert ov._ensure_schema_ready() is None
    assert reader.closed is True
