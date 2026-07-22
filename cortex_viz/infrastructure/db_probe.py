"""Startup decision: does this cortex-viz process have a Cortex database?

cortex-viz delivers standalone value without Cortex: the Trace view reads
``~/.claude/projects/*.jsonl`` + git live and never touches PostgreSQL.
This module owns the single startup question the composition root
(``server.http_standalone``) and the launch handler
(``handlers.open_visualization``) both ask — "no-DB mode?" — via:

  * ``no_db_requested()``    — the explicit ``CORTEX_VIZ_NO_DB=1`` opt-out
    (mirrored by the ``--no-db`` CLI flag; the flag sets nothing here, the
    composition root ORs it in).
  * ``open_store_or_none()`` — build a ``MemoryReader`` and probe it with
    one ``SELECT 1``; on any ``psycopg.Error`` (connection refused, auth
    failure, pool timeout — ``psycopg_pool.PoolTimeout`` subclasses
    ``psycopg.OperationalError``) log ONE actionable line to stderr and
    return ``None`` instead of dying. The probe is bounded by the
    interactive pool timeout (``POOL_INTERACTIVE_TIMEOUT_S``, 5 s —
    memory_config.py, Cortex's proven interactive bound), so a downed
    Postgres delays startup by at most that.

Boundary invariant: imports psycopg + stdlib + sibling infrastructure
only. Never imports ``mcp_server.*`` (same rule as ``memory_read``).
"""

from __future__ import annotations

import os
import re
import sys

import psycopg

# The connectivity failure family. ``psycopg.Error`` is the driver's
# root exception; ``psycopg_pool.PoolTimeout`` subclasses
# ``psycopg.OperationalError`` so pool-level timeouts are covered too
# (verified against psycopg-pool 3.x MRO). Exported so callers above
# infrastructure (handlers) can distinguish "database down" from a bug
# without importing psycopg themselves.
DB_UNREACHABLE_ERRORS: tuple[type[BaseException], ...] = (psycopg.Error,)

# Env var mirrored by the ``--no-db`` CLI flag of
# ``server.http_standalone``. Env (not only a flag) because the spawned
# standalone server inherits ``os.environ`` from ``http_launcher`` /
# the MCP process, so one setting reaches every launch surface.
NO_DB_ENV = "CORTEX_VIZ_NO_DB"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def no_db_requested() -> bool:
    """True iff the user explicitly opted out of PostgreSQL via env."""
    return os.environ.get(NO_DB_ENV, "").strip().lower() in _TRUTHY


def _redact_url(url: str) -> str:
    """Mask the password component of a conninfo URL for log output."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


def open_store_or_none():
    """Return a probed ``MemoryReader``, or ``None`` when the DB is down.

    Postcondition: a non-``None`` return has answered ``SELECT 1`` (the
    schema may still be old — that is ``schema_preflight``'s question,
    not this one). A ``None`` return has logged exactly one actionable
    stderr line and closed the reader's pools. Non-connectivity errors
    (anything not ``psycopg.Error``) propagate — they are bugs, not a
    missing database.
    """
    import logging

    from cortex_viz.infrastructure.memory_read import MemoryReader

    reader = MemoryReader()
    # psycopg_pool logs one "error connecting in 'pool-1'" warning per
    # reconnect attempt while the probe waits out its timeout — 5 noisy
    # lines for one down database. Mute that logger for the probe's
    # duration only (restored in ``finally``); the single actionable
    # line below is the whole story.
    pool_logger = logging.getLogger("psycopg.pool")
    previous_level = pool_logger.level
    pool_logger.setLevel(logging.CRITICAL)
    try:
        reader.query("SELECT 1")
        return reader
    except psycopg.Error as exc:
        print(
            f"[cortex-viz] Cortex PostgreSQL unreachable at "
            f"{_redact_url(reader.url)} ({type(exc).__name__}: {exc}) — "
            "serving in no-DB mode: the Trace view works fully from "
            "~/.claude session logs + git; Graph/Brain/Knowledge/Wiki/Board "
            "need Cortex (https://github.com/cdeust/Cortex). Start Cortex's "
            "PostgreSQL or fix DATABASE_URL to enable them; pass --no-db "
            f"(or {NO_DB_ENV}=1) to skip this probe entirely.",
            file=sys.stderr,
        )
        reader.close()
        return None
    finally:
        pool_logger.setLevel(previous_level)


__all__ = [
    "DB_UNREACHABLE_ERRORS",
    "NO_DB_ENV",
    "no_db_requested",
    "open_store_or_none",
]
