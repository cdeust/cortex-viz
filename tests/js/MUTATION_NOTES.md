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

---

# Coverage-honesty seams (issue #36 AC#6)

Same discipline for the coverage-honesty logic. Scope (`stryker.conf.json`,
per-commit narrow gate — only the lines this change touched):

| File | Range | Meaning | Score |
|---|---|---|---|
| `ui/unified/js/workflow_graph.js` | 48–63 | `wfgEdgeCoverage` (LOD/dangling edge accounting) | 27 killed / 0 survived |
| `ui/unified/js/coverage_model.js` | 30–253 | verdict logic (`axis`/`filesAxis`/`lodAxis`/`staleness`/`omissions`/`computeCoverage`) | 276 killed / 3 equivalent |
| `ui/unified/js/coverage_indicator.js` | 55–73, 84–101 | `gatherSources` + `fmtAge` | 59 killed / 0 survived |

`workflow_graph.js` and `coverage_indicator.js` reach 100% (0 survivors). Every
behaviour-bearing mutant of the model is killed — each omission's *presence*
and *exact label*, every staleness rule (revision-mismatch, count-growth,
zero-snapshot guard, both-revisions requirement, node-only/edge-only growth),
the age computation, the `complete`/`incomplete`/`unknown` verdict on every
denominator combination, and every named degraded mode — is pinned by a killing
test. Two rounds of triage turned three redundant survivors into killed mutants
by removing the redundancy (§9): the second `report.status = 'unknown'`
assignment (the initial value already carries it), the dead `omissions: []`
initialiser (always overwritten), and two needless `!= null ? x : null`
null-normalisations (`now`, `captured_at`) that the downstream guard already
handles.

The 3 residual survivors are provably equivalent — the mutated boundary maps to
the identical value on both sides, so no test can distinguish them:

- `coverage_model.js:32` `count()` `n < 0` → `n <= 0`: the boundary value `0`
  returns `0` either way (`floor(0) === 0`).
- `coverage_model.js:48` `axis()` `s > r` → `s >= r`: when `s === r`, the
  taken branch computes `s - r === 0`, identical to the `: 0` else.
- `coverage_model.js:60` `filesAxis()` `present > indexed` → `>=`: same shape —
  at equality, `present - indexed === 0 === the else`.

Non-equivalent survivors: 0 (§12.4 satisfied).

---

## Run — `ui/brain/js/trigram.js` (js/remote-property-injection #153/#154)

Scope (§12.1 per-commit narrow gate — only the changed lines):

| File | Range | Meaning | Score |
|---|---|---|---|
| `ui/brain/js/trigram.js` | 105–123 | `indexWords` (dedup map `Object.create(null)` → `Set`) | 100% — 39 killed + 1 timeout / 0 survived |
| `ui/brain/js/trigram.js` | 136–146 | `uniqueWords` (moved here from `search_worker.js`, same map change) | (same run) |

Non-equivalent survivors: 0 (§12.4 satisfied). No mutant needed an equivalence
argument — the first pass left 5 survivors + 1 uncovered mutant, and all six
were genuine test gaps, closed by tests rather than reasoned away:

- **The security change itself is pinned, not merely current.** Reverting either
  map to a plain object (the exact defect the alerts named) is killed by 3 tests
  in "trigram dedup does not collide with inherited property names". Verified by
  injecting that mutant by hand before trusting the suite.
- **Both camelCase split loops (`:119`, `:120`) survived** disabling *and*
  boundary inversion. The union of the two splits is the reason
  `splitLowerUpperOnly` exists — trigram.js:84–95 explains it with `userIDs` as
  the worked example — but nothing asserted it, so either loop could be deleted
  silently. Closed by "unions both camelCase splits so an acronym stays findable
  whole" (`userIDs`, `HTTPServer`, `fooBar`).
- **The alnum-run class (`:106`) survived widening** `\p{N}` → `\P{N}`, which
  makes `/`, `-`, `_` word characters. Nothing asserted that a separator
  separates. Closed by "treats every non-alphanumeric character as a separator".
- **The `|| []` fallback (`:106`) had no coverage at all** — no test passed a
  string with no alphanumeric run. Closed by "yields no words when the string
  holds no alphanumeric run" (`''`, `'---'`, `'   '`).

Unlike the `|| []` fallbacks triaged as equivalent above, this one is
observable: the mutated value flows into `splitCamel`, which splits it into
real indexed words.

---

## Run — brain-view HTML escapers (js/incomplete-html-attribute-sanitization #148–#151)

Scope (§12.1 per-commit narrow gate — only the changed lines):

| File | Range | Meaning | Score |
|---|---|---|---|
| `ui/brain/js/boot.js` | 63–69 | `esc` (legend rows; character class grew to cover `"` and `'`) | 100% — 13 killed / 0 survived |
| `ui/brain/js/impact.js` | 22–26 | `esc` (impact panel; same widening) | 100% — 10 killed / 0 survived |

