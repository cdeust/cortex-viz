"""Shared module-level state for the standalone HTTP server.

Two modules (``http_standalone`` and ``http_standalone_graph``) need to
read and update the conversations cache; keeping the mutable state here
lets them cooperate without a circular import.

Exposes:

* ``IDLE_TIMEOUT`` / ``GRAPH_CACHE_TTL`` / ``CONVERSATIONS_CACHE_TTL``
  — tuned constants, documented at definition.
* ``touch`` / ``seconds_since_last_request`` — idle-watchdog accounting.
* ``get_cached_conversations_state`` /
  ``set_cached_conversations_state`` — read/replace the TTL cache.
"""

from __future__ import annotations

import threading
import time

import os as _os

IDLE_TIMEOUT = float(_os.environ.get("CORTEX_IDLE_TIMEOUT", "600"))  # default 10 min
GRAPH_CACHE_TTL = 120.0  # seconds — avoids rebuilding 8000+ nodes.
CONVERSATIONS_CACHE_TTL = 60.0  # seconds — JSONL scan is ~O(files).

_last_request_time = time.monotonic()
_request_lock = threading.Lock()

_cached_conversations: list[dict] | None = None
_conversations_cache_ts: float = 0.0


def touch() -> None:
    """Record that a request was just served."""
    global _last_request_time
    with _request_lock:
        _last_request_time = time.monotonic()


# Live streaming connections (SSE / chunked graph streams). The idle
# watchdog counts REQUEST ARRIVALS, but a browser holding an SSE stream
# makes no new requests — and background tabs get frozen by Chrome, so
# their 30 s stats polls stop too. Result (2026-06-10): the server
# idle-shut under an open tab and every later click hit a dead port.
# A held stream IS activity; track it explicitly.
_live_streams = 0


def stream_opened() -> None:
    """Mark a long-lived stream connection as open (call before serving)."""
    global _live_streams
    with _request_lock:
        _live_streams += 1


def stream_closed() -> None:
    """Mark a long-lived stream connection as closed (call in finally)."""
    global _live_streams
    with _request_lock:
        _live_streams = max(0, _live_streams - 1)


def seconds_since_last_request() -> float:
    """Elapsed monotonic seconds since the last touch().

    Returns 0.0 while any long-lived stream connection is open — a held
    SSE/chunked stream is a live client, never idle.
    """
    with _request_lock:
        if _live_streams > 0:
            return 0.0
        return time.monotonic() - _last_request_time


def get_cached_conversations_state() -> tuple[list[dict] | None, float]:
    """Return (cached, cached_ts) — None if never populated."""
    return _cached_conversations, _conversations_cache_ts


def set_cached_conversations_state(value: list[dict], ts: float) -> None:
    """Replace the conversations cache with a freshly-read value."""
    global _cached_conversations, _conversations_cache_ts
    _cached_conversations = value
    _conversations_cache_ts = ts
