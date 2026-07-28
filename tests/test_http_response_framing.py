"""Framing tests for the standalone server's response helpers.

An HTTP/1.1 response carrying neither ``Content-Length`` nor chunked
encoding is *unframed*: the client cannot tell where the body ends, so it
blocks until its own timeout rather than completing the exchange.
``http_standalone.py`` sets ``protocol_version = "HTTP/1.1"``, which makes
keep-alive the default, so framing is not optional on *any* response —
including the empty ones.

Issue #66: ``send_plain_error`` wrote a status line and ``end_headers()``
and nothing else. Every 403/404 from the sandboxed static readers goes
through that helper, so every *refused* request hung the caller (measured
on the reporting run: an 8-request ``curl`` loop wedged for 3 minutes)
while every accepted request returned normally — which is exactly the
shape that hides in a green test suite, because "no body was leaked" was
asserted and "the client could tell there was no body" was not.

These tests assert the header is genuinely emitted *before* the headers
are terminated, not merely that the call returned without raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_viz.server.http_standalone_response import (
    send_json_error,
    send_json_ok,
    send_plain_error,
)
from cortex_viz.server.http_standalone_static import serve_shared_asset, serve_static


class RecordingHandler:
    """Records what a helper actually wrote, in order.

    ``headers`` is the *request* mapping (the CORS layer reads ``Origin``
    off it); ``sent`` is the *response* mapping. ``sent_at_end`` snapshots
    the response headers at ``end_headers()`` time, so a header written
    after the terminator — which the real client would never see — cannot
    satisfy an assertion.
    """

    def __init__(self, request_headers: dict[str, str] | None = None) -> None:
        self.headers = request_headers if request_headers is not None else {}
        self.status: int | None = None
        self.sent: dict[str, str] = {}
        self.sent_at_end: dict[str, str] | None = None
        self.body = b""
        self.wfile = self

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.sent[key] = value

    def end_headers(self) -> None:
        self.sent_at_end = dict(self.sent)

    def write(self, data: bytes) -> None:
        self.body += data


def assert_framed(h: RecordingHandler, expected_length: int) -> None:
    """The contract every response on this server must satisfy."""
    assert h.sent_at_end is not None, "headers were never terminated"
    assert "Content-Length" in h.sent_at_end, (
        f"{h.status} response is unframed — it declares neither Content-Length "
        "nor chunked encoding, so an HTTP/1.1 keep-alive client cannot detect "
        "the end of the body and blocks until its own timeout (issue #66)"
    )
    assert h.sent_at_end["Content-Length"] == str(expected_length)
    assert len(h.body) == expected_length, (
        f"declared Content-Length {h.sent_at_end['Content-Length']} but wrote "
        f"{len(h.body)} bytes — a mismatch desynchronises the connection"
    )


# Every status the rejection paths actually emit, plus the generic ones.
PLAIN_ERROR_STATUSES = [400, 403, 404, 405, 500]


@pytest.mark.parametrize("status", PLAIN_ERROR_STATUSES)
def test_plain_error_is_framed(status: int) -> None:
    """Regression for #66 — a bodiless error still needs an explicit length."""
    h = RecordingHandler()
    send_plain_error(h, status)
    assert h.status == status
    assert_framed(h, 0)
    assert h.body == b"", "a plain error must not write a body"


def test_json_ok_is_framed() -> None:
    h = RecordingHandler()
    send_json_ok(h, {"nodes": [1, 2, 3]})
    assert h.status == 200
    assert_framed(h, len(h.body))


def test_json_error_is_framed() -> None:
    h = RecordingHandler()
    send_json_error(h, ValueError("boom"))
    assert h.status == 500
    assert_framed(h, len(h.body))
    assert b"boom" not in h.body, "the error body must not echo str(exc)"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A shared/ foundation dir with one legitimate asset beside a secret."""
    shared = tmp_path / "ui" / "shared"
    (shared / "tokens").mkdir(parents=True)
    (shared / "ds.css").write_text("/* ds */", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return shared


# The payloads from the issue's reproduction, as the reader receives them
# (the HTTP layer has already percent-decoded by this point).
REJECTED_PAYLOADS = [
    "../../secret.txt",
    "../../../../etc/passwd",
    "/etc/passwd",
    "./../secret.txt",
    "tokens/../../secret.txt",
    ".hidden",
    "\x00",
    "",
]


@pytest.mark.parametrize("payload", REJECTED_PAYLOADS)
def test_rejected_shared_asset_is_framed(sandbox: Path, payload: str) -> None:
    """The reported symptom end-to-end: a refused read must fail fast."""
    h = RecordingHandler()
    serve_shared_asset(h, sandbox, payload)
    assert h.status in (403, 404), f"{payload!r} was not refused"
    assert_framed(h, 0)


@pytest.mark.parametrize("payload", REJECTED_PAYLOADS)
def test_rejected_static_read_is_framed(tmp_path: Path, payload: str) -> None:
    """Same contract on the other sandboxed reader."""
    js_dir = tmp_path / "js"
    js_dir.mkdir()
    (js_dir / "config.js").write_text("// cfg", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    h = RecordingHandler()
    serve_static(h, js_dir, payload, "application/javascript")
    assert h.status in (403, 404), f"{payload!r} was not refused"
    assert_framed(h, 0)


def test_accepted_read_stays_framed(sandbox: Path) -> None:
    """The guard must not be so tight that the legitimate path regresses."""
    h = RecordingHandler()
    serve_shared_asset(h, sandbox, "ds.css")
    assert h.status == 200
    assert h.body == b"/* ds */"
    assert_framed(h, len(b"/* ds */"))
