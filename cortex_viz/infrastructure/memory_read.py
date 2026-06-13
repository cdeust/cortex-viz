"""Read-only PostgreSQL reader — the cortex-viz ↔ Cortex data contract.

This module severs the ONLY hard code-coupling that remained between the
visualization stack and the Cortex memory engine: the standalone HTTP server
used to build ``mcp_server.infrastructure.memory_store.MemoryStore`` directly
(``http_standalone.py:113``). The viz never needs Cortex's *live objects* — it
needs *rows*. Those rows live in PostgreSQL, a shared artifact reachable over
``DATABASE_URL``.

``MemoryReader`` exposes the read surface the viz server consumes and — like
``MemoryStore`` — serves it from TWO connection pools rather than one shared
connection:

  * ``interactive_pool`` (small, fast)  — hot-path requests: the stats HUD,
    SSE progress, node clicks, sankey. Must never block.
  * ``batch_pool``       (bounded)      — the heavy galaxy graph build, which
    streams the whole corpus. Bounded max-size so the build cannot exhaust
    connections or spike CPU, and runs on its OWN pool so it can never starve
    the interactive path.

A single shared connection (the original design) was a regression: the build
thread and HTTP request threads contended on one psycopg connection — which
runs one query at a time and is not safe across threads — so every request
serialized behind the build and the UI froze. Two sized pools restore the
concurrency MemoryStore had (its interactive/batch split).

Boundary invariant: this module imports ``psycopg`` + Python stdlib + the
copied ``cortex_viz`` config ONLY. It must never import ``mcp_server.*``.

SQL is transcribed verbatim from the corresponding Cortex mixins so the viz
renders byte-identical data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from cortex_viz.infrastructure.memory_config import get_memory_settings

# source: mcp_server/infrastructure/memory_config.py:47 — 127.0.0.1 not
# localhost to avoid IPv6 ::1 / peer-auth ambiguity.
_DEFAULT_DATABASE_URL = "postgresql://127.0.0.1:5432/cortex"


def _resolve_database_url() -> str:
    """Resolve the shared Cortex DATABASE_URL.

    Mirrors ``mcp_server.infrastructure.pg_store._get_database_url``: an empty
    value or an unexpanded ``${...}`` token is treated as unset.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "${" in url:
        return _DEFAULT_DATABASE_URL
    return url


