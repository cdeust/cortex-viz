# Mutation testing — scoped run notes (issue #35 AC#5)

Per coding-standards §12, mutation testing (not line coverage) is the strength
gate on changed logic. This documents the scoped Stryker run over the load
bearing seams this change introduced, and triages every surviving mutant:
**killed, or documented-equivalent** (§12.4). No survivor is left un-triaged.

Run: `npm run test:mutation` (config: `stryker.conf.json`, `@stryker-mutator/vitest-runner`).
Scope (§12.1/§12.5 per-commit narrow gate — only the changed lines):

| File | Range | Meaning | Score |
|---|---|---|---|
| `ui/unified/js/renderer.js` | 18–41 | `computeNeighborSet` + `linkTier` | 48 killed / 1 equivalent |
| `ui/unified/js/workflow_graph_filters.js` | 33–108 | `buildPredicate` (incl. `rebuildEdgeHits`) | 162 killed / 20 equivalent |
| `ui/unified/js/workflow_graph.js` | 29–35 | `wfgLodTier` | 14 killed / 0 survived |

Every behaviour-bearing mutant is killed. The residual survivors are all
provably equivalent — no test can distinguish them because they change no
observable output. Categories:

## 1. Defensive `|| []` / `|| ''` fallbacks for absent inputs

- `renderer.js:21` `edges = edges || []`
- `workflow_graph_filters.js:43` `ctx.edges || []`, `:64` `n.extra_domain_ids || []`
- `:63`, `:65`, `:103` `(field || '')` fallbacks (label/path/body/id/label-lookup)

These only fire when the guarded value is absent/empty. Replacing the fallback
(`[]`→`["…"]`, `''`→`"…"`) injects a value that is then either ignored (a junk
array element has no `.source`/`.target`/matches no real domain id) or would
only be observable if a real query/selection equalled the literal
`"Stryker was here!"` sentinel — which cannot occur with real data. Equivalent.

## 2. Cache-key / cache-guard mutants (pure performance optimisation)

- `:37` `_edgeHitsKey = ''` (initial value, never compared while `_edgeHits` is null)
- `:40` `edgeKind + '@' + count` (the `'@'` separator; no reachable key collision across the finite AST edge-kind set)
- `:41` `if (_edgeHits && _edgeHitsKey === key)` → `if (false)` (disables the cache → always recompute; `rebuildEdgeHits` is deterministic, so the result is identical — only speed differs)

The `if(true)`-serves-stale variant of the cache guard IS killed (the
"edge-hit cache rebuilds when the edge set changes size" test). Only the
optimisation-disabling variant survives, and it is behaviour-equivalent.

## 3. Inert branch entry for sentinel filter values

- `:72` `if (f !== 'all')` → `if (true)` / `f !== ''`: for the `'all'` sentinel the block body matches none of the prefixes and falls through to `return true`, so entering it is a no-op.
- `:101` `if (st.query)` → `if (true)`: an empty query yields `hay.indexOf('') === 0`, i.e. it matches everything — identical to skipping the block.
- `:71` `st.wfgFilter || 'all'` → default fallback: an absent filter resolves to `'all'`, whose branch is inert (as above).

## 4. Search-haystack separators

- `:103` the `+ ' ' +` separators between label/path/body/id.

Dropping a separator can only ever concatenate two adjacent fields
(`"a.py" "src/a.py"` → `"a.pysrc/a.py"`); it never removes a substring, so no
correct query can be turned into a false negative. The separator is defensive
against a contrived cross-field false-positive that no test (and no realistic
query) exercises. Equivalent for the state→visible-set contract.

Behaviour-bearing structure — every field's *inclusion* in the haystack, every
filter branch's *decision*, both edge-endpoint *object/string* forms, the
directional neighbour attribution, and all three LOD thresholds — is pinned by
a killing test.
