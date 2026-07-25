"""Coverage-honesty read-path — ``GET /api/graph/coverage`` (issue #36).

The browser's coverage indicator (``ui/unified/js/coverage_indicator.js``)
answers "what is missing from what I am looking at?". The store-vs-rendered,
LOD-collapse and staleness signals it needs are computed client-side from data
the UI already holds. The one signal it CANNOT derive itself is engine parse
coverage — which source files were indexed, which failed to parse, and which
indexed only partially. That is produced by the AP engine
(cdeust/automatised-pipeline#57: tree-sitter ERROR/MISSING range persistence in
an ``index_coverage`` table + a ``check_index_coverage`` tool).

This module is the CONSUMER read-path against that shape. It is deliberately
tolerant of the engine not having landed #57 yet: when the store exposes no
coverage accessor (older engine), the endpoint returns an EXPLICIT, NAMED
``{"available": false, "reason": ...}`` — a degraded mode the client surfaces
verbatim (coding-standards §13 F2), never a fabricated or silently-zero figure.

Expected accessor contract (AP#57): ``store.index_coverage()`` returns a mapping
with ``files_present`` / ``files_indexed`` / ``parse_incomplete`` (ints),
``extraction_failures`` (list of ``{"path", "error_ranges", "reason"}``),
``revision`` (the AP#55 snapshot/store revision) and ``generated_at`` (unix
seconds). Every field is defaulted here so a partial engine payload still yields
a well-typed response.
"""

from __future__ import annotations

from typing import Any

from cortex_viz.server.http_standalone_response import (
    send_json_error,
    send_json_ok,
)


def _int(value: Any) -> int:
    """Coerce a wire value to a non-negative int (0 on None/garbage).

    Pre: value is anything the engine put on the field. Post: a non-negative
    int — the client subtracts these, so a negative or non-numeric value must
    never leak through.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _normalise_failures(raw: Any) -> list[dict]:
    """Normalise the extraction-failure list to ``[{path, error_ranges, reason}]``.

    Pre: raw is the engine's ``extraction_failures`` (list or absent). Post: a
    list of dicts with a string ``path``, a list ``error_ranges`` and a string
    ``reason``; non-list input yields ``[]`` (absent-field case).
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ranges = item.get("error_ranges")
        out.append(
            {
                "path": str(item.get("path", "")),
                "error_ranges": ranges if isinstance(ranges, list) else [],
                "reason": str(item.get("reason", "")),
            }
        )
    return out


def build_coverage_payload(store) -> dict:
    """Build the ``/api/graph/coverage`` payload from the store.

    Pre: ``store`` is the viz store (may be ``None`` in no-DB mode, may predate
    AP#57's ``index_coverage`` accessor). Post: a dict that is EITHER
    ``{"available": False, "reason": <str>}`` when engine coverage cannot be
    read, OR ``{"available": True, files_present, files_indexed,
    parse_incomplete, extraction_failures, revision, generated_at}`` with every
    field present and well-typed. Never raises for the absent-accessor case —
    that is the expected degraded mode, not an error.
    """
    if store is None:
        return {
            "available": False,
            "reason": "no store bound (no-DB mode)",
        }
    accessor = getattr(store, "index_coverage", None)
    if not callable(accessor):
        return {
            "available": False,
            "reason": (
                "engine index does not report parse coverage "
                "(requires automatised-pipeline#57)"
            ),
        }
    raw = accessor()
    if not isinstance(raw, dict):
        return {
            "available": False,
            "reason": "engine coverage accessor returned no data",
        }
    return {
        "available": True,
        "files_present": _int(raw.get("files_present")),
        "files_indexed": _int(raw.get("files_indexed")),
        "parse_incomplete": _int(raw.get("parse_incomplete")),
        "extraction_failures": _normalise_failures(raw.get("extraction_failures")),
        # revision / generated_at pass through as-is (opaque to the client,
        # which only compares revision for equality and ages generated_at);
        # None when the engine predates the AP#55 revision contract.
        "revision": raw.get("revision"),
        "generated_at": raw.get("generated_at"),
    }


def serve_graph_coverage(handler, store) -> None:
    """GET /api/graph/coverage — engine parse-coverage for the UI indicator.

    Delegates shaping to ``build_coverage_payload`` (unit-tested without HTTP)
    and only owns the transport. The absent-accessor path is a normal 200 with
    ``available: false`` — the client treats it as a named degraded mode, so it
    must NOT be an HTTP error.
    """
    try:
        send_json_ok(handler, build_coverage_payload(store))
    except Exception as e:  # a store that HAS the accessor but it raised
        send_json_error(handler, e)