class _MaterializedCursor:
    """Cursor surrogate that pre-fetches rows so the pooled connection can be
    returned immediately. source: pg_store.py _MaterializedCursor."""

    __slots__ = ("_rows", "_idx", "_rowcount")

    def __init__(self, cursor: psycopg.Cursor) -> None:
        self._rowcount = cursor.rowcount
        try:
            self._rows = cursor.fetchall()
        except (psycopg.ProgrammingError, TypeError):
            self._rows = []
        self._idx = 0

    def fetchone(self) -> dict | None:
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self) -> list:
        remaining = self._rows[self._idx :]
        self._idx = len(self._rows)
        return remaining

    @property
    def rowcount(self) -> int:
        return self._rowcount

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class MemoryReader:
    """Read-only, two-pool view over Cortex's PostgreSQL store."""

    def __init__(self, database_url: str | None = None) -> None:
        self._url = database_url or _resolve_database_url()
        # NO single connection. Every read — hot-path or build — is served
        # from a pool (interactive or batch). A single shared connection
        # serialized all threads behind whichever query held it; pools give
        # each concurrent caller its own connection.
        self._interactive_pool: ConnectionPool | None = None
        self._batch_pool: ConnectionPool | None = None

    # ── Pools ─────────────────────────────────────────────────────────
    # Sizes from cortex_viz.infrastructure.memory_config (Cortex's proven
    # values: interactive 2–8, batch 1–2). Override via CORTEX_MEMORY_POOL_*.

    def _open_pool(self, min_size: int, max_size: int, timeout: float) -> ConnectionPool:
        return ConnectionPool(
            conninfo=self._url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )

    @property
    def interactive_pool(self) -> ConnectionPool:
        if self._interactive_pool is None:
            s = get_memory_settings()
            self._interactive_pool = self._open_pool(
                s.POOL_INTERACTIVE_MIN, s.POOL_INTERACTIVE_MAX,
                s.POOL_INTERACTIVE_TIMEOUT_S,
            )
        return self._interactive_pool

    @property
    def batch_pool(self) -> ConnectionPool:
        if self._batch_pool is None:
            s = get_memory_settings()
            self._batch_pool = self._open_pool(
                s.POOL_BATCH_MIN, s.POOL_BATCH_MAX, s.POOL_BATCH_TIMEOUT_S,
            )
        return self._batch_pool

    def _execute(
        self, query: str, params: Any = None, *, batch: bool = False
    ) -> _MaterializedCursor:
        """Borrow a connection from the chosen pool, run, materialize, return.

        ``batch=True`` routes to the bounded batch pool (galaxy build / bulk
        scans); the default interactive pool serves the hot path.
        """
        pool = self.batch_pool if batch else self.interactive_pool
        with pool.connection() as conn:
            return _MaterializedCursor(conn.execute(query, params))

    def close(self) -> None:
        for pool in (self._interactive_pool, self._batch_pool):
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    pass

    # ── Normalization ─────────────────────────────────────────────────
    # source: pg_store.py:860 _normalize_memory_row, minus embedding→bytes
    # (the viz never reads embeddings; dropped to keep payloads small and the
    # read path pgvector-free).

    def _normalize_memory_row(self, row: dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        if "heat" not in d and "heat_base" in d:
            d["heat"] = d["heat_base"]
        d.pop("embedding", None)
        if isinstance(d.get("tags"), str):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
        for field in ("created_at", "ingested_at", "last_accessed",
                      "last_reconsolidated"):
            if isinstance(d.get(field), datetime):
                d[field] = d[field].isoformat()
        return d

    # ── Memories ──────────────────────────────────────────────────────

    def get_hot_memories(
        self,
        min_heat: float = 0.7,
        limit: int = 20,
        include_benchmarks: bool = False,
    ) -> list[dict[str, Any]]:
        bench_filter = (
            "" if include_benchmarks else "AND NOT coalesce(is_benchmark, FALSE) "
        )
        # limit<=0 is the build/vitals full-corpus scan → batch pool.
        batch = limit <= 0
        if limit > 0:
            rows = self._execute(
                f"SELECT * FROM memories WHERE heat_base >= %s {bench_filter}"
                "ORDER BY heat_base DESC LIMIT %s",
                (min_heat, limit), batch=batch,
            ).fetchall()
        else:
            rows = self._execute(
                f"SELECT * FROM memories WHERE heat_base >= %s {bench_filter}"
                "ORDER BY heat_base DESC",
                (min_heat,), batch=batch,
            ).fetchall()
        return [self._normalize_memory_row(r) for r in rows]

    def get_recent_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
        return [self._normalize_memory_row(r) for r in rows]

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM memories WHERE id = %s", (memory_id,)
        ).fetchone()
        return self._normalize_memory_row(row) if row is not None else None

    def count_memories(self) -> dict[str, int]:
        row = self._execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE store_type = 'episodic') AS episodic,
                COUNT(*) FILTER (WHERE store_type = 'semantic') AS semantic,
                COUNT(*) FILTER (WHERE heat_base >= 0.05) AS active,
                COUNT(*) FILTER (WHERE heat_base < 0.05) AS archived,
                COUNT(*) FILTER (WHERE is_stale) AS stale,
                COUNT(*) FILTER (WHERE is_protected) AS protected
            FROM memories
            """
        ).fetchone()
        return dict(row) if row else {}

    def get_avg_heat(self) -> float:
        row = self._execute("SELECT AVG(heat_base) AS avg_heat FROM memories").fetchone()
        return float(row["avg_heat"] or 0.0) if row else 0.0

    def get_domain_counts(self) -> dict[str, int]:
        rows = self._execute(
            "SELECT COALESCE(domain, 'unclassified') AS d, COUNT(*) AS c "
            "FROM memories WHERE NOT is_stale GROUP BY domain"
        ).fetchall()
        return {r["d"]: r["c"] for r in rows}

    # ── Entities ──────────────────────────────────────────────────────

    def get_entity_by_id(self, entity_id: int) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM entities WHERE id = %s", (entity_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_top_entities_for_domain(
        self, domain_slug: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM entities WHERE domain = %s "
            "ORDER BY heat DESC, mention_count DESC LIMIT %s",
            (domain_slug, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_entities(
        self, min_heat: float = 0.05, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        # Bulk scan for the build → batch pool.
        if include_archived:
            rows = self._execute(
                "SELECT * FROM entities WHERE heat >= %s", (min_heat,), batch=True
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM entities WHERE heat >= %s AND NOT archived",
                (min_heat,), batch=True,
            ).fetchall()
        return [dict(r) for r in rows]

    def count_entities(self) -> int:
        row = self._execute("SELECT COUNT(*) AS c FROM entities").fetchone()
        return row["c"] if row else 0

    # ── Relationships ─────────────────────────────────────────────────

    def count_relationships(self) -> int:
        row = self._execute("SELECT COUNT(*) AS c FROM relationships").fetchone()
        return row["c"] if row else 0

    def get_all_relationships(self) -> list[dict[str, Any]]:
        # Bulk scan for the build → batch pool.
        rows = self._execute(
            "SELECT id, source_entity_id, target_entity_id, "
            "relationship_type, weight, is_causal, confidence, "
            "release_probability, facilitation, depression, last_reinforced "
            "FROM relationships",
            batch=True,
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Consolidation / triggers ──────────────────────────────────────

    def get_last_consolidation(self) -> str | None:
        row = self._execute(
            "SELECT timestamp FROM consolidation_log "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row["timestamp"].isoformat() if row else None

    def count_active_triggers(self) -> int:
        row = self._execute(
            "SELECT COUNT(*) AS c FROM prospective_memories WHERE is_active"
        ).fetchone()
        return row["c"] if row else 0

    # ── Galaxy graph-build read path (batch pool) ─────────────────────

    def iter_hot_memories_chunked(
        self,
        min_heat: float = 0.0,
        include_benchmarks: bool = True,
        chunk_size: int = 1000,
        columns: str = "*",
        hard_limit: int | None = None,
    ) -> "Iterator[list[dict[str, Any]]]":
        """Stream hot memories hottest-first via KEYSET pagination, on the
        batch pool. Each chunk borrows + returns a connection, so between
        chunks the connection is free and interactive requests are unaffected.
        source: pg_store_queries.py iter_hot_memories_chunked.
        """
        bench_filter = (
            "" if include_benchmarks else "AND NOT coalesce(is_benchmark, FALSE) "
        )
        yielded = 0
        last_heat: float | None = None
        last_id: int | None = None
        cap = int(hard_limit) if hard_limit and hard_limit > 0 else None
        while True:
            page = int(chunk_size)
            if cap is not None:
                remaining = cap - yielded
                if remaining <= 0:
                    return
                page = min(page, remaining)
            if last_heat is None:
                where = "heat_base >= %s "
                params: list[Any] = [min_heat]
            else:
                where = "heat_base >= %s AND (heat_base, id) < (%s, %s) "
                params = [min_heat, last_heat, last_id]
            sql = (
                f"SELECT {columns} FROM memories WHERE {where}{bench_filter}"
                f"ORDER BY heat_base DESC, id DESC LIMIT {page}"
            )
            rows = self._execute(sql, tuple(params), batch=True).fetchall()
            if not rows:
                return
            yield [self._normalize_memory_row(dict(r)) for r in rows]
            yielded += len(rows)
            tail = rows[-1]
            last_heat = tail["heat_base"]
            last_id = tail["id"]
            if len(rows) < page:
                return

    def list_memory_entity_edges(self) -> list[dict[str, Any]]:
        """memory_entities join → MEMORY→ENTITY edges (build, batch pool).
        source: pg_store_entities.py list_memory_entity_edges."""
        rows = self._execute(
            "SELECT memory_id, entity_id FROM memory_entities", batch=True
        ).fetchall()
        return [
            {"memory_id": r["memory_id"], "entity_id": r["entity_id"]}
            for r in rows
            if r.get("memory_id") is not None and r.get("entity_id") is not None
        ]

    def search_by_tag_vector(
        self,
        query_embedding: bytes | None,
        tag: str,
        domain: str | None = None,
        min_heat: float = 0.01,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Tag-filtered search (build calls it only with query_embedding=None,
        so the read path stays pgvector-free). source: pg_store_queries.py."""
        if query_embedding is not None:
            raise NotImplementedError(
                "vector-mode search_by_tag_vector is not supported in the "
                "read-only viz path (viz callers pass query_embedding=None)"
            )
        rows = self._execute(
            "SELECT *, heat_base::REAL AS score FROM memories "
            "WHERE tags @> %s::jsonb AND heat_base >= %s AND NOT is_stale "
            "AND ((%s::TEXT IS NULL) OR domain = %s OR is_global = TRUE) "
            "ORDER BY heat_base DESC LIMIT %s",
            (json.dumps([tag]), min_heat, domain, domain, limit),
            batch=True,
        ).fetchall()
        return [self._normalize_memory_row(r) for r in rows]
