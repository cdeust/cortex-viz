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

import re

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from cortex_viz.infrastructure.memory_config import get_memory_settings

# source: mcp_server/infrastructure/memory_config.py:47 — 127.0.0.1 not
# localhost to avoid IPv6 ::1 / peer-auth ambiguity.
_DEFAULT_DATABASE_URL = "postgresql://127.0.0.1:5432/cortex"

# ── A3 goal / task-set keyword promotion ─────────────────────────────────────
# Local transcription of mcp_server/core/goal_maintenance's keyword surface
# (_MIN_KEYWORD_LEN, _STOP_WORDS, _WORD_RE), duplicated here because this module
# must NOT import mcp_server.* (module header boundary invariant) — the same
# idiom as MemoryReader._FAMILIARITY_THRESHOLD. Keep in sync with
# goal_maintenance if that module's filter changes.
_GOAL_MIN_KEYWORD_LEN: int = 3
_GOAL_STOP_WORDS = frozenset({"the", "and", "for", "with", "that", "this", "from"})
_GOAL_WORD_RE = re.compile(r"[a-z0-9]+")
# How many goal keywords to join into the sidebar's task label.
_GOAL_LABEL_MAX_KEYWORDS: int = 4


def _goal_tokens(text: str) -> list[str]:
    """Lowercase word-boundary tokens of a trigger condition, filtered exactly
    like mcp_server.core.goal_maintenance._tokenize (len >= 3, no stop words)."""
    return [
        w
        for w in _GOAL_WORD_RE.findall(text.lower())
        if len(w) >= _GOAL_MIN_KEYWORD_LEN and w not in _GOAL_STOP_WORDS
    ]


# Local transcription of mcp_server/core/forward_model's B3 cerebellar
# forward-model primitive (CORRECTION_GAIN, ERROR_DEADBAND, the predict→correct
# EMA), duplicated here because this module must NOT import mcp_server.* (module
# header boundary invariant) — the same idiom as _goal_tokens /
# _FAMILIARITY_THRESHOLD. Keep in sync with forward_model.py if its constants or
# the correction rule change.
_FM_CORRECTION_GAIN: float = 0.5
_FM_ERROR_DEADBAND: float = 0.05


