"""Cross-loop AP-bridge sync machinery for the AST workflow-graph source.

Split out of ``workflow_graph_source_ast.py`` (was 917 lines) to respect
the 500-line file limit. Holds the loop-owner machinery that exposes a
synchronous façade over the async ``ap_bridge`` MCP calls:

  * ``_ap_sync_timeout_s`` — cross-loop wait ceiling (env-driven).
  * ``_run``              — legacy fresh-loop sync wrapper.
  * ``_SyncLoop``         — pinned single-loop owner + streaming primitive.

Pure infrastructure — no core imports. ``workflow_graph_source_ast``
re-exports these symbols so existing import paths keep resolving.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING, Any, Iterator

from cortex_viz.errors import McpConnectionError

if TYPE_CHECKING:
    from cortex_viz.infrastructure.ap_bridge import APBridge

def _ap_sync_timeout_s() -> float:
    """Cross-loop wait ceiling for AP reader-thread calls.

    source: memory_config.AP_SYNC_RESULT_TIMEOUT_S (see that field's
    derivation comment — floored at the in-loop 3600 s AP-call ceiling
    plus a drain margin). Read lazily so env overrides apply per-process.
    """
    from cortex_viz.infrastructure.memory_config import get_memory_settings

    return float(get_memory_settings().AP_SYNC_RESULT_TIMEOUT_S)


def _run(coro):
    """Legacy sync wrapper — each call creates a fresh loop. Retained
    for callers that only need one roundtrip per process.

    The AST source itself avoids this for multi-call flows: the subprocess
    streams (``asyncio.subprocess``) are bound to whichever loop created
    them, and a second ``asyncio.run`` invalidates them. The class uses
    ``_SyncLoop`` to pin one loop across all its calls.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Lamport H4: an untimed cross-loop .result() hangs this caller
            # forever if the loop thread wedges. Bound every cross-loop wait.
            try:
                return asyncio.run_coroutine_threadsafe(coro, loop).result(
                    timeout=_ap_sync_timeout_s()
                )
            except FutureTimeoutError as exc:
                raise McpConnectionError(
                    "AP cross-loop call exceeded "
                    f"{_ap_sync_timeout_s():.0f}s — subprocess presumed wedged"
                ) from exc
    except RuntimeError:
        pass
    return asyncio.run(coro)


