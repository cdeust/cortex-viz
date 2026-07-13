"""Read-only schema preflight for the graph-build path.

The graph build has no schema guard: every loader below assumes the
Cortex Postgres schema already carries the objects it selects from, and
none of them check first. On a database whose schema predates those
objects, the build fails deep inside ``graph_build_run.py`` (phase
"error") with stderr routed to ``DEVNULL`` by ``http_launcher.py`` — the
user sees a broken graph with no diagnostic. Root cause: RCA
2026-07-13, no schema guard at any layer between ``open_visualization``
and the first catalog-missing SQL error.

This module runs ONE read-only catalog query — no DDL, no writes — and
reports which required objects are absent, each traced to the loader
that consumes it so ``open_visualization`` can produce an actionable
message instead of an opaque build failure.

Required objects and their consuming loader:

  * table ``memories``        — memory_read.py (every ``MemoryReader``
    query; e.g. ``get_hot_memories`` at memory_read.py:288)
  * table ``entities``        — memory_read.py:404 (``get_entity``),
    memory_read.py:418 (``get_all_entities``)
  * table ``memory_entities``  — memory_read.py:1115 (memory<->entity
    edge loader feeding the MEMORY->ENTITY graph edges)
  * function ``effective_heat(memories, timestamptz, ...)``
    — memory_read.py:245 (``_HEAT_EXPR``), memory_read.py:1095 (galaxy
    node heat); Cortex's live-heat read path (pg_schema.py, v3.25.0)
  * function ``get_temporal_co_access(real, integer, integer)``
    — memory_associations.py:257 (docstring), memory_associations.py:264
    (``_TEMPORAL_ASSOCIATION_SQL``), memory_associations.py:475
    (call-parameter wiring) — the v3 temporal association channel
  * column ``memories.supersedes_id``
    — memory_supersede.py:38 (``_SUPERSEDE_SQL``) — the recorded
    supersession lineage read by ``load_supersede_edges``
  * column ``memories.embedding`` (pgvector)
    — memory_associations.py:232-241 (``_SEMANTIC_ASSOCIATION_SQL``) —
    the v2 semantic-association HNSW kNN channel

Function checks match on name + schema only (``pg_proc``/``pg_namespace``),
not the full argument-type signature: a ``to_regprocedure(...)`` exact-
signature lookup requires PostgreSQL to parse the ``memories`` composite
type as an argument type, which raises a hard catalog error (not a NULL
result) when the ``memories`` table itself is absent — exactly the
schema state this preflight must degrade gracefully on. Name-level
existence is sufficient to distinguish "old schema, function missing"
from "current schema" for every version this preflight has seen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class PgStore(Protocol):
    """The read primitive ``check_schema`` needs from its store.

    Matches ``MemoryReader.query`` (see ``memory_read.py``) exactly —
    no wider surface, per ISP: this module only ever issues one SELECT
    and never writes, so it declares nothing beyond that one method.
    """

    def query(
        self, sql: str, params: Any = None, *, batch: bool = False
    ) -> list[dict[str, Any]]: ...


# One row, one round trip: every check is a pure catalog lookup
# (to_regclass / pg_proc / information_schema.columns) — none of these
# can raise on a missing object, unlike to_regprocedure with typed args
# (see module docstring). Safe to run against any schema state,
# including a completely empty database.
_PREFLIGHT_SQL = """
SELECT
    to_regclass('public.memories') IS NOT NULL AS has_memories,
    to_regclass('public.entities') IS NOT NULL AS has_entities,
    to_regclass('public.memory_entities') IS NOT NULL AS has_memory_entities,
    EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'effective_heat'
    ) AS has_effective_heat,
    EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'get_temporal_co_access'
    ) AS has_get_temporal_co_access,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'memories'
          AND column_name = 'supersedes_id'
    ) AS has_supersedes_id,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'memories'
          AND column_name = 'embedding'
    ) AS has_embedding
"""

# (result column, human-readable object name, consuming loader) — the
# single source of truth for both the SQL above and the missing-object
# messages below. Keep in lockstep with the module docstring.
_REQUIREMENTS: tuple[tuple[str, str, str], ...] = (
    ("has_memories", "table memories", "memory_read.py"),
    ("has_entities", "table entities", "memory_read.py"),
    ("has_memory_entities", "table memory_entities", "memory_read.py"),
    (
        "has_effective_heat",
        "function effective_heat(memories, timestamptz, ...)",
        "memory_read.py",
    ),
    (
        "has_get_temporal_co_access",
        "function get_temporal_co_access(real, integer, integer)",
        "memory_associations.py",
    ),
    (
        "has_supersedes_id",
        "column memories.supersedes_id",
        "memory_supersede.py",
    ),
    (
        "has_embedding",
        "column memories.embedding (pgvector)",
        "memory_associations.py",
    ),
)


@dataclass(frozen=True)
class SchemaPreflightResult:
    """Outcome of a single ``check_schema`` call.

    Postcondition: ``ok`` is True iff ``missing`` is empty. ``missing``
    entries are human-readable, e.g. ``"function effective_heat(...) —
    required by memory_associations.py"``, ready to surface verbatim in
    a user-facing message.
    """

    ok: bool
    missing: tuple[str, ...] = ()


def check_schema(pg_store: PgStore) -> SchemaPreflightResult:
    """Run the read-only catalog preflight against ``pg_store``.

    Precondition: ``pg_store`` exposes ``.query(sql, params, *,
    batch=False) -> list[dict]`` (the ``MemoryReader`` read primitive —
    see ``memory_read.MemoryReader.query``); a single connection is
    borrowed and returned, no transaction is left open.
    Postcondition: returns a ``SchemaPreflightResult`` reflecting every
    object in ``_REQUIREMENTS``; never raises for a missing-schema
    database (see module docstring on why function checks avoid
    ``to_regprocedure``). Any other error (e.g. the database itself is
    unreachable) propagates — that is not a schema question and the
    caller already has its own connectivity error handling.
    """
    rows = pg_store.query(_PREFLIGHT_SQL, (), batch=False)
    if not rows:
        # A single SELECT with no FROM clause always returns exactly one
        # row; an empty result means the query round trip itself broke,
        # not a schema gap. Treat conservatively as "nothing confirmed
        # present" rather than raising IndexError on rows[0].
        missing = tuple(
            f"{desc} — required by {loader}" for _, desc, loader in _REQUIREMENTS
        )
        return SchemaPreflightResult(ok=False, missing=missing)
    row = rows[0]
    missing = tuple(
        f"{desc} — required by {loader}"
        for col, desc, loader in _REQUIREMENTS
        if not row.get(col)
    )
    return SchemaPreflightResult(ok=not missing, missing=missing)


__all__ = ["PgStore", "SchemaPreflightResult", "check_schema"]
