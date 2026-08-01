"""Response helpers for the standalone HTTP server.

Every wiki / graph / discussion endpoint repeats the same 6-line
boilerplate: ``send_response(200)`` → set ``Content-Type`` → apply CORS
headers → set ``Cache-Control`` → ``end_headers`` → write body, plus a
matching 500-branch that prints the traceback to stderr and writes a
sanitized error name. Centralising the two patterns here keeps
``http_standalone.py`` under the 300-line ceiling without changing any
observable HTTP behaviour.
"""

from __future__ import annotations

import json
import sys
import traceback

from cortex_viz.server.http_common import _apply_cors_headers


def send_json_ok(handler, data: dict | list, cache_control: str = "no-cache") -> None:
    """Send a 200 JSON response with loopback-strict CORS.

    ``Content-Length`` is mandatory here. ``BaseHTTPRequestHandler``
    runs at ``HTTP/1.1`` with keep-alive; without an explicit framing
    header (``Content-Length`` or chunked encoding) the browser reads
    until the connection closes and every ``fetch()`` hangs for the
    ~60 s keep-alive idle — manifesting as an infinite loading spinner
    even though the server already wrote the body. Setting the length
    lets the client parse the response and free the socket.
    """
    body = json.dumps(data, default=str).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    _apply_cors_headers(handler)
    if cache_control:
        handler.send_header("Cache-Control", cache_control)
    handler.end_headers()
    handler.wfile.write(body)


def send_json_error(handler, exc: BaseException, status: int = 500) -> None:
    """Send a JSON error response with the exception class name.

    The body never echoes ``str(exc)`` because user-controlled data can
    reach that surface (file paths, query strings); we log the full
    traceback to stderr instead.
    """
    body = json.dumps({"error": type(exc).__name__}).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    traceback.print_exc(file=sys.stderr)
    handler.wfile.write(body)


def send_json_warming(handler, reason: str, progress: dict | None = None) -> None:
    """Send ``202 Accepted`` — the answer exists later, not never.

    A first build that has not finished is a normal lifecycle state, not
    a server fault. It used to go out as ``503`` with a body that said
    ``"status": "warming"`` — status line and body contradicting each
    other — so clients branching on ``response.ok`` (the browser default)
    logged an expected state as an outage and the console filled with
    503s (issue #90). 202 says "accepted, not ready": retry, don't alarm.

    ``progress`` rides along so the caller can render the state it is
    waiting on without a second round-trip.
    """
    payload: dict = {"status": "warming", "reason": reason}
    if progress:
        payload["progress"] = progress
    body = json.dumps(payload, default=str).encode()
    handler.send_response(202)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    _apply_cors_headers(handler)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def send_json_capability_unavailable(
    handler, capability: str, reason: str, fallback: str | None = None
) -> None:
    """Send ``200`` describing a capability this install does not have.

    An optional extra that was never installed is a supported
    configuration, not an error: ``viz-tile`` is ~200 MB of
    datashader/numba and the docs present it as opt-in. Reporting its
    absence as ``503 {"status":"error"}`` made the tilemap view render a
    red failure for a capability the user never asked for, and clients
    retried a request that can never succeed until a reinstall (#90).

    The endpoint answers truthfully about its own capability and names
    the path that DOES work, so the client degrades on purpose instead
    of failing. No ``detail`` field: the ImportError message is logged
    server-side, never echoed (same rule as ``send_json_error``).
    """
    payload: dict = {
        "status": "unavailable",
        "capability": capability,
        "reason": reason,
    }
    if fallback:
        payload["fallback"] = fallback
    send_json_ok(handler, payload, cache_control="no-store")


def send_plain_error(handler, status: int) -> None:
    """Send a bare status response with no body.

    ``Content-Length: 0`` is mandatory for the same reason it is in
    ``send_json_ok`` — an empty body still has to be *framed*. At
    ``HTTP/1.1`` with keep-alive, a response declaring neither a length
    nor chunked encoding leaves the client unable to tell that the body
    already ended, so it holds the connection until its own timeout.
    Every rejection path in the sandboxed static readers lands here, so
    omitting the header hung the caller on *every* refused request
    instead of failing it fast (issue #66).
    """
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