def _fm_mean_abs_error(trajectory: list[float]) -> float:
    """Mean absolute one-step forward-model error over a scalar ``trajectory``.

    Mirrors mcp_server.core.forward_model: seed the running estimate with the
    first value, then for each subsequent value score the residual against the
    one-step prediction (the current estimate) and fold a fixed-gain fraction of
    it back into the estimate (residuals within the deadband leave the estimate
    frozen). Returns the mean |residual| across the corrected steps — 0.0 for a
    trajectory shorter than two points (nothing to predict yet).
    """
    if len(trajectory) < 2:
        return 0.0
    estimate = float(trajectory[0])
    total = 0.0
    n = 0
    for actual in trajectory[1:]:
        actual = float(actual)
        error = actual - estimate
        total += abs(error)
        n += 1
        if abs(error) > _FM_ERROR_DEADBAND:
            estimate = estimate + _FM_CORRECTION_GAIN * error
    return round(total / n, 6) if n else 0.0


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

    def query(
        self, sql: str, params: Any = None, *, batch: bool = False
    ) -> list[dict[str, Any]]:
        """Public read primitive: run ``sql`` and return all rows as dicts.

        A thin, read-only wrapper over the pooled cursor so sibling
        infrastructure modules (memory_browse, wiki_read) can own their own
        SQL without reaching into ``_execute`` or duplicating pool handling.
        Reads only — there is no commit; DML would be rolled back when the
        pooled connection is returned.
        """
        return self._execute(sql, params, batch=batch).fetchall()

    def close(self) -> None:
        for pool in (self._interactive_pool, self._batch_pool):
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    pass

    # A memory's CURRENT heat is derived at READ TIME by Cortex's effective_heat
    # SQL function (lazy A3 decay from heat_base over the consolidation stages);
    # the stored heat_base column is only the base the decay starts from, NOT the
    # live value. Every query that surfaces a memory's heat selects this so the
    # viz matches Cortex's own read path (pg_schema.py effective_heat /
    # recall_memories_lazy, Cortex >= 3.25.0). heat_base remains the physical
    # sort/keyset key (indexed, stable across a paginated scan) — effective_heat
    # is not indexable. The `m` alias must be the memories row in these queries.
    #   source: cortex mcp_server/infrastructure/pg_schema.py::effective_heat (v3.25.0).
    _HEAT_EXPR = "effective_heat(m, NOW())::REAL"

    # ── Normalization ─────────────────────────────────────────────────
    # source: pg_store.py:860 _normalize_memory_row, minus embedding→bytes
    # (the viz never reads embeddings; dropped to keep payloads small and the
    # read path pgvector-free).

    def _normalize_memory_row(self, row: dict[str, Any]) -> dict[str, Any]:
        d = dict(row)
        # `heat` is the effective_heat the query selected; only when a query did
        # not compute it (detail reads) do we fall back to the frozen base.
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
        # "Hot" is CURRENT heat, so filter+order on effective_heat, not the
        # frozen heat_base (which the homeostatic factor can push either way).
        batch = limit <= 0
        heat = self._HEAT_EXPR
        if limit > 0:
            rows = self._execute(
                f"SELECT {heat} AS heat, * FROM memories m "
                f"WHERE {heat} >= %s {bench_filter}"
                f"ORDER BY {heat} DESC LIMIT %s",
                (min_heat, limit), batch=batch,
            ).fetchall()
        else:
            rows = self._execute(
                f"SELECT {heat} AS heat, * FROM memories m "
                f"WHERE {heat} >= %s {bench_filter}"
                f"ORDER BY {heat} DESC",
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
                COUNT(*) FILTER (WHERE effective_heat(m, NOW()) >= 0.05) AS active,
                COUNT(*) FILTER (WHERE effective_heat(m, NOW()) < 0.05) AS archived,
                COUNT(*) FILTER (WHERE is_stale) AS stale,
                COUNT(*) FILTER (WHERE is_protected) AS protected
            FROM memories m
            """
        ).fetchone()
        return dict(row) if row else {}

    def get_avg_heat(self) -> float:
        row = self._execute(
            "SELECT AVG(effective_heat(m, NOW())) AS avg_heat FROM memories m"
        ).fetchone()
        return float(row["avg_heat"] or 0.0) if row else 0.0

    def get_stage_counts(self) -> dict[str, int]:
        """Consolidation-stage histogram, SQL-side. Feeds the dashboard's
        stage panel and the system-vitals pipeline rows — replaces the
        full-corpus scan (every row through ``effective_heat`` took ~50 s
        at 108k memories; this GROUP BY is ~140 ms, measured 2026-07-02)."""
        rows = self._execute(
            "SELECT consolidation_stage AS stage, COUNT(*) AS c "
            "FROM memories GROUP BY consolidation_stage"
        ).fetchall()
        return {r["stage"]: int(r["c"]) for r in rows if r["stage"]}

    def get_provenance_counts(self) -> dict[str, int]:
        """C1 source/reality-monitoring histogram (perceived / told /
        inferred). Empty when the column predates C1 (older Cortex store) —
        callers treat that as no provenance data, same graceful-absence
        idiom as ``count_procedural_skills``."""
        try:
            rows = self._execute(
                "SELECT COALESCE(source_attribution, 'unknown') AS attr, "
                "COUNT(*) AS c FROM memories GROUP BY 1"
            ).fetchall()
            return {r["attr"]: int(r["c"]) for r in rows}
        except Exception:
            return {}

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

    def count_procedural_skills(self) -> int:
        """Count learned procedural skills (B1). Zero when the table is absent
        (a Cortex store predating procedural memory) — treated as no skills."""
        try:
            row = self._execute(
                "SELECT COUNT(*) AS c FROM procedural_skills"
            ).fetchone()
            return row["c"] if row else 0
        except Exception:
            return 0

    def count_provenance(self) -> dict[str, int]:
        """Source-monitoring breakdown (C1): memory counts by epistemic origin
        — perceived / told / inferred / unknown. Zero-filled when the
        source_attribution column is absent (a Cortex store predating reality
        monitoring), so callers always get the four keys."""
        out = {"perceived": 0, "told": 0, "inferred": 0, "unknown": 0}
        try:
            rows = self._execute(
                "SELECT source_attribution AS a, COUNT(*) AS c "
                "FROM memories GROUP BY source_attribution"
            ).fetchall()
            for r in rows:
                key = r["a"] if r["a"] in out else "unknown"
                out[key] += r["c"]
        except Exception:
            pass  # column absent — report all-zero
        return out

    def count_crystallized_confabulations(self) -> int:
        """C1 read-side enforcement signal: semantic memories the confabulation
        gate flagged at the episodic->semantic PROMOTION point — an internally
        generated (INFERRED) cluster with zero perceptual grounding that was
        crystallized as a semantic FACT anyway (Johnson & Raye 1981). The
        consolidation writer (mcp_server/handlers/consolidation/cls.py) tags each
        such promotion 'confabulation-risk' and sets store_type='semantic'; this
        counts them standing in the store.

        Distinct from get_provenance_counts()['inferred'] (which the sidebar's
        sv-inferred already surfaces): that is every INFERRED memory AT REST,
        including raw episodic reasoning notes that make no factual claim. This
        counts only the subset that was PROMOTED to knowledge — the confabulations
        that actually crossed the crystallization gate, which is the specific
        failure mode C1's read-side gate exists to surface.

        Zero-filled when the tags/store_type columns are absent (a Cortex store
        predating C1 read-side or consolidation), so callers on an un-migrated
        store always get 0 — same graceful-absence idiom as get_provenance_counts.
        """
        try:
            row = self._execute(
                "SELECT COUNT(*) AS c FROM memories "
                "WHERE store_type = 'semantic' "
                "AND tags @> %s::jsonb AND NOT is_stale",
                (json.dumps(["confabulation-risk"]),),
            ).fetchone()
            return int(row["c"]) if row and row["c"] else 0
        except Exception:
            return 0  # column/tag absent — report zero

    def count_habituated_repeats(self) -> int:
        """Habituation suppression signal (E1): how many stored memories share
        a stimulus_signature with at least one other memory — i.e. repeated
        presentations of the same content that the write gate's response
        decrement (Rankin 2009) acted on. Counts the memories in every
        signature group of size >= 2, minus one representative per group, so the
        number is "surplus repeats that habituation is damping." Zero-filled
        when the stimulus_signature column is absent (a Cortex store predating
        habituation), so callers on an un-migrated store always get 0.
        """
        try:
            rows = self._execute(
                "SELECT stimulus_signature AS s, COUNT(*) AS c "
                "FROM memories WHERE stimulus_signature IS NOT NULL "
                "AND stimulus_signature <> '' GROUP BY stimulus_signature "
                "HAVING COUNT(*) > 1"
            ).fetchall()
            return sum(int(r["c"]) - 1 for r in rows)
        except Exception:
            return 0  # column absent — report zero

    def count_extinguished(self, threshold: float = 0.5) -> int:
        """Extinction suppression signal (E2): how many stored memories carry a
        reversible inhibitory extinction tag at or above ``threshold`` — the
        learned association is suppressed WITHOUT deletion (the row is fully
        present, not is_stale), so it can spontaneously recover or be reinstated
        (Bouton 2004, Learn. Mem. 11:485-494; Milad & Quirk 2012, Annu. Rev.
        Psychol. 63:129-151). This is distinct from active_forgetting's
        is_stale soft-delete: an extinguished memory is deprecated-but-retained.
        Zero-filled when the extinction_strength column is absent (a Cortex store
        predating extinction), so callers on an un-migrated store always get 0.
        """
        try:
            row = self._execute(
                "SELECT COUNT(*) AS c FROM memories "
                "WHERE extinction_strength >= %s AND NOT is_stale",
                (threshold,),
            ).fetchone()
            return int(row["c"]) if row and row["c"] else 0
        except Exception:
            return 0  # column absent — report zero

    def count_conflicting_claim_pairs(self) -> int:
        """Conflict-monitoring signal (A2): how many pairs of persisted claims
        disagree — claims that share at least one entity but carry opposing
        claim_types. This is the persisted counterpart of the recall-time
        conflict monitor (mcp_server/core/conflict_monitor.py routing to
        claim_resolver.plan_conflicts): the resolver flags decision↔limitation,
        method↔limitation, and decision↔decision pairs about the same entities,
        and this counts those same pairs standing in the store.

        Botvinick 2001 conflict = co-active incompatible responses; here the
        "co-activation" is a shared entity and the "incompatibility" is the
        opposing claim_type. Counts each unordered pair once (a.id < b.id).

        Zero-filled when the ``wiki.claim_events`` table is absent (a Cortex
        store predating the claim/wiki layer), so callers on a store without it
        always get 0 — same graceful-absence idiom as
        ``count_habituated_repeats`` / ``get_provenance_counts``.
        """
        try:
            rows = self._execute(
                "SELECT COUNT(*) AS c FROM wiki.claim_events a "
                "JOIN wiki.claim_events b "
                "  ON a.id < b.id AND a.entity_ids && b.entity_ids "
                "WHERE ("
                "  (a.claim_type = 'decision'   AND b.claim_type = 'limitation') OR "
                "  (a.claim_type = 'limitation' AND b.claim_type = 'decision')   OR "
                "  (a.claim_type = 'method'     AND b.claim_type = 'limitation') OR "
                "  (a.claim_type = 'limitation' AND b.claim_type = 'method')     OR "
                "  (a.claim_type = 'decision'   AND b.claim_type = 'decision')"
                ")"
            ).fetchone()
            return int(rows["c"]) if rows else 0
        except Exception:
            return 0  # table absent — report zero

    # C2 dual-process familiarity threshold. Mirrors
    # mcp_server/core/dual_process_retrieval.FAMILIARITY_THRESHOLD (0.92) —
    # duplicated as a local literal because this module must NOT import
    # mcp_server.* (boundary invariant, see module header). Cosine similarity
    # at/above this is a near-duplicate — the regime where an a-contextual
    # familiarity signal alone is trustworthy (Yonelinas 2002; Diana et al 2007).
    _FAMILIARITY_THRESHOLD: float = 0.92

    # A1 central-executive focus capacity: the Cowan (2001) ~4-chunk ceiling on
    # the focus of attention. Local transcription of
    # mcp_server.core.attentional_control.FOCUS_CAPACITY_DEFAULT (= 4); this
    # module must NOT import mcp_server.* (module header boundary invariant), the
    # same duplication idiom as _FAMILIARITY_THRESHOLD. Keep in sync with
    # attentional_control.py if that constant changes.
    _ATTENTION_FOCUS_CAPACITY: int = 4

    def count_attentional_salience(self, sample_limit: int = 300) -> dict[str, Any]:
        """Bottom-up attentional-salience footprint (A1 central-executive).

        Standing-store counterpart of the recall-time attentional re-weight
        (mcp_server/core/attentional_control.allocate_attention, wired into
        recall via recall_pipeline.attentional_focus_rerank). That stage scores
        each recall candidate by top-down query relevance PLUS a bottom-up
        salience term ``0.5·importance + 0.5·|valence|`` (Posner & Petersen
        1990's stimulus-driven capture), softmax-weights them, and nudges scores
        toward the salient/relevant few within the Cowan focus.

        The top-down half is query-dependent and in-flight — nothing to measure
        at rest. The bottom-up half IS a persisted property of every memory
        (``importance``, ``emotional_valence`` columns). This reader measures how
        CONCENTRATED that bottom-up salience is over the most recent
        ``sample_limit`` memories: the share of total salience mass held by the
        top ``_ATTENTION_FOCUS_CAPACITY`` (Cowan ~4) most-salient of them. High
        share = a few memories dominate stimulus-driven capture (a sharp
        spotlight even before any query); low share = salience spread evenly
        (attention rests on the query alone). This is a DESCRIPTIVE salience
        statistic over real columns — it does NOT run the softmax spotlight
        (that needs a live query) and does NOT reproduce recall ordering.

        Bounded by design (``sample_limit`` recent rows, one aggregate query) so
        it never triggers the whole-corpus scan the vitals docstring warns
        about. Returns ``{sampled, focus_share, mean_salience, max_salience}``.
        Zero-filled when the ``importance`` / ``emotional_valence`` columns are
        absent (a store predating affect) — same graceful-absence idiom as
        ``count_familiarity_resolvable`` / ``count_active_goal``, so a reader on
        an un-migrated store gets a neutral (all-zero) result.

        Honesty note: "salience" here is the fixed-constant bottom-up term
        ``0.5·importance + 0.5·|valence|`` A1 uses, NOT a learned saliency model;
        ``focus_share`` is a concentration ratio of that term, not an attention
        distribution (no softmax, no top-down query).
        """
        cap = self._ATTENTION_FOCUS_CAPACITY
        neutral = {
            "sampled": 0,
            "focus_share": 0.0,
            "mean_salience": 0.0,
            "max_salience": 0.0,
        }
        try:
            row = self._execute(
                "WITH sample AS ("
                "  SELECT 0.5 * COALESCE(importance, 0.0) "
                "       + 0.5 * ABS(COALESCE(emotional_valence, 0.0)) AS sal "
                "  FROM memories "
                "  ORDER BY created_at DESC LIMIT %s"
                "), ranked AS ("
                "  SELECT sal, ROW_NUMBER() OVER (ORDER BY sal DESC) AS rk "
                "  FROM sample"
                ") "
                "SELECT COUNT(*) AS sampled, "
                "  COALESCE(SUM(sal), 0.0) AS total_sal, "
                "  COALESCE(SUM(sal) FILTER (WHERE rk <= %s), 0.0) AS focus_sal, "
                "  COALESCE(AVG(sal), 0.0) AS mean_sal, "
                "  COALESCE(MAX(sal), 0.0) AS max_sal "
                "FROM ranked",
                (sample_limit, cap),
            ).fetchone()
            if not row:
                return neutral
            sampled = int(row["sampled"] or 0)
            if not sampled:
                return neutral
            total_sal = float(row["total_sal"] or 0.0)
            focus_sal = float(row["focus_sal"] or 0.0)
            # Concentration of bottom-up salience in the Cowan focus. When total
            # salience is zero (no importance/valence signal at all) the spotlight
            # has nothing to grip → report 0.0, not a divide-by-zero.
            focus_share = round(focus_sal / total_sal, 4) if total_sal > 0.0 else 0.0
            return {
                "sampled": sampled,
                "focus_share": focus_share,
                "mean_salience": round(float(row["mean_sal"] or 0.0), 4),
                "max_salience": round(float(row["max_sal"] or 0.0), 4),
            }
        except Exception:
            return neutral

    def count_familiarity_resolvable(self, sample_limit: int = 300) -> dict[str, Any]:
        """Dual-process retrieval signal (C2): over the most recent
        ``sample_limit`` embedded memories, how many are resolvable by
        FAMILIARITY ALONE — i.e. have a nearest OTHER neighbour whose cosine
        similarity clears ``_FAMILIARITY_THRESHOLD``.

        This is the standing-store counterpart of the recall-time familiarity
        triage (mcp_server/core/dual_process_retrieval.py): the triage reads a
        fast a-contextual max-similarity gate before the expensive recollection
        chain; here we measure what share of recent memories sit in the
        overwhelming-familiarity regime (a near-duplicate exists), where that
        gate alone would suffice. High share = a corpus with much redundant,
        familiarity-resolvable content; low share = memories that need slow
        contextual recollection to disambiguate.

        Bounded by design (``sample_limit`` recent rows, one indexed pgvector
        KNN lookup each) so it never triggers the whole-corpus scan the vitals
        docstring warns about. Returns ``{sampled, resolvable, share,
        mean_top_sim}``. Zero-filled when the ``embedding`` column / pgvector is
        absent (a store predating vectors), so callers on an un-migrated store
        always get zeros — same graceful-absence idiom as
        ``count_habituated_repeats`` / ``count_conflicting_claim_pairs``.

        Honesty note: "familiarity" here is the max-cosine-similarity heuristic,
        NOT a trained dual-process model; "resolvable by familiarity alone" means
        only that a near-duplicate neighbour exists above a fixed threshold.
        """
        try:
            row = self._execute(
                "WITH sample AS ("
                "  SELECT id, embedding FROM memories "
                "  WHERE embedding IS NOT NULL "
                "  ORDER BY created_at DESC LIMIT %s"
                ") "
                "SELECT COUNT(*) AS sampled, "
                "  COUNT(*) FILTER (WHERE nn.sim >= %s) AS resolvable, "
                "  COALESCE(AVG(nn.sim), 0.0) AS mean_top_sim "
                "FROM sample s "
                "CROSS JOIN LATERAL ("
                "  SELECT 1.0 - (m.embedding <=> s.embedding) AS sim "
                "  FROM memories m "
                "  WHERE m.id <> s.id AND m.embedding IS NOT NULL "
                "  ORDER BY m.embedding <=> s.embedding LIMIT 1"
                ") nn",
                (sample_limit, self._FAMILIARITY_THRESHOLD),
            ).fetchone()
            if not row:
                return {"sampled": 0, "resolvable": 0, "share": 0.0, "mean_top_sim": 0.0}
            sampled = int(row["sampled"] or 0)
            resolvable = int(row["resolvable"] or 0)
            share = round(resolvable / sampled, 4) if sampled else 0.0
            return {
                "sampled": sampled,
                "resolvable": resolvable,
                "share": share,
                "mean_top_sim": round(float(row["mean_top_sim"] or 0.0), 4),
            }
        except Exception:
            return {"sampled": 0, "resolvable": 0, "share": 0.0, "mean_top_sim": 0.0}

    def count_sleep_phase_outputs(self) -> dict[str, int]:
        """Two-phase consolidation footprint (F1): the standing outputs of the
        NREM/REM offline consolidation split
        (mcp_server/core/sleep_phases.py).

        - ``nrem`` — auto-narration semantic memories the NREM-like exact-replay
          phase (run_sleep_compute) stores; identified by ``source =
          'sleep-compute'``. This is the persisted trace of the replay/narration
          pass.
        - ``rem`` — abstract schemas the REM-like recombination/abstraction
          phase forms (schema_extraction.extract_schema_from_cluster + merge),
          persisted in the ``schemas`` table. This is the abstraction output the
          single pre-split pass never produced.

        Diekelmann & Born 2010 (active systems consolidation: NREM replay, REM
        integration/schema); van de Ven 2020 (offline replay phase). Each count
        is zero-filled independently when its table/column is absent (a Cortex
        store predating that phase), so callers on an un-migrated store always
        get both keys — same graceful-absence idiom as
        ``count_habituated_repeats`` / ``count_conflicting_claim_pairs``.

        Honesty note: these are STANDING counts of what the two phases have
        persisted, not a live per-cycle telemetry — the last-cycle NREM/REM
        counts flow separately through the consolidate handler's ``sleep_phases``
        block; this reader measures the cumulative footprint in the store.
        """
        out = {"nrem": 0, "rem": 0}
        try:
            row = self._execute(
                "SELECT COUNT(*) AS c FROM memories WHERE source = 'sleep-compute'"
            ).fetchone()
            out["nrem"] = int(row["c"]) if row else 0
        except Exception:
            pass  # source column / table absent — report zero
        try:
            row = self._execute("SELECT COUNT(*) AS c FROM schemas").fetchone()
            out["rem"] = int(row["c"]) if row else 0
        except Exception:
            pass  # schemas table absent — report zero
        return out

    def count_targeted_reactivation(self) -> dict[str, Any]:
        """Targeted memory reactivation footprint (F2): the cue that biased the
        most recent offline consolidation, if the store recorded one
        (mcp_server/core/targeted_reactivation.py).

        A TMR cue (topic / tag / entity / free-text) is a RUNTIME input to the
        consolidation pass — it re-weights which memories preferentially replay
        in the NREM phase (Rasch et al. 2007; Oudiette & Paller 2013). It is not
        an intrinsic property of any memory, so unlike the other vitals there is
        no per-row footprint to count. This reader instead reports the cue of
        the last consolidation cycle IF the store persisted it, by reading the
        optional ``tmr_cue`` / ``tmr_cued_replayed`` columns of
        ``consolidation_log``.

        Returns ``{"cue": <str|None>, "cued_replayed": <int>}``:
          - ``cue`` — the last cycle's cue string, or ``None`` when the last
            cycle ran without a cue (identity replay), when TMR was ablated, or
            when the store predates cue logging (the columns are absent).
          - ``cued_replayed`` — how many replayed memories matched that cue.

        Graceful absence: any missing table/column yields
        ``{"cue": None, "cued_replayed": 0}`` — the same zero-filled idiom as
        ``count_sleep_phase_outputs`` / ``count_conflicting_claim_pairs``. So on
        a current store (no ``tmr_cue`` column yet) the sidebar shows "--", which
        is honest: no cue-directed cycle has been recorded.

        Honesty note: this reports the STANDING record of the last cue, not a
        live per-cycle telemetry. The authoritative per-cycle TMR figures flow
        through the consolidate handler's ``sleep_phases.tmr`` block
        (``cue`` / ``cued_replayed`` / ``ablated``); this reader only surfaces
        whatever of that the store chose to persist.
        """
        out: dict[str, Any] = {"cue": None, "cued_replayed": 0}
        try:
            row = self._execute(
                "SELECT tmr_cue, tmr_cued_replayed FROM consolidation_log "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                cue = row["tmr_cue"]
                out["cue"] = cue if cue else None
                out["cued_replayed"] = int(row["tmr_cued_replayed"] or 0)
        except Exception:
            pass  # column/table absent — report the null (no-cue) case
        return out

    def count_stress_modulation(self) -> dict[str, Any]:
        """Stress-hormone (glucocorticoid) modulation footprint (D1): the
        session-stress scalar and consolidation gain of the most recent offline
        CLS consolidation cycle, if the store recorded them.

        Stress hormones modulate consolidation strength along an inverted-U:
        moderate session stress ENHANCES consolidation, extreme stress IMPAIRS
        it (Roozendaal & McGaugh 2011, Behav. Neurosci. 125:797-824; McGaugh
        2000, Science 287:248-251). The CLS handler
        (mcp_server/handlers/consolidation/cls.py) derives a session-stress
        scalar from the consolidated batch (urgency/deadline + failure lexical
        markers, reusing the emotional-tagging lexicons, plus the
        negative-valence share) and scales the pattern-recurrence bar by the
        inverted-U gain (mcp_server/core/stress_modulation.py).

        Like the TMR cue (F2), session stress is NOT an intrinsic per-row
        property — it is a per-cycle quantity — so this reader reports the last
        cycle's values IF the store persisted them, by reading the optional
        ``stress_scalar`` / ``consolidation_gain`` columns of
        ``consolidation_log``.

        Returns ``{"stress": <float>, "gain": <float>, "is_impairing": <bool>}``:
          - ``stress`` — the last cycle's session-stress scalar in [0, 1]
            (0.0 = a calm cycle, or the store predates stress logging);
          - ``gain`` — the inverted-U consolidation gain applied (1.0 =
            unmodulated: neutral stress, the mechanism ablated, or no logging);
          - ``is_impairing`` — True iff ``gain < 1.0`` (extreme stress on the
            falling arm of the inverted-U).

        Graceful absence: any missing table/column yields the neutral case
        ``{"stress": 0.0, "gain": 1.0, "is_impairing": False}`` — the same
        zero-filled idiom as ``count_targeted_reactivation`` /
        ``count_sleep_phase_outputs``. So on a current store (no
        ``stress_scalar`` column yet) the sidebar shows the neutral "1.00×",
        which is honest: no stress-modulated cycle has been recorded.

        Honesty note: this reports the STANDING record of the last cycle's
        stress, not live per-cycle telemetry — the authoritative per-cycle
        figures flow through the consolidate handler's CLS stats
        (``session_stress`` / ``consolidation_gain`` /
        ``effective_min_occurrences``). D1 is a DESIGN INFERENCE: the stress
        scalar is a lexical+valence proxy, not a measured glucocorticoid level,
        and the gain is a deterministic inverted-U (Hebb 1955 shape), not a
        fitted dose-response.
        """
        out: dict[str, Any] = {"stress": 0.0, "gain": 1.0, "is_impairing": False}
        try:
            row = self._execute(
                "SELECT stress_scalar, consolidation_gain FROM consolidation_log "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                out["stress"] = round(float(row["stress_scalar"] or 0.0), 4)
                gain = row["consolidation_gain"]
                out["gain"] = round(float(gain), 4) if gain is not None else 1.0
                out["is_impairing"] = out["gain"] < 1.0
        except Exception:
            pass  # column/table absent — report the neutral (unmodulated) case
        return out

    def count_active_goal(self) -> dict[str, Any]:
        """Active goal / task-set footprint (A3): the sustained goal vector
        promoted from the store's currently-active prospective triggers
        (mcp_server/core/goal_maintenance.py).

        Cortex's prospective triggers ("remember to do X when Y happens") are
        momentary, event-driven checks. A3 promotes the *active* ones into a
        sustained goal/task-set that biases the write gate and recall fusion
        toward goal-relevant information while it is in play — the Miller &
        Cohen (2001, Annu. Rev. Neurosci. 24:167-202) prefrontal task-set. This
        reader shows the STANDING footprint of that goal: the count of active
        triggers forming it, the size of its keyword surface, and a short label
        assembled from its top keywords, so the sidebar mirrors what the
        engine's goal vector is built from.

        Returns ``{"active": <bool>, "triggers": <int>, "keywords": <int>,
        "label": <str>}``:
          - ``active``   — True iff at least one active trigger contributes a
            keyword / entity / directory signal (an inactive goal is the write
            + recall identity, exactly the no-goal case);
          - ``triggers`` — number of active prospective triggers;
          - ``keywords`` — size of the promoted goal's keyword surface;
          - ``label``    — up to the first few goal keywords, joined, as a
            human-readable task label ("--" when the goal is inactive).

        Boundary note: the goal-promotion logic (token filter, stop words) is a
        small local transcription of ``mcp_server.core.goal_maintenance`` —
        this module must NOT import ``mcp_server.*`` (module header invariant),
        the same duplication idiom as ``_FAMILIARITY_THRESHOLD``.

        Graceful absence: any missing table/column yields the inactive case
        ``{"active": False, "triggers": 0, "keywords": 0, "label": None}`` — the
        same zero-filled idiom as ``count_stress_modulation`` /
        ``count_targeted_reactivation``. So on a store with no active triggers
        the sidebar honestly shows "--" (no goal in play → no write/recall
        bias). DESIGN INFERENCE: this is a keyword/entity goal-match promoted
        from the trigger surface, not a learned PFC task-set controller.
        """
        out: dict[str, Any] = {
            "active": False,
            "triggers": 0,
            "keywords": 0,
            "label": None,
        }
        try:
            rows = self._execute(
                "SELECT trigger_type, trigger_condition, target_directory "
                "FROM prospective_memories WHERE is_active"
            ).fetchall()
        except Exception:
            return out  # table/column absent — report the inactive (no-goal) case

        out["triggers"] = len(rows)
        keywords: set[str] = set()
        has_entity_or_dir = False
        for r in rows:
            ttype = r["trigger_type"]
            condition = (r["trigger_condition"] or "").strip()
            if ttype == "keyword_match":
                keywords.update(_goal_tokens(condition))
            elif ttype == "entity_match":
                if condition:
                    has_entity_or_dir = True
            elif ttype == "directory_match":
                target = (r["target_directory"] or condition).strip()
                if target:
                    has_entity_or_dir = True
        out["keywords"] = len(keywords)
        out["active"] = bool(keywords or has_entity_or_dir)
        if keywords:
            out["label"] = " ".join(sorted(keywords)[:_GOAL_LABEL_MAX_KEYWORDS])
        return out

    def count_forward_model(self, sample_limit: int = 300) -> dict[str, Any]:
        """Cerebellar forward-model footprint (B3): over the most recent
        ``sample_limit`` memories, the mean absolute one-step forward-model
        prediction error of the heat trajectory.

        The cerebellar forward model predicts the next value of a signal and
        corrects its estimate from the residual — the sensory prediction error
        (Wolpert, Miall & Kawato 1998, Trends Cogn. Sci. 2:338-347; Ito 2008,
        Nat. Rev. Neurosci. 9:304-313). Here the tracked signal is each recent
        memory's ``heat_base`` (the stored base activation, oldest→newest); the
        forward model (local transcription of mcp_server/core/forward_model.py,
        _fm_mean_abs_error) predicts each heat one step ahead from the corrected
        running estimate and reports the mean |residual|. A high value means the
        recent heat trajectory is poorly predicted by its own smooth dynamics
        (jumpy activation); ~0 means heat evolves predictably.

        Bounded by design (``sample_limit`` recent rows, one pass, no neighbour
        lookups) so it never triggers the whole-corpus scan the vitals docstring
        warns about. Returns ``{"sampled": <int>, "mean_error": <float>}``.
        Zero-filled on any error or a store predating heat_base, the same
        graceful-absence idiom as count_familiarity_resolvable /
        count_stress_modulation.

        Honesty note: this is the STANDING footprint of a MINIMAL, deterministic
        forward-model primitive flagged LOW AI PRIORITY in the gap analysis — a
        scalar predict→error→correct EMA over heat, NOT a learned cerebellar
        circuit and NOT the perceptual predictive-coding novelty scorer. It
        reports how self-predictable recent heat is, nothing more.
        """
        out: dict[str, Any] = {"sampled": 0, "mean_error": 0.0}
        try:
            rows = self._execute(
                "SELECT heat_base FROM memories "
                "WHERE heat_base IS NOT NULL "
                "ORDER BY created_at DESC LIMIT %s",
                (sample_limit,),
            ).fetchall()
            # rows come newest-first; reverse to oldest→newest so the forward
            # model replays the trajectory in the direction heat evolved.
            trajectory = [float(r["heat_base"]) for r in reversed(rows)]
            out["sampled"] = len(trajectory)
            out["mean_error"] = _fm_mean_abs_error(trajectory)
        except Exception:
            pass  # heat_base column absent / query failed — neutral (zeroed) case
        return out

    def list_procedural_skills(
        self, min_proficiency: float = 0.0, limit: int = 200
    ) -> list[dict[str, Any]]:
        """List procedural skills for the Board/Knowledge skill panel, best
        first. Returns an empty list when the table is absent."""
        try:
            rows = self._execute(
                "SELECT skill_id, action_sequence, context_signature, "
                "occurrences, success_count, failure_count, proficiency, "
                "is_habitual, last_seen "
                "FROM procedural_skills WHERE proficiency >= %s "
                "ORDER BY proficiency DESC, occurrences DESC LIMIT %s",
                (min_proficiency, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

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
                # heat_base is REAL (float4). The keyset boundary `last_heat`
                # round-trips through Python as float8; comparing it back
                # against the float4 column promotes inexactly, so inside a
                # large equal-heat cluster (e.g. the 38,932 rows at the
                # default heat 0.5009) the boundary row never satisfies `<`
                # and `last_id` freezes — the cursor re-reads the same page
                # forever (observed: 2.2M+ rows yielded from a 108k table).
                # Cast the boundary to ::real so the compare is float4 vs
                # float4 (exact). source: reproduced 2026-06-22, cluster at
                # heat_base=0.5009 size 38932.
                where = "heat_base >= %s AND (heat_base, id) < (%s::real, %s) "
                params = [min_heat, last_heat, last_id]
            # effective_heat AS heat gives each node its live decayed heat;
            # ordering/keyset stay on heat_base (the indexed physical key).
            sql = (
                f"SELECT {self._HEAT_EXPR} AS heat, {columns} "
                f"FROM memories m WHERE {where}{bench_filter}"
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