Non-equivalent survivors: 0 (§12.4 satisfied). Both escapers reach 100% on the
first pass except for one gap, closed by a test rather than reasoned away:

- **`impact.js:23`'s null-coercion arm** (`s == null ? '' : s`) survived
  `ConditionalExpression` and had **no coverage at all** for its `''` literal.
  The boot.js describe block asserted the coercion; the impact.js one did not,
  so `String(null)` → the literal text `"null"` would have rendered in the panel
  unnoticed. Closed by "leaves ordinary text untouched and coerces
  null/undefined to empty" in the impact block. This is the common path, not an
  edge case — an impact row's `label`/`kind`/`confidence` are usually absent.

Each escaped character is its own mutant here (the class `[&<>"']` and the five
replacement entities), so the property that actually closes the alerts — that
`"` is escaped — cannot regress silently: dropping it from either the class or
the entity table is killed by both the direct `esc('"')` assertion and the
parsed-DOM attribute-breakout tests in `brain_escape.test.mjs`.

---

## Run — renderings that were computed and discarded (js/unused-local-variable #155/#160/#163/#164/#165/#174/#175)

Scope (§12.1 per-commit narrow gate — only the changed lines):

| File | Range | Meaning | Score |
|---|---|---|---|
| `ui/unified/js/knowledge.js` | 52–56, 695–703, 737–743, 1109–1112, 1130–1145 | `stageMeta`, the feel-dot emotion ink, the badge row, `codeBlock`, the fence loop | 98.28% — 57 killed / 1 survived (equivalent) |
| `ui/dashboard/js/interaction.js` | 135–152 | `connItemHtml` (the relationship-kind row) | 96.00% — 24 killed / 1 survived (equivalent) |
| `ui/unified/js/polling.js` | 22–33, 88–97 | offline degradation (threshold, empty-canvas test, catch arm) | 100% — 22 killed / 0 survived |
| `ui/unified/js/wiki.js` | 2075–2089 | `buildEditorSetup` (CM6 history + keymap assembly) | 100% — 36 killed / 0 survived |

Total 139 killed / 2 survived, both equivalent (§12.4 satisfied: 0 non-equivalent
survivors). The first pass scored 85.62% with 16 survivors + 5 uncovered; every
one was triaged rather than reasoned away, and the gaps it exposed were real:

- **`connItemHtml`'s label arms had no coverage at all** — neither `d.name ||
  'Entity'` (a nameless entity) nor `(d.content || '')` (a contentless memory)
  nor the `.slice(0, 60)` truncation was exercised. Three tests added.
- **The row's closing `</div>` survived deletion.** The suite rendered one row
  at a time, so the parser auto-closed it; the live path concatenates rows into
  a single `innerHTML` write, where an unclosed row makes every later row its
  *child*. Closed by "closes the row so concatenated rows are siblings".
- **`stageMeta`'s empty-stage arm had no coverage** — `buildCard` guards with
  `if (stage)` and never reaches it, but the inspector's Stage row calls it
  unguarded. Closed by a direct `stageMeta('' | undefined | null)` assertion.
- **Four mutants in the fence loop survived together** (`inCode = true`, the
  close-branch block, `if (inCode)` → `true`, and the `</code></pre>` suffix)
  because every test held a *single* fenced block: with only one block, an
  unclosed one is flushed identically by the trailing "close unclosed code" arm.
  Closed by asserting that prose after a fence is a sibling of the block, that
  a second block does not inherit the first's lines, and that the fence must be
  line-anchored (which also killed the `/^```/` → `/```/` regex mutant).
- **`lines.join('\n')` survived** — every code body was one line. Closed by a
  two-line block asserting the break survives.

Two survivors were **dead code, not missing tests**, and were removed (§12.1:
mutation surfaces dead code — delete it, don't test it):

- **`codeLang = ''` on the closing fence.** Every opening fence assigns
  `codeLang` unconditionally, so the reset can never be observed. Deleted; the
  invariant is now a comment at the site.
- **`window.JUG &&` inside `_canvasEmpty`.** `useFallback` writes through
  `JUG.state` unconditionally two lines later, so a missing bus was never
  survivable — the guard only hid that. Simplified to a direct read.

### Equivalent mutants (documented, not ignored)

- **`interaction.js:149` — dropping `</span>` before `'</div>'`.** HTML5 parsing
  closes the open `<span>` at the `</div>`, so the built DOM is byte-identical;
  only a source-text assertion could tell the difference, and that would pin the
  implementation rather than the behaviour.
- **`knowledge.js:701` — `hasOwnProperty` guard → `if (true)`.** Without the
  guard the assignment is `style.background = undefined` (unknown/neutral
  feeling) or a stringified function (`emotion === 'constructor'`). Neither is a
  valid `<color>`, so CSSOM drops the declaration and the rendered dot is
  unchanged. The guard states the intent and keeps the prototype chain out of a
  style attribute; it is not observable through the DOM.