class _SyncLoop:
    """Owns a single event loop + runs coroutines on it synchronously.

    The MCP client spawns the AP subprocess and binds its stdin/stdout
    to the *current* event loop. If we close that loop between calls,
    subsequent writes to those streams raise ``RuntimeError: Event loop
    is closed``. This helper pins one loop for the lifetime of a caller
    so every AP call shares the same loop/transport.

    When called from *inside* a running event loop (e.g. a FastMCP
    async handler), we run the coroutine on the private loop inside a
    dedicated thread so we never compete with the outer loop. That is
    the only reliable way to expose a sync façade to async callers
    without leaking thread-local state.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            import threading

            def _run_forever():
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()

            self._thread = threading.Thread(
                target=_run_forever,
                name="ap-sync-loop",
                daemon=True,
            )
            self._thread.start()
        return self._loop

    def run(self, coro):
        """Run ``coro`` on the pinned loop and block until it completes.

        Single-reader-thread ownership (verified): ``_ensure_loop`` spawns
        exactly one ``ap-sync-loop`` thread that owns the loop for this
        ``_SyncLoop``'s lifetime; every AP call funnels through here onto
        that one loop. No other thread drives the loop, so the JSON-RPC
        pipe has a single reader (Lamport H4 satisfied by construction).

        The wait is bounded: if the loop thread wedges (e.g. the AP
        subprocess stalls below the in-loop await), ``.result(timeout=…)``
        raises rather than hanging this worker forever. On timeout we never
        return partial data — we raise ``McpConnectionError``.
        """
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=_ap_sync_timeout_s())
        except FutureTimeoutError as exc:
            future.cancel()
            raise McpConnectionError(
                "AP reader-thread call exceeded "
                f"{_ap_sync_timeout_s():.0f}s — subprocess presumed wedged"
            ) from exc

    def run_iter(self, agen) -> Iterator[Any]:
        """Drive an async generator one step per bounded cross-loop call,
        yielding each item synchronously to the caller.

        This is the streaming primitive: ``agen`` (an async generator that
        yields one batch per AP query) is advanced one ``__anext__`` at a
        time, each on the pinned loop with a bounded ``.result(timeout=…)``.
        The caller therefore receives batch *N* (and may process/discard it)
        BEFORE batch *N+1*'s query is ever issued — peak retained inside the
        source is one batch, not the union across all queries.

        On a wedged loop thread, each step raises ``McpConnectionError``
        rather than hanging. Partial batches already yielded are real data;
        the generator stops at the failed step (it does not silently return
        a truncated full list).
        """
        loop = self._ensure_loop()
        _SENTINEL = object()

        async def _step():
            try:
                return await agen.__anext__()
            except StopAsyncIteration:
                return _SENTINEL

        while True:
            future = asyncio.run_coroutine_threadsafe(_step(), loop)
            try:
                item = future.result(timeout=_ap_sync_timeout_s())
            except FutureTimeoutError as exc:
                future.cancel()
                raise McpConnectionError(
                    "AP reader-thread step exceeded "
                    f"{_ap_sync_timeout_s():.0f}s — subprocess presumed wedged"
                ) from exc
            if item is _SENTINEL:
                return
            yield item

    def close(self) -> None:
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
            try:
                if self._thread is not None:
                    self._thread.join(timeout=2.0)
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass
        self._loop = None
        self._thread = None


# ── Symbol/edge constants + per-graph async batch loaders ──────────────
# Moved from workflow_graph_source_ast.py (split for the 500-line limit).
# Re-exported there so existing import paths keep resolving.


def _as_list(payload: Any) -> list[dict]:
    """Normalise AP's ``query_graph`` response into a list of dicts.

    AP's Stage-3a query_graph returns the shape:
        {
          "columns": ["a", "b"],
          "rows":    [["1", "2"], ["3", "4"]],
          "status":  "ok",
          ...
        }

    We zip ``columns`` with each row to produce ``[{"a": "1", "b": "2"}, ...]``.
    Error responses (``status: "error"``) surface as an empty list — the
    caller is already resilient to that case. Plain lists and dicts with a
    ``rows`` key containing dicts are also accepted for forward-compat.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    if payload.get("status") == "error":
        return []
    cols = payload.get("columns")
    rows = payload.get("rows")
    if isinstance(cols, list) and isinstance(rows, list):
        out: list[dict] = []
        for row in rows:
            if isinstance(row, list) and len(row) == len(cols):
                out.append({str(c): row[i] for i, c in enumerate(cols)})
            elif isinstance(row, dict):
                out.append(row)
        return out
    # Older ``{"content": [...]}`` / ``{"data": [...]}`` shapes.
    inner = payload.get("content") or payload.get("data")
    if isinstance(inner, list):
        if inner and isinstance(inner[0], dict) and inner[0].get("type") == "text":
            try:
                parsed = json.loads(inner[0].get("text") or "")
                if isinstance(parsed, list):
                    return [r for r in parsed if isinstance(r, dict)]
            except ValueError:
                return []
        return [r for r in inner if isinstance(r, dict)]
    return []


# AP's node labels carrying symbol semantics. Derived from
# stage-3 tree-sitter extractors; see
# ``automatised-pipeline/src/clustering.rs`` for the canonical list.
_SYMBOL_LABELS = (
    # Core — Rust + Python (original set)
    "Function",
    "Method",
    "Struct",
    "Enum",
    "Trait",
    "Constant",
    "TypeAlias",
    # JVM family — Java, Kotlin
    "Class",
    "Interface",
    "Field",
    "Property",
    # Swift / ObjC family
    "Protocol",
    "Extension",
    # C / C++
    "Union",
    "Typedef",
    "Macro",
    # Go / general
    "Module",
    "Package",
    "Namespace",
    "Variable",
    # Import statements (one node per ``import`` site). AP wires every
    # file to its imports via the ``Defines_File_Import`` rel table; the
    # nodes themselves carry ``id`` (``<file>::<modpath>``), ``path``,
    # ``alias``, ``is_glob``. Loaded via a custom property mapping in
    # ``_load_symbols_async`` because imports lack ``qualified_name``.
    "Import",
)

