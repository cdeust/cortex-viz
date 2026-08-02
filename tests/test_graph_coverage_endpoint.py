"""Coverage-honesty read-path (issue #36) — ``/api/graph/coverage``.

Pins the CONSUMER contract against the AP#57 engine shape and, above all, the
absent-field / absent-accessor degraded mode: an engine that predates #57 must
yield an explicit ``available: false`` (still HTTP 200), never a fabricated or
silently-zero coverage figure and never a 500. Every branch of
``build_coverage_payload`` has an asserting test (§13 A2/A3).
"""

from __future__ import annotations

import io

from cortex_viz.server.graph_coverage import (
    build_coverage_payload,
    serve_graph_coverage,
)


class _Store:
    """Store stub exposing an ``index_coverage`` accessor (AP#57 contract)."""

    def __init__(self, payload):
        self._payload = payload

    def index_coverage(self):
        return self._payload


class _StoreNoAccessor:
    """A pre-#57 store: no ``index_coverage`` at all."""


class _StoreRaises:
    def index_coverage(self):
        raise RuntimeError("index_coverage table locked")


# ── absent / degraded branches (the load-bearing honesty) ──────────────


def test_no_store_is_named_unavailable():
    out = build_coverage_payload(None)
    assert out["available"] is False
    assert "no-DB" in out["reason"] or "no store" in out["reason"]


def test_pre_57_engine_without_accessor_is_named_unavailable():
    out = build_coverage_payload(_StoreNoAccessor())
    assert out["available"] is False
    assert "automatised-pipeline#57" in out["reason"]


def test_accessor_returning_non_dict_is_named_unavailable():
    out = build_coverage_payload(_Store(None))
    assert out["available"] is False
    out2 = build_coverage_payload(_Store(["not", "a", "dict"]))
    assert out2["available"] is False


# ── present branch (AP#57 shape) ───────────────────────────────────────


def test_full_engine_payload_is_normalised():
    out = build_coverage_payload(
        _Store(
            {
                "files_present": 120,
                "files_indexed": 118,
                "parse_incomplete": 3,
                "extraction_failures": [
                    {"path": "src/bad.c", "error_ranges": [[10, 20]], "reason": "ERROR"}
                ],
                "revision": "abc123",
                "generated_at": 1690000000,
            }
        )
    )
    assert out["available"] is True
    assert out["files_present"] == 120
    assert out["files_indexed"] == 118
    assert out["parse_incomplete"] == 3
    assert out["revision"] == "abc123"
    assert out["generated_at"] == 1690000000
    assert out["extraction_failures"][0]["path"] == "src/bad.c"
    assert out["extraction_failures"][0]["error_ranges"] == [[10, 20]]


def test_partial_engine_payload_defaults_missing_fields():
    # A partial payload (only the two headline counts) must still be well-typed:
    # counts default to 0, failures to [], revision/generated_at to None.
    out = build_coverage_payload(_Store({"files_present": 50, "files_indexed": 50}))
    assert out["available"] is True
    assert out["parse_incomplete"] == 0
    assert out["extraction_failures"] == []
    assert out["revision"] is None
    assert out["generated_at"] is None


def test_negative_and_garbage_counts_coerced_non_negative():
    out = build_coverage_payload(
        _Store({"files_present": -5, "files_indexed": "oops", "parse_incomplete": 2.9})
    )
    assert out["files_present"] == 0
    assert out["files_indexed"] == 0
    assert out["parse_incomplete"] == 2


def test_non_list_failures_becomes_empty_list():
    out = build_coverage_payload(
        _Store({"files_present": 1, "files_indexed": 1, "extraction_failures": "boom"})
    )
    assert out["extraction_failures"] == []


def test_failure_entries_missing_fields_are_defaulted():
    out = build_coverage_payload(
        _Store(
            {
                "files_present": 1,
                "files_indexed": 1,
                "extraction_failures": [{}, {"path": "x"}, "not-a-dict"],
            }
        )
    )
    # the string entry is dropped; the two dicts are defaulted.
    assert len(out["extraction_failures"]) == 2
    assert out["extraction_failures"][0] == {
        "path": "",
        "error_ranges": [],
        "reason": "",
    }
    assert out["extraction_failures"][1]["path"] == "x"


# ── transport wrapper ──────────────────────────────────────────────────


class _FakeHandler:
    def __init__(self, path="/api/graph/coverage"):
        self.path = path
        self.status = None
        self.headers = {}
        self.headers_sent = {}
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers_sent[key] = value

    def end_headers(self):
        pass

    def body_json(self):
        import json

        return json.loads(self.wfile.getvalue().decode())


def test_serve_returns_200_available_false_for_pre_57_engine():
    h = _FakeHandler()
    serve_graph_coverage(h, _StoreNoAccessor())
    assert h.status == 200
    assert h.body_json()["available"] is False


def test_serve_returns_200_available_true_for_engine_with_coverage():
    h = _FakeHandler()
    serve_graph_coverage(h, _Store({"files_present": 2, "files_indexed": 2}))
    assert h.status == 200
    body = h.body_json()
    assert body["available"] is True
    assert body["files_present"] == 2


def test_serve_error_path_when_accessor_raises():
    # An accessor that EXISTS but raises is a real error (not the absent case),
    # so it flows through send_json_error rather than a silent available:false.
    h = _FakeHandler()
    serve_graph_coverage(h, _StoreRaises())
    assert h.status is not None
    assert h.status != 200
