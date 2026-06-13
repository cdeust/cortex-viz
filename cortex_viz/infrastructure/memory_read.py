"""Read-only PostgreSQL reader — the cortex-viz ↔ Cortex data contract.

This module severs the ONLY hard code-coupling that remained between the
visualization stack and the Cortex memory engine: the standalone HTTP server
used to build ``mcp_server.infrastructure.memory_store.MemoryStore`` directly
(``http_standalone.py:113``). The viz never needs Cortex's *live objects* — it
needs *rows*. Those rows live in PostgreSQL, a shared artifact reachable over
``DATABASE_URL``.

``MemoryReader`` exposes exactly the surface the viz server consumes — the 14
read methods called as ``store.<method>(...)`` plus the dict-row connection
``_conn`` used by the four raw sankey ``SELECT`` sites in
``http_standalone_endpoints.py``. It is read-only: no writes, no schema init,
no pgvector adapter (embeddings are never rendered, so the column is dropped on
normalization to keep payloads lean).

Boundary invariant: this module imports ``psycopg`` + Python stdlib ONLY. It
must never import ``mcp_server.*``. Enforced by ``grep -r mcp_server
cortex_viz == 0``.

The SQL below is transcribed verbatim from the corresponding Cortex mixins
(``pg_store_queries``, ``pg_store_entities``, ``pg_store_relationships``,
``pg_store_stats``, ``pg_store``) so the viz renders byte-identical data. Any
divergence is a contract break.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

# source: mcp_server/infrastructure/memory_config.py:47 — 127.0.0.1 not
# localhost to avoid IPv6 ::1 / peer-auth ambiguity.
_DEFAULT_DATABASE_URL = "postgresql://127.0.0.1:5432/cortex"


def _resolve_database_url() -> str:
    """Resolve the shared Cortex DATABASE_URL.

    Mirrors ``mcp_server.infrastructure.pg_store._get_database_url``: an empty
    value or an unexpanded ``${...}`` token (Claude Code passes the literal
    through when a user_config option is unset) is treated as unset, falling
    back to the default.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "${" in url:
        return _DEFAULT_DATABASE_URL
    return url


