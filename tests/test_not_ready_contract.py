"""Wire tests for the not-ready / capability-absent contract (issue #90).

Three expected states used to go out as ``503 {"status":"error"}``, which
clients branching on ``response.ok`` read as an outage:

  * the first build has not written a snapshot yet,
  * the layout table is still empty,
  * the optional ``viz-tile`` extra is not installed.

The first two are now ``202 Accepted`` with ``status:"warming"``; the third
is ``200`` with ``status:"unavailable"`` plus the fallback path. These tests
pin the status codes and bodies, because the whole defect was a status code
contradicting its own body.

No HTTP, no PG: a fake handler captures the bytes.
"""

from __future__ import annotations

import io
import json

from cortex_viz.server.http_standalone_response import (
    send_json_capability_unavailable,
    send_json_warming,
)


class _FakeHandler:
    """Minimal stand-in. ``headers`` exists because the CORS helper reads
    the request Origin before reflecting a loopback value."""

    def __init__(self) -> None:
        self.wfile = io.BytesIO()
        self.headers: dict[str, str] = {}
        self.headers_sent: list[tuple[str, str]] = []
        self.status = None

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, k: str, v: str) -> None:
        self.headers_sent.append((k, v))

    def end_headers(self) -> None:
        pass


def _body(h: _FakeHandler) -> dict:
    return json.loads(h.wfile.getvalue().decode())


def _header(h: _FakeHandler, name: str) -> str | None:
    for k, v in h.headers_sent:
        if k.lower() == name.lower():
            return v
    return None


def test_warming_is_202_not_an_error() -> None:
    h = _FakeHandler()
    send_json_warming(h, "no_snapshot")
    assert h.status == 202
    body = _body(h)
    assert body["status"] == "warming"
    assert body["reason"] == "no_snapshot"
    # The old body said "warming" under a 503. Status and body must agree.
    assert "error" not in body


def test_warming_carries_progress_so_the_client_can_name_the_phase() -> None:
    h = _FakeHandler()
    send_json_warming(
        h,
        "no_snapshot",
        {"phase": "layout bake (DrL)", "indeterminate": True, "phase_elapsed": 480.0},
    )
    prog = _body(h)["progress"]
    assert prog["phase"] == "layout bake (DrL)"
    assert prog["indeterminate"] is True
    assert prog["phase_elapsed"] == 480.0


def test_warming_is_framed_and_uncacheable() -> None:
    h = _FakeHandler()
    send_json_warming(h, "no_layout")
    # Content-Length is mandatory at HTTP/1.1 keep-alive or fetch() hangs.
    assert _header(h, "Content-Length") == str(len(h.wfile.getvalue()))
    # A transient state must never be cached as if it were the answer.
    assert _header(h, "Cache-Control") == "no-store"


def test_absent_extra_is_200_unavailable_with_a_fallback() -> None:
    h = _FakeHandler()
    send_json_capability_unavailable(
        h, capability="viz-tile", reason="extra_not_installed", fallback="/api/graph"
    )
    assert h.status == 200
    body = _body(h)
    assert body["status"] == "unavailable"
    assert body["capability"] == "viz-tile"
    assert body["fallback"] == "/api/graph"
    # Not an error, and not retryable-as-warming: a client that retries a
    # missing dependency loops until reinstall.
    assert body["status"] != "warming"
    assert "error" not in body


def test_absent_extra_never_echoes_the_import_error() -> None:
    """The ImportError text is logged server-side, never put on the wire.

    Same rule as ``send_json_error``: that surface can carry paths.
    """
    h = _FakeHandler()
    send_json_capability_unavailable(
        h, capability="viz-tile", reason="extra_not_installed"
    )
    body = _body(h)
    assert "detail" not in body
    assert "No module named" not in json.dumps(body)
