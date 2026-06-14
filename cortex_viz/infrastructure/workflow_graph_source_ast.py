"""AST-backed loader for the workflow graph (ADR-0046).

Peer of ``workflow_graph_source_pg`` / ``workflow_graph_source_jsonl``.
Calls the ``automatised-pipeline`` MCP server via ``ap_bridge`` and
returns builder-shaped dicts for symbol nodes and the AST edges
(``defined_in``, ``calls``, ``imports``, ``member_of``).

Constrained to the Cortex-known file set: AP may have indexed files
that Cortex doesn't know about (e.g. vendored dependencies); we filter
so the graph stays focused on what the user's sessions actually touch.

Pure infrastructure — no core imports. When AP is disabled
(``CORTEX_MEMORY_AP_ENABLED=0``) or unreachable, every loader returns
``[]`` so the workflow graph degrades to the native in-process AST
source in ``workflow_graph_source_native_ast``.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator

from cortex_viz.infrastructure.ap_bridge import (
    APBridge,
    is_enabled,
    resolve_graph_path,
    resolve_graph_paths,
)

# Sync machinery, symbol constants, and the per-graph symbol/edge async
# batch loaders live in the sibling ``_async`` / ``_edges`` modules
# (split to respect the 500-line file limit). Re-exported here so every
# historical import path (``_SyncLoop``, ``_run``, ``_ap_sync_timeout_s``,
# ``_as_list``, ``_SYMBOL_LABELS``, ``_NON_QUALIFIED_LABELS``,
# ``_symbol_type_from_label``) keeps resolving.
from cortex_viz.infrastructure.workflow_graph_source_ast_async import (  # noqa: F401
    _NON_QUALIFIED_LABELS,
    _SYMBOL_LABELS,
    _SyncLoop,
    _ap_sync_timeout_s,
    _as_list,
    _run,
    _symbol_batches_async,
    _symbol_type_from_label,
)
from cortex_viz.infrastructure.workflow_graph_source_ast_edges import (  # noqa: F401
    _edge_batches_async,
)



class WorkflowGraphASTSource:
    """AST-layer loader. Construct once per graph build; the inner
    bridge caches its MCP connection across calls."""

    def __init__(self, bridge: APBridge | None = None) -> None:
        self._bridge = bridge or APBridge()
        self._loop_owner = _SyncLoop()

    def enabled(self) -> bool:
        return is_enabled()

    def close(self) -> None:
        """Close the underlying bridge + pinned loop. Idempotent."""
        try:
            self._loop_owner.run(self._bridge.close())
        except Exception:
            pass
        self._loop_owner.close()

    def iter_symbols(
        self,
        file_paths: Iterable[str],
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield AST symbol rows one per-query batch (one AP ``query_graph``
        per label per graph). Each yielded list is the result of a single
        AP roundtrip; the caller sees batch *N* before batch *N+1*'s query
        is issued, so the source-internal peak is one query's rows — never
        the union across all ~21 label queries × graphs.

        Row shape per item: ``{file_path, qualified_name, symbol_type,
        signature, language, line}``. ``domain`` is inferred downstream.
        A corrupt/missing graph is skipped without aborting the stream.
        """
        if not is_enabled():
            return
        graph_paths = resolve_graph_paths()
        if not graph_paths:
            return
        paths = [p for p in file_paths if p]
        yield from self._loop_owner.run_iter(
            self._iter_symbols_async(graph_paths, paths)
        )

    def load_symbols(
        self,
        file_paths: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Full-set convenience over ``iter_symbols``.

        Materializes every batch into one list for consumers that genuinely
        need the whole set (the workflow_graph builder iterates ``ast_symbols``
        once and the handler takes ``len(...)``). The streaming win is still
        real: ``iter_symbols`` bounds the *source*-internal peak to one query
        while this list is filled. Consumers that can iterate should call
        ``iter_symbols`` directly to avoid the final materialization.
        """
        out: list[dict[str, Any]] = []
        for batch in self.iter_symbols(file_paths):
            out.extend(batch)
        return out

    def iter_ast_edges(
        self,
        file_paths: Iterable[str],
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield CALLS / IMPORTS / MEMBER_OF / USES edge rows one per-query
        batch (one AP ``query_graph`` per rel-table per graph, ~89 queries).
        Same incremental contract as ``iter_symbols``: peak retained inside
        the source is one rel-table's rows, not the union of all 89.
        Empty ``file_paths`` means "no path filter"."""
        if not is_enabled():
            return
        graph_paths = resolve_graph_paths()
        if not graph_paths:
            return
        paths = [p for p in file_paths if p]
        yield from self._loop_owner.run_iter(self._iter_edges_async(graph_paths, paths))

    def load_ast_edges(
        self,
        file_paths: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Full-set convenience over ``iter_ast_edges`` (see ``load_symbols``
        for why the final list is kept: the builder + handler ``len(...)``
        genuinely need the whole edge set)."""
        out: list[dict[str, Any]] = []
        for batch in self.iter_ast_edges(file_paths):
            out.extend(batch)
        return out

    async def _iter_symbols_async(
        self,
        graph_paths: list[str],
        paths: list[str],
    ):
        """Async generator: one batch per (graph, label) AP query.

        A failed query for one (graph, label) is skipped — one bad graph or
        label never kills the whole stream, matching the prior swallow-and-
        continue contract.
        """
        for gp in graph_paths:
            try:
                async for batch in _symbol_batches_async(self._bridge, gp, paths):
                    if batch:
                        yield batch
            except Exception:
                # One corrupt / missing graph never kills the whole stream.
                continue

    async def _iter_edges_async(
        self,
        graph_paths: list[str],
        paths: list[str],
    ):
        """Async generator: one batch per (graph, rel-table) AP query."""
        for gp in graph_paths:
            try:
                async for batch in _edge_batches_async(self._bridge, gp, paths):
                    if batch:
                        yield batch
            except Exception:
                continue

    async def _load_symbols_async(
        self,
        graph_path: str,
        paths: list[str],
    ) -> list[dict[str, Any]]:
        """Full-set per-graph drain of ``_symbol_batches_async``.

        Kept list-returning because ``http_standalone_graph`` caches the
        per-project symbol list and reports ``len(syms)`` — a genuine
        full-set consumer (reported as needing-full-set in the C3 RCA).
        """
        out: list[dict[str, Any]] = []
        async for batch in _symbol_batches_async(self._bridge, graph_path, paths):
            out.extend(batch)
        return out

    def search_codebase(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Forward ``search_codebase`` to AP and normalize to a flat
        list of ``{id, qualified_name, file_path, score, snippet}``.

        Phase 3 (ADR-0046). When AP is disabled OR no graph_path is
        configured, returns ``[]`` so the unified-search fusion
        gracefully falls back to Cortex-only results.
        """
        if not is_enabled() or not query or not query.strip():
            return []
        gp = resolve_graph_path()
        if not gp:
            return []
        resp = self._loop_owner.run(
            self._bridge.search_codebase(gp, query, limit=int(limit))
        )
        out: list[dict[str, Any]] = []
        for r in _as_list(resp):
            qname = r.get("qualified_name") or r.get("name") or ""
            fpath = r.get("file_path") or r.get("abs_path") or ""
            if not qname:
                continue
            out.append(
                {
                    # Deterministic id so RRF fusion can dedupe with
                    # the same scheme used for SYMBOL graph nodes.
                    "id": f"symbol:{fpath}::{qname}",
                    "qualified_name": str(qname),
                    "file_path": str(fpath),
                    "score": float(r.get("score") or 0.0),
                    "snippet": r.get("snippet") or r.get("signature") or "",
                    "source": "ap",
                }
            )
        return out

    def verify_symbols(self, qualnames: list[str]) -> dict[str, bool]:
        """Return ``{qualname: exists_in_ap}`` for each candidate.

        Used by the wiki_verify handler (ADR-0046 Phase 2). Returns
        ``{qname: False}`` for every input when AP is disabled OR no
        graph_path is configured — the handler interprets that as
        'verification skipped', not as confirmed staleness.
        """
        if not is_enabled():
            return {q: False for q in qualnames}
        gp = resolve_graph_path()
        if not gp:
            return {q: False for q in qualnames}
        uniq = [q for q in dict.fromkeys(qualnames) if q]
        if not uniq:
            return {}
        return self._loop_owner.run(self._verify_symbols_async(gp, uniq))

    async def _verify_symbols_async(
        self,
        graph_path: str,
        qualnames: list[str],
    ) -> dict[str, bool]:
        """Batch verification across every AP symbol label.

        AP has no unified ``Symbol`` label — we iterate the known set
        (Function, Method, Struct, ...). Wiki references are usually
        bare names (``WorkflowGraphBuilder``), so we widen the match:
        a qualname counts as found if any AP symbol name equals it,
        its name equals the tail, or the qualified_name endswith the
        tail (``::tail`` or ``.tail``).
        """
        out: dict[str, bool] = {q: False for q in qualnames}
        all_names: list[str] = []
        all_short: list[str] = []
        for label in _SYMBOL_LABELS:
            query = (
                f"MATCH (s:{label}) "
                "RETURN DISTINCT s.qualified_name AS qualified_name, "
                "                s.name           AS name"
            )
            rows = await self._bridge.call(
                "query_graph",
                {"graph_path": graph_path, "query": query},
            )
            for r in _as_list(rows):
                qn = str(r.get("qualified_name") or "")
                nm = str(r.get("name") or "")
                if qn:
                    all_names.append(qn)
                if nm:
                    all_short.append(nm)
        for q in qualnames:
            tail = q.rsplit(".", 1)[-1]
            if tail in all_short:
                out[q] = True
                continue
            for qn in all_names:
                if qn == q or qn.endswith(f"::{tail}") or qn.endswith(f".{tail}"):
                    out[q] = True
                    break
        return out

    async def _load_edges_async(
        self,
        graph_path: str,
        paths: list[str],
    ) -> list[dict[str, Any]]:
        """Full-set per-graph drain of ``_edge_batches_async``.

        Kept list-returning for ``http_standalone_graph`` (caches the
        per-project edge list, reports ``len(edgs)``) — a genuine full-set
        consumer (reported as needing-full-set in the C3 RCA).
        """
        out: list[dict[str, Any]] = []
        async for batch in _edge_batches_async(self._bridge, graph_path, paths):
            out.extend(batch)
        return out


__all__ = ["WorkflowGraphASTSource"]