class MemoryReader:
    """Read-only view over Cortex's PostgreSQL store for the viz server."""

    def __init__(self, database_url: str | None = None) -> None:
        self._url = database_url or _resolve_database_url()
        # Single autocommit connection, dict rows — matches the row factory
        # MemoryStore uses (pg_store.py:133) so column access is identical.
        self._conn: psycopg.Connection = psycopg.connect(
            self._url, row_factory=dict_row, autocommit=True
        )

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    # ── Normalization ─────────────────────────────────────────────────
    # source: pg_store.py:860 _normalize_memory_row, minus the embedding→bytes
    # conversion (the viz never reads embeddings; dropping the column keeps the
    # SSE payload small and removes the pgvector dependency from the read path).

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
        for field in (
            "created_at",
            "ingested_at",
            "last_accessed",
            "last_reconsolidated",
        ):
            if isinstance(d.get(field), datetime):
                d[field] = d[field].isoformat()
        return d

    # ── Memories ──────────────────────────────────────────────────────
    # source: pg_store_queries.py / pg_store_stats.py / pg_store.py

    def get_hot_memories(
        self,
        min_heat: float = 0.7,
        limit: int = 20,
        include_benchmarks: bool = False,
    ) -> list[dict[str, Any]]:
        bench_filter = (
            "" if include_benchmarks else "AND NOT coalesce(is_benchmark, FALSE) "
        )
        if limit > 0:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE heat_base >= %s {bench_filter}"
                "ORDER BY heat_base DESC LIMIT %s",
                (min_heat, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE heat_base >= %s {bench_filter}"
                "ORDER BY heat_base DESC",
                (min_heat,),
            ).fetchall()
        return [self._normalize_memory_row(r) for r in rows]

    def get_recent_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [self._normalize_memory_row(r) for r in rows]

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = %s", (memory_id,)
        ).fetchone()
        return self._normalize_memory_row(row) if row is not None else None

    def count_memories(self) -> dict[str, int]:
        row = self._conn.execute(
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
        row = self._conn.execute(
            "SELECT AVG(heat_base) AS avg_heat FROM memories"
        ).fetchone()
        return float(row["avg_heat"] or 0.0) if row else 0.0

    def get_domain_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT COALESCE(domain, 'unclassified') AS d, COUNT(*) AS c "
            "FROM memories WHERE NOT is_stale GROUP BY domain"
        ).fetchall()
        return {r["d"]: r["c"] for r in rows}

    # ── Entities ──────────────────────────────────────────────────────
    # source: pg_store_entities.py

    def get_entity_by_id(self, entity_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id = %s", (entity_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_top_entities_for_domain(
        self, domain_slug: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM entities WHERE domain = %s "
            "ORDER BY heat DESC, mention_count DESC LIMIT %s",
            (domain_slug, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_entities(
        self, min_heat: float = 0.05, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        if include_archived:
            rows = self._conn.execute(
                "SELECT * FROM entities WHERE heat >= %s", (min_heat,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM entities WHERE heat >= %s AND NOT archived",
                (min_heat,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_entities(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM entities"
        ).fetchone()
        return row["c"] if row else 0

    # ── Relationships ─────────────────────────────────────────────────
    # source: pg_store_relationships.py

    def count_relationships(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM relationships"
        ).fetchone()
        return row["c"] if row else 0

    def get_all_relationships(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, source_entity_id, target_entity_id, "
            "relationship_type, weight, is_causal, confidence, "
            "release_probability, facilitation, depression, last_reinforced "
            "FROM relationships"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Consolidation / triggers ──────────────────────────────────────
    # source: pg_store_stats.py

    def get_last_consolidation(self) -> str | None:
        row = self._conn.execute(
            "SELECT timestamp FROM consolidation_log "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row["timestamp"].isoformat() if row else None

    def count_active_triggers(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM prospective_memories WHERE is_active"
        ).fetchone()
        return row["c"] if row else 0

    # ── Galaxy graph-build read path ──────────────────────────────────
    # The workflow-graph build (handlers/workflow_graph via
    # workflow_graph_source_pg) reads three more methods beyond the 14 the
    # HTTP routes use. Transcribed verbatim from Cortex pg_store mixins.

    def iter_hot_memories_chunked(
        self,
        min_heat: float = 0.0,
        include_benchmarks: bool = True,
        chunk_size: int = 1000,
        columns: str = "*",
        hard_limit: int | None = None,
    ) -> "Iterator[list[dict[str, Any]]]":
        """Stream hot memories hottest-first via KEYSET pagination.

        source: pg_store_queries.py iter_hot_memories_chunked — keyset paging
        ``WHERE (heat_base, id) < (last_heat, last_id) ORDER BY heat_base DESC,
        id DESC LIMIT n`` walks the composite index one bounded page at a time
        so the first batch lands in ~ms (avoids the full-table sort stall).
        ``columns`` is an internal projection allowlist (NOT user input); keyset
        values are bound params.
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
            rows = self._conn.execute(sql, tuple(params)).fetchall()
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
        """Every row of the ``memory_entities`` join table → MEMORY→ENTITY
        edges. source: pg_store_entities.py list_memory_entity_edges."""
        rows = self._conn.execute(
            "SELECT memory_id, entity_id FROM memory_entities"
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
        """Tag-filtered memory search. source: pg_store_queries.py.

        The viz graph-build calls this only with ``query_embedding=None``
        (tag-filtered, heat-ordered — e.g. tag='tool:bash' command events), so
        the read path stays pgvector-free. The vector-ranked branch needs the
        pgvector adapter registered and is not used by the viz; it raises
        rather than silently degrade.
        """
        if query_embedding is not None:
            raise NotImplementedError(
                "vector-mode search_by_tag_vector is not supported in the "
                "read-only viz path (viz callers pass query_embedding=None)"
            )
        rows = self._conn.execute(
            "SELECT *, heat_base::REAL AS score FROM memories "
            "WHERE tags @> %s::jsonb AND heat_base >= %s AND NOT is_stale "
            "AND ((%s::TEXT IS NULL) OR domain = %s OR is_global = TRUE) "
            "ORDER BY heat_base DESC LIMIT %s",
            (json.dumps([tag]), min_heat, domain, domain, limit),
        ).fetchall()
        return [self._normalize_memory_row(r) for r in rows]