# Labels whose nodes don't expose ``qualified_name`` / ``name``. The
# load query falls back to ``id`` / ``path`` (or whatever the node
# DOES carry) so they still flow into the graph.
_NON_QUALIFIED_LABELS = {"Import"}


def _symbol_type_from_label(label: str) -> str:
    """Map AP's label → workflow-graph symbol_type.

    Keeps the value set small so the palette (``SYMBOL_COLORS``) stays
    compact. Every AP label from every supported language collapses
    into one of: function · method · class · module · constant.
    """
    low = label.lower()
    if low == "function":
        return "function"
    if low == "method":
        return "method"
    # All type-like constructs → class. Covers Rust (struct/enum/trait),
    # Java/Kotlin (class/interface), Swift/ObjC (protocol/extension),
    # C/C++ (union).
    if low in (
        "struct",
        "enum",
        "trait",
        "class",
        "interface",
        "protocol",
        "extension",
        "union",
    ):
        return "class"
    # Module-ish containers → module (amber).
    if low in ("module", "package", "namespace"):
        return "module"
    # Value-ish / alias-ish → constant (slate).
    if low in (
        "constant",
        "typealias",
        "typedef",
        "macro",
        "field",
        "property",
        "variable",
    ):
        return "constant"
    return low


def _repo_relative_for_match(p: str) -> str:
    """Resolve an absolute file path to its git-repo-relative form — the exact
    prefix AP stores in ``qualified_name`` (``<repo-rel>::<name>``) and in
    ``File.id``. A bare relative path is returned cleaned; on any failure
    returns "" so the caller falls back to the basename. Mirrors
    ``server.trace_impact._to_repo_relative`` so the file-detail AST resolves
    the SAME symbols the impact view already does."""
    p = (p or "").replace("\\", "/")
    if not p.startswith("/"):
        return p.lstrip("./")
    import os
    import subprocess
    from pathlib import Path

    try:
        real = os.path.realpath(p)
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(Path(real).parent),
            timeout=5,
        )
        root = res.stdout.strip() if res.returncode == 0 else ""
        if root:
            return str(Path(real).relative_to(root))
    except Exception:
        return ""
    return ""


