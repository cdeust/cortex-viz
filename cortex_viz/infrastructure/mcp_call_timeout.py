"""Per-call response-wait timeout policy for the MCP stdio client.

A single ``tools/call`` that is not answered within this window fails
LOUDLY with an McpConnectionError instead of blocking the caller forever.
This is the safety net against an upstream child wedged writing a response
larger than the OS pipe buffer, or a client whose reader loop is no longer
draining its stdout (e.g. bound to a now-closed event loop).

source: ingest stdio-deadlock RCA 2026-06-11 — an ``ingest_codebase`` call
hung 4.5+ hours at 0% CPU on both sides because the cached client's reader
was bound to a worker-thread loop that had since closed, so ``await future``
was unbounded.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Default ceiling (seconds) on one tools/call response wait. 600s = 10x the
# measured 32s success latency of an analyze/ingest run on the Cortex repo
# (live incident 2026-06-11: call 1 completed in 32s). 10x leaves headroom
# for larger polyglot repos while failing a genuinely wedged child in
# minutes, not hours. source: ingest stdio-deadlock RCA 2026-06-11.
_DEFAULT_CALL_TIMEOUT_S = 600.0
_ENV_VAR = "CORTEX_MCP_CALL_TIMEOUT_S"


def default_call_timeout_s() -> float:
    """Return the configured per-call timeout in seconds.

    Reads ``CORTEX_MCP_CALL_TIMEOUT_S`` (positive float) when set and valid;
    otherwise returns the documented default. A non-positive or malformed
    override falls back to the default rather than disabling the ceiling —
    an unbounded wait is the exact failure this guard exists to prevent.
    """
    raw = os.environ.get(_ENV_VAR)
    if raw:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            _warn_ignored(raw, "not a number")
        else:
            if val > 0:
                return val
            _warn_ignored(raw, "not positive")
    return _DEFAULT_CALL_TIMEOUT_S


def _warn_ignored(raw: str, why: str) -> None:
    """Report an override that was set but could not be honoured.

    Silence here is the wrong default: the operator deliberately set the
    variable, so a typo (``6OO``, ``600s``, ``-1``) that lands back on the
    600s default is a configuration error they cannot see from any output.
    The value is echoed so the typo is visible in the log line itself.
    """
    logger.warning(
        "%s=%r ignored (%s); using the %.0fs default",
        _ENV_VAR,
        raw,
        why,
        _DEFAULT_CALL_TIMEOUT_S,
    )
