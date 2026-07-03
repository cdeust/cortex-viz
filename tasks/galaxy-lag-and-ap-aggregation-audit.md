# Galaxy graph — lag diagnosis + AP aggregation/read audit

Scope: `cortex-viz` (extracted viz MCP). Reviewed the live build→serve→render
path and the automatised-pipeline (AP) read path. Two questions:
1. Why does the galaxy keep being laggy?
2. Is the AP data aggregation + reading correct?

Method: static read of the hot path + two reproductions (`_graph_signature`
against the real LadybugDB on-disk layout; an `apply_delta` vs in-place-append
microbenchmark at the code's own cited totals).

---

## Part 1 — Why the galaxy is laggy

Lag has THREE independent sources, on two sides of the process boundary. They
stack, which is why prior single-fix attempts only moved the freeze around.

### FINDING A (server, primary) — `apply_delta` copies the whole graph on every L6 delta → O(N²)

`cortex_viz/server/graph_appliers.py:119-148` (`apply_delta`), lines 121-122:

```python
with state._apply_lock:
    old = state._graph_cache["data"] if state._graph_cache else None
    new_nodes = list(old["nodes"]) if old else []   # full shallow copy
    new_edges = list(old["edges"]) if old else []   # full shallow copy
    ...
    state._graph_cache = {"data": cur, "domain_filter": None}
```

This is the PRODUCTION cross-process path. The build runs in a child process
(`build_process._worker`), which forwards every non-baseline L6 delta over the
queue (`graph_build_merge.py:151-159`, gated on `_SINK_Q is not None and stage
!= "baseline"`); the server's drain thread replays each one through
`apply_delta` (`build_process.py:222`). L6 streams ~200 nodes per batch, so at
the cited ~200k-node / 480k-edge galaxy the drain re-copies the entire
accumulated node+edge list on each of ~1,000 deltas.

Reproduction (this session, at the code's own cited totals — 200k nodes /
480k edges / 200-node batches):

| path | element copies | wall | notes |
|---|---|---|---|
| `apply_delta` (server drain) | 339,660,000 | 3.11 s | pure list-copy CPU, in the drain thread |
| in-place append | 0 | 0.04 s | what the in-process merge already does |

~87× slower, copies grow as **N²/BATCH ≈ 0.34 billion** for one build. That
CPU burns in the server process's drain thread; under the Python GIL it
directly starves the HTTP-server thread, so `/api/graph/slice`,
`/api/graph/node` and every static asset stall for seconds at a time while L6
streams. It also churns ~0.3 GB of transient lists → GC pressure.

The irony: the IN-PROCESS merge (`graph_build_merge.py:56-84`) was already
fixed to be O(batch) — it appends into `cur["nodes"]` in place with persistent
`seen_n`/`seen_e` dedup sets, and its own comment (lines 33-40) documents that
rebuilding per batch "pinned the GIL for hours." **`apply_delta` is the server
mirror of that same merge and never got the same fix** — it reintroduces the
exact O(N²) copy on the drain side. `apply_graph_replace` (line 222) is O(N)
and correct; only the incremental `apply_delta` is pathological.

Fix (root cause, mirrors the in-process merge): keep the server cache lists
mutable and append in place under `_apply_lock` instead of copy-swapping.
`get_graph_slice` (line 295) already reads `state._graph_cache` via a single
atomic reference read and slices a window, so it tolerates in-place growth as
well as it tolerates reference-swap. Maintain `_applied_node_ids` (already
present) for dedup; drop the `list(old[...])` clones.

### FINDING B (client, primary) — every live delta destroys and re-mounts the ENTIRE force simulation

`ui/unified/js/workflow_graph_bridge.js`:
- `render()` at lines 84-114 does `_handle.destroy()` then
  `JUG.renderWorkflowGraph(wrapper, data)` — a full teardown + re-mount that
  re-runs `mount()` (`workflow_graph.js:216+`): re-maps every node, re-strips
  coords, re-seeds slot positions for all N, and starts a fresh
  `d3.forceSimulation` over the whole graph.
- The `state:lastData` handler (lines 128-159) fires on every SSE append and
  schedules `render(d)` — i.e. a whole-graph re-mount, not an incremental add.

The append handle EXISTS but is never used. `workflow_graph.js:401` implements
a real incremental `append(newNodes, newEdges)` that mutates the live
`ctx.nodes`/`ctx.edges` arrays (canvas reads them each frame — no re-layout),
it's returned at line 571, wired at line 194 (`handle.append = impl.append`)…
and the bridge calls `render()` (destroy+remount) instead of `_handle.append()`
on every delta. The 8 s debounce for `>15000` nodes (line 140) is a
band-aid over this: it just makes the full re-shuffle happen less often. On a
64k+ node galaxy, each re-mount re-seeds and re-simulates every node — that is
the visible "galaxy re-shuffles / freezes when something streams in."

Fix: on `state:lastData` deltas, call `_handle.append(delta.nodes, delta.edges)`
(the delta payload is already carried on the event — `graph.js:290-294`) and
reserve the destroy+remount `render()` for the first paint and view switches
only.

### FINDING C (client, secondary but severe) — `addBatchToGraph` full rebuild + `CANVAS_THRESHOLD = 0`

- `ui/unified/js/graph.js:90-107` (`addBatchToGraph`) concatenates into
  `lastData` then calls `buildGraph(lastData)` — a full O(N) filter+rebuild of
  the legacy force-graph — on every discussion batch. `appendGraphDelta`
  (line 224) guards this with `JUG.__wfgActive` so it's suppressed once the D3
  renderer is active, but `addBatchToGraph` (used by the discussion loader,
  `polling.js:262`) has no such guard and rebuilds unconditionally.
- `workflow_graph.js:18` sets `CANVAS_THRESHOLD = 0`, so rendering is always
  canvas (intentional — SVG can't grow incrementally). That's correct, but it
  means Finding B's re-mounts always pay the canvas re-seed cost; the two
  compound.

**FIXED:** `addBatchToGraph` now delegates to `appendGraphDelta`, inheriting
its dedup, in-place O(batch) growth, `!__wfgActive` rebuild guard, and the
`state:lastData` delta the bridge appends incrementally. `CANVAS_THRESHOLD`
is unchanged (canvas-always is the intended design). See status list below.

---

## Part 2 — AP data aggregation + reading

The read/aggregation LOGIC is sound and was clearly hardened over several
passes. Aggregation conserves rows, the streaming contract bounds peak memory,
and the Cypher widening fixed real under-counting. **One correctness bug
remains, plus two lower-severity items.**

### FINDING D (correctness bug) — the L6 AST disk cache signature is a constant → cache never invalidates

`cortex_viz/server/graph_build_l6.py:91-108` (`_graph_signature`) walks
`root.rglob("*")` over the graph path to build a change-detection hash. But
AP's LadybugDB graph is a **single file** named `graph` (with a `graph.wal`
sibling) — `ap_bridge.resolve_graph_paths` documents this explicitly:
*"AP's LadybugDB is a single `graph` file with a `graph.wal` sibling, NOT a
directory."* `Path("…/graph").rglob("*")` on a file yields `[]`, so the hash
digests nothing.

Reproduction (this session, real single-file layout + a re-index):

```
graph path is a FILE: True
rglob('*') on a file yields: []
signature before re-index: e3b0c44298fc1c14
signature after  re-index: e3b0c44298fc1c14   # unchanged after content+mtime change
sha256(empty)[:16]      : e3b0c44298fc1c14   # == the empty-hash constant
```

Consequences:
- The signature is the SHA-256-of-empty constant for every project, so
  `_cache_load` treats a re-indexed graph as unchanged and serves **stale
  symbols/edges** until the JSON cache file is manually deleted. A re-index
  (the whole point of AP change-detection) is invisible to the viz.
- Worse for freshness than for lag, but it also interacts with lag: because
  the signature never varies, `_cache_store` writes a cache that is only ever
  invalidated by the `.wal` sibling — never the graph body.

Fix: hash the `graph` file itself plus its `graph.wal` sibling by
`(size, mtime)` (and, if `gp_` ever is a directory, keep the rglob branch).
Something like: stat `root`, and `root.with_suffix('.wal')` /
`root.parent/(root.name + '.wal')`, digest their `(st_size, int(st_mtime))`.
Then a re-index (which rewrites both) changes the signature and the cache
refetches.

### FINDING E (robustness) — over-enumeration of rel tables is ~89 round-trips/project, unbounded symbol load

`workflow_graph_source_ast_edges.py` enumerates the full Cartesian product of
label pairs (`calls_rels` 3×3, `imports_rels` File×~21, `member_rels` 8×5,
`uses` 2×9, plus `Defines_File_Import`) — ~89 `query_graph` calls per graph,
most against rel tables that don't exist (AP returns empty). The comment
acknowledges this is deliberate ("over-enumeration is safe — it just costs
extra round-trips"). It's correct but it's the reason a cold L6 (cache miss)
is slow: 89 serialized MCP round-trips per project, each bounded only by the
180 s `_L6_PROJECT_TIMEOUT_S`. This is not the interactive-lag cause (L6 runs
in the child), but it lengthens the "building… 30%" window the user sees.
Aggregation itself (`lod_aggregator.aggregate`) is a correct single
O(N·levels) pass and conserves rows — no issue there.

**FIXED.** Correction to the original note above: AP's `query_graph` guard
FORBIDS the `CALL` keyword (`automatised-pipeline` `src/main.rs`
`FORBIDDEN_CYPHER_KEYWORDS`), so the engine's catalog (`CALL show_tables()`) is
NOT reachable at runtime — the table set cannot be discovered dynamically. The
authoritative list is AP's static `REL_TABLES` in `src/graph_store.rs`.
Transcribed exactly the tables the viz's prior enumeration returned rows for
into an explicit `_AP_REL_TABLES` constant (24 entries) and replaced the
Cartesian product with a single loop over it. Of the prior ~89 queries only 24
hit a table that AP's schema actually creates; the other 65 were queries
against label pairs that can never exist (`HasMethod_Class_Field`,
`Uses_Method_Interface`, `Calls_Macro_Macro`, … — AP has no
Class/Interface/Protocol node labels and no Macro-call table). Trimming to the
24 real tables is behaviour-preserving for loaded edges (AP returned empty for
the 65 anyway) while cutting cold-L6 round-trips by ~73%. `has_provenance`
flags were set per AP's schema (`is_resolution_rel` vs
`is_structural_provenance_rel`) and kept identical to the prior behaviour.
Verified: exactly 24 queries, every one exists in AP `REL_TABLES`, no forbidden
keyword leaks, provenance flags match the schema, one batch yielded per query;
90/90 tests pass.

Follow-up (NOT in this change): AP's schema defines ~29 more real edge tables
the viz does not yet load (`Imports_File_File`, `References_File_File`,
`HasField_*`, `HasVariant_*`, `Defines_File_<symbol>`, `Imports_Module_*`,
`Uses_Struct_*`/`Uses_Field_*`, `Calls_*_StdlibSymbol`, `Calls_CallSite_*`).
Adding them widens the graph, but each has an endpoint/column subtlety
(File/Module carry `id` not `qualified_name`; Variant/StdlibSymbol aren't in
`_SYMBOL_LABELS`), so they need their own load-path handling rather than being
folded into this trim.

### FINDING F (minor) — `resolution_method` quote-stripping is a documented AP-side workaround

`workflow_graph_source_ast_edges.py` strips literal single quotes from
`r.reason` because AP's `resolver.rs` formats the value as `format!("'{method}'")`.
The strip is correct and self-documented ("Remove this strip once AP fixes the
upstream quoting"). No action needed in viz; track upstream in AP.

---

## Part 3 — Server won't start ("visualization not running")

Reported 2026-07-02: `open_visualization` does not bring the galaxy up. Root-
caused by spawning `http_standalone.py` directly and reading stderr. `main()`
runs a sequence of steps BEFORE `_bind_server()` binds `127.0.0.1:3458`; any
exception in that prelude kills the process before it can serve even the UI
skeleton — which presents to the user simply as "nothing opened". Two distinct
pre-bind crashes, both the same anti-pattern (a best-effort step with no guard
sitting on the critical startup path):

### FINDING G (packaging, blocker) — psycopg is required but only an optional extra

`main()` → `_get_store()` → `MemoryReader`, which does `import psycopg` at
module top (`memory_read.py:41`, no SQLite fallback). But `psycopg[binary]`,
`psycopg-pool`, `pgvector` were declared ONLY under the optional `[data]` extra
in `pyproject.toml`, not core `dependencies`. Any install that didn't request
`cortex-viz[data]` spawns a server that dies at import with
`ModuleNotFoundError: No module named 'psycopg'` — before `_bind_server`.
(A *down* DB does NOT cause this: `MemoryReader.__init__` opens its pools
lazily, so with the driver present the socket binds and the skeleton serves,
degrading to "loading memories" only when a query is actually issued.)

**FIXED:** promoted the three read-path drivers from the `[data]` extra into
core `dependencies` (the server cannot run without them); kept `[data]` as a
back-compat alias so existing `cortex-viz[data]` commands/lockfiles still
resolve. Reproduced the crash, then confirmed startup advances past
`_get_store()` once the drivers are installed.

### FINDING H (robustness, blocker) — graph-path discovery aborts startup on an unstattable path

`ensure_build_started(store)` (also pre-bind) → `_roster_fingerprint()` →
`resolve_graph_paths()` in `ap_bridge.py`. That function probes filesystem
candidates with `Path.exists()`, `Path.is_dir()`, `Path.iterdir()` — all of
which RAISE (`PermissionError`/`OSError`) on a stat failure rather than
returning `False`/empty. A single unreadable candidate (e.g.
`~/.cortex/ap_graph/graph` or the `~/.cortex/ap_graphs` roster) therefore
aborts the whole server before bind. This contradicts the build's own explicit
"degrade gracefully" contract (there is a `degraded` progress phase for exactly
the no-graph case).

**FIXED:** wrapped the `exists()` check in `_add` and the `is_dir()`/`iterdir()`
roster walk in `try/except OSError` — an unstattable candidate is treated as
absent and contributes no graphs, never aborting discovery. Verified:
`resolve_graph_paths()` returns `[]` (not an exception) when every path is
unstattable, and works normally otherwise; with G+H fixed, startup advances all
the way to `_bind_server()` (the only remaining failure in-sandbox is the
sandbox blocking `socket.bind`, which does not occur on the user's machine).

Note: `ap_bridge.py` is 477 lines — over the project's 300-line hard limit.
This is PRE-EXISTING (H added ~20 lines to an already-oversized file). Flagged
for a separate split; not bundled into this startup fix.

---

## Priority / status

1. **FINDING A** — [DONE] `apply_delta` now appends fresh nodes/edges in place
   into the existing cache list objects under `_apply_lock` (re-aliasing
   `links` to `edges`, updating `meta` counts), mirroring the in-process merge.
   `begin_epoch` / `apply_graph_replace` still swap the reference. Measured on
   the real function: ~0.44 s / 0.44 ms-per-delta flat over a 200k-node/480k-edge
   1000-delta build, vs ~3.1 s growing before. Verified: dedup, links-alias,
   meta counts, `_node_index`, `_adjacency`, slice-pagination union,
   replace-reset; 26/26 graph-builder tests pass.
2. **FINDING B** — [DONE] the bridge's `state:lastData` handler now takes an
   incremental fast-path: when the live handle already renders this view and
   the event carries a `delta`, it calls `_handle.append(delta.nodes,
   delta.edges)` and returns, instead of destroy()+re-mount. Wholesale
   reference swaps (trace clear, wiki — no `delta`) and first-paint / view
   switches still take the debounced remount. View identity gated by
   `_viewOf()` (`workflow_graph.v1` vs `trace.v1`). `node --check` clean.
3. **FINDING D** — [DONE] `_graph_signature` now hashes the `graph` file + its
   `.wal` sibling by `(name, size, mtime)` when `gp_` is a file (the real
   LadybugDB layout), keeping the `rglob` walk only for the defensive
   directory case. A re-index (graph body or WAL) now moves the signature, so
   the AST disk cache invalidates and refetches instead of serving the
   empty-hash constant forever. Verified: signature ≠ empty-const, changes on
   graph-body re-index, changes on WAL-only change, stable across identical
   reads, `""` on missing path, directory fallback intact.
4. **FINDING C** — [DONE] `addBatchToGraph` (the `/api/discussions` batch
   loader path) now delegates to `appendGraphDelta` instead of reimplementing
   the merge. The old body `.concat`ed brand-new node/edge arrays (O(N) per
   batch, and replaced the array objects the canvas/bridge referenced) then
   called `buildGraph()` UNCONDITIONALLY — no `__wfgActive` guard, so every
   discussion batch rebuilt the hidden legacy force-graph on top of the active
   D3 renderer. Routing through `appendGraphDelta` gives dedup, in-place
   O(batch) growth (same arrays), the `!__wfgActive` rebuild guard, and the
   `state:lastData` delta the bridge appends incrementally (Fix B). Verified in
   a JUG harness: dedup, same-array in-place, zero legacy rebuilds under
   `__wfgActive`, correct incremental delta, rebuild resumes when the legacy
   renderer is active.
5. **FINDING E** — [DONE] replaced the Cartesian-product rel-table enumeration
   (~89 `query_graph` calls, 65 against tables that can never exist) with an
   explicit `_AP_REL_TABLES` list of the 24 tables AP's schema actually
   creates, transcribed from AP's static `REL_TABLES` (the engine forbids
   `CALL`, so the catalog can't be read at runtime). Behaviour-preserving for
   loaded edges; ~73% fewer cold-L6 round-trips. Verified: 24 queries, all real
   per AP `REL_TABLES`, provenance flags match the schema, no forbidden
   keyword; 90/90 tests pass. A ~29-table widening is captured as a follow-up.

6. **FINDING G** — [DONE] promoted `psycopg[binary]`, `psycopg-pool`, `pgvector`
   from the optional `[data]` extra into core `dependencies` (the standalone
   server imports psycopg before it binds; without the driver it crashes at
   startup and nothing opens). `[data]` kept as a back-compat alias. Reproduced
   the crash and confirmed startup advances past `_get_store()` with the driver
   present.
7. **FINDING H** — [DONE] `resolve_graph_paths()` (`ap_bridge.py`, on the
   pre-bind startup path) now wraps `exists()`/`is_dir()`/`iterdir()` in
   `try/except OSError`, so an unstattable graph candidate is treated as absent
   instead of aborting the whole server before it binds. Verified: returns `[]`
   under total stat-failure, normal otherwise; startup then reaches
   `_bind_server`. (`ap_bridge.py` is now 477 lines — pre-existing 300-line
   overflow, flagged for a separate split.)

Findings A, B, C, D, E, G, H all landed independently; each is a root-cause
fix, not a throttle. A–E address lag; G and H are startup blockers that were
found while investigating "the visualization won't open" and share one
anti-pattern — a best-effort step (store import, graph discovery) on the
critical pre-bind path with no guard. C is a natural companion to B: both
funnel every live growth through the single `appendGraphDelta` →
incremental-append path. The existing 8 s big-graph debounce and memory caps
are now band-aids over the fixed B/C and can be relaxed. Only Finding F remains
(an AP-upstream quoting workaround, no viz action).