async def _symbol_batches_async(
    bridge: "APBridge",
    graph_path: str,
    paths: list[str],
):
    """Yield one batch of symbol rows per AP label query (async gen).

    AP stores each symbol under its own label (Function, Method,
    Struct, Enum, Trait, Constant, TypeAlias). The qualified_name
    follows ``<relative_file>::<name>``. We query each label
    separately (LadybugDB rejects multi-label ``MATCH``). Each label's
    rows are yielded as soon as its query returns, so the consumer can
    process/discard a label's rows before the next label is queried.

    ``paths`` entries may be absolute (builder convention); AP's
    ``File.id`` and the symbol ``qualified_name`` prefix are
    repo-relative. We match by ``endswith`` so both forms work.
    """
    # Build a set of basenames and tail fragments for fast matching.
    # These are also used to construct server-side WHERE predicates so
    # Kuzu filters by file prefix rather than returning ALL symbols and
    # discarding in Python. Previously the code used a blanket
    # ``LIMIT 500`` without a WHERE clause — on a 50k-symbol codebase
    # alphabetically-early files consume the entire limit and the
    # desired file's symbols are never returned.
    # source: measured 2026-06-04 — query for consolidate.py returned 0
    #   because the first 500 Functions all start with benchmarks/* or
    #   _pipeline/*; mcp_server/handlers/* never appeared.
    path_tails: set[str] = set()
    for p in paths:
        if not p:
            continue
        # AP keys symbols by REPO-RELATIVE ``qualified_name`` (``<rel>::<name>``).
        # The absolute path is NEVER a valid STARTS WITH prefix — and, being
        # the longest candidate, it used to win the longest-first dedup below
        # and crowd out the real tail, so every file returned 0 symbols even
        # though the impact view (which resolves repo-relative first) found
        # them. Resolve to the git-repo-relative form here too; add the
        # basename as a fallback for non-git files. NEVER add the abs path.
        rel = _repo_relative_for_match(p)
        if rel:
            path_tails.add(rel)
        base = p.rsplit("/", 1)[-1]
        if base:
            path_tails.add(base)

    # Build a Cypher WHERE predicate that filters at the Kuzu level.
    # Each tail produces one STARTS WITH predicate on qualified_name
    # (or id for Import nodes). We emit the shortest unique tails only
    # — if "pkg/mod.py" is present, "mod.py" is redundant because any
    # match for "mod.py" also matches "pkg/mod.py". Cap at 10 tails
    # to keep the WHERE clause tractable.
    def _where_for_tails(prop: str, tails: set[str]) -> str:
        if not tails:
            return ""
        # Sort longest-first so shorter redundant tails are skipped.
        sorted_tails = sorted(tails, key=len, reverse=True)
        kept: list[str] = []
        for t in sorted_tails:
            if any(t == k or k.endswith(t) for k in kept):
                continue  # already covered by a longer tail
            kept.append(t)
            if len(kept) >= 10:
                break
        escaped = [t.replace("'", "\\'") for t in kept]
        preds = " OR ".join(f"{prop} STARTS WITH '{t}::'" for t in escaped)
        return f" WHERE {preds}"

    for label in _SYMBOL_LABELS:
        # Import nodes don't carry qualified_name / name — they use
        # ``id`` (``<file>::<modpath>``) and ``path`` (the imported
        # module). Use those as the qualified_name / name surrogate.
        if label in _NON_QUALIFIED_LABELS:
            prop = "s.id"
            select = (
                f"MATCH (s:{label})"
                "{where}"
                " RETURN s.id   AS qualified_name,"
                "        s.path AS name"
            )
        else:
            prop = "s.qualified_name"
            select = (
                f"MATCH (s:{label})"
                "{where}"
                " RETURN s.qualified_name AS qualified_name,"
                "        s.name           AS name"
            )
        if paths:
            where = _where_for_tails(prop, path_tails)
            query = select.format(where=where)
        else:
            # Load-all mode: no filter, no limit — pull the full graph.
            query = select.format(where="")
        rows = await bridge.call(
            "query_graph",
            {"graph_path": graph_path, "query": query},
        )
        # Per-label batch: built, yielded, then dropped before the next
        # label's query runs — peak retained inside the source is one
        # label's rows, not the union across all _SYMBOL_LABELS queries.
        batch: list[dict[str, Any]] = []
        for r in _as_list(rows):
            qn = r.get("qualified_name")
            if not qn:
                continue
            qn_s = str(qn)
            file_part, sep, _ = qn_s.partition("::")
            if not sep:
                continue
            # Python-side match as a secondary safeguard (the WHERE
            # clause is the primary filter; this handles edge cases
            # where a shorter tail matched a different file).
            if path_tails and not any(
                p == file_part or p.endswith(file_part) or file_part.endswith(p)
                for p in path_tails
            ):
                continue
            # Resolve file_path back to the absolute form if possible.
            abs_match = next(
                (p for p in paths if p.endswith(file_part)),
                file_part,
            )
            batch.append(
                {
                    "file_path": abs_match,
                    "qualified_name": qn_s,
                    "symbol_type": _symbol_type_from_label(label),
                    "signature": None,
                    "language": None,
                    "line": None,
                }
            )
        if batch:
            yield batch
