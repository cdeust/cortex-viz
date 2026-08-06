# Brain view — node search by similarity (2026-07-13)

## Constat (phase Understand, 5 lecteurs + vérification empirique)

- La vue brain (`ui/brain-viz.html`, page standalone servie à `/brain`) streame le
  graphe COMPLET via `/api/graph/full/stream` (NDJSON) — **vérifié sur le serveur
  live (port 3458)** : chaque nœud arrive en dict complet avec `id`, `kind`,
  `label`, `color`, `domain_id`, `path`, `type`, `domain`… 186 228 nœuds /
  447 941 arêtes aujourd'hui (278 557 mesurés au 2026-07-02 — dimensionner ~300k).
- Tous les labels sont donc **déjà côté client** (`ui/brain/js/data.js` accumule
  dans des arrays plats ; le tooltip lit `n.label || n.id`, interact.js:151).
- Le fly-to existe déjà : `JUG.selectNodeById(id)` → `BRAIN.selectNode` +
  `BRAIN.focusNode` → `BRAIN.focusOn` (scene.js:113, 520 ms) + panneau détail.
- Aucune recherche dans la vue brain ; 4 inputs de recherche existent ailleurs
  (galaxy `#search-box` substring, wiki, knowledge FTS, atom) — conventions :
  input débounce inline 120–300 ms, guard clavier `INPUT/SELECT`, Escape ferme.
- **Aucun changement requis dans Cortex ni automatised-pipeline pour la v1** —
  contrairement à l'intuition initiale (« implication for sure ») : la trace
  montre que toutes les données nécessaires sont déjà dans le graphe streamé.
  Upgrade path v2 documenté en fin de fichier.

## Décisions de conception (sourcées)

1. **Similarité = pg_trgm, sémantique word_similarity** (max par mot).
   - Mesure de base : trigrammes pg_trgm exacts — lowercase, mots = séquences
     alphanumériques, padding « deux espaces devant, un derrière », score =
     Jaccard |∩|/|∪| des ensembles de trigrammes.
     Source : PostgreSQL docs, module pg_trgm (implémentation de référence,
     active dans la DB cortex — vérifié `pg_extension`).
   - Justification word_similarity vs similarity : mesuré sur la DB de référence
     `similarity('http','http_standalone_routes') = 0.2173913` (< seuil) vs
     `word_similarity(...) = 1.0`. Les labels de nœuds sont des identifiants
     longs ; la requête est courte → max-par-mot est la sémantique correcte.
   - Score final d'un nœud = moyenne sur les tokens de la requête du max sur
     les mots du haystack (label + dernier segment de path) de sim_trgm(token, mot).
     Multi-token = AND (moyenne), tie-break déterministe (score desc, label asc, id asc).
   - Découpage des mots : séquences alphanumériques (pg_trgm) + split camelCase
     et `_ :: / .` (précédent interne : AP `src/search/vector.rs` tokenise ainsi).
   - Seuil d'affichage : **0.3** — valeur par défaut documentée de
     `pg_trgm.similarity_threshold`. Aucune autre constante inventée.
   - **Conformité testée contre la référence** : paires générées par
     `SELECT similarity(a,b)` sur la DB cortex (pg_trgm réel), figées en fixture,
     comparées à l'implémentation JS via node (disponible : v24.7.0).

2. **Exécution dans un Web Worker** (`ui/brain/js/search_worker.js`).
   - Budget frame Three.js : 16,7 ms à 60 Hz ; un scan de ~300k nœuds coûte des
     dizaines de ms → hors main thread (source : RAIL model, budget animation).
   - Index construit une fois à la fin du stream : par nœud, mots précalculés en
     trigrammes hashés triés (typed arrays). Par requête : scan + intersection
     de listes triées. Mesurer et rapporter le temps réel (zetetic : mesure).
   - Protocole de messages (contrat figé) :
     - main→worker `{type:'index', nodes:[{id,label,path,kind}]}` (une fois)
     - main→worker `{type:'query', q:string, seq:int, limit:int}`
     - worker→main `{type:'ready', count:int, elapsed_ms:number}`
     - worker→main `{type:'results', seq:int, total:int, elapsed_ms:number,
       items:[{id,label,kind,path,score}]}` (top `limit`, score ≥ 0.3,
       les résultats d'un `seq` obsolète sont jetés côté main)

3. **UI** (`ui/brain/js/search.js` + markup `ui/brain-viz.html`).
   - Boîte dans le cluster `#chrome-top-right` (convention brain), classes DS :
     `.aia-inputwrap` + `.aia-input__icon` + `input.aia-field` (core.css:80-94,
     même pattern que le kit UI officiel). Gate design AI Architect appliqué :
     aliases uniquement, pas de hex brut, terracotta = sélection seulement,
     deux surfaces (paper/ink), texte ≥ 11 px, counts exacts, empty state
     littéral (`no node matches "q"` / `247 matches`).
   - Debounce 200 ms inline (convention repo).
   - Clavier : `/` focus la recherche (guard `INPUT/SELECT` comme controls.js:147),
     `↑/↓` navigation, `Enter` sélectionne, `Escape` ferme et blur.
     A11y : role combobox/listbox + aria-activedescendant.
   - Sélection d'un résultat → `JUG.selectNodeById(id)` (sélection + fly-to +
     panneau détail, mécanisme existant). Si un filtre kind (légende) masque le
     nœud choisi, le filtre est levé avant le focus (état honnête à l'écran).

## Étapes

- [x] 1. `trigram.js` + `search_worker.js` (agent zetetic engineer, Sonnet) +
        fixture de conformité pg_trgm générée depuis la DB + test node.
- [x] 2. `search.js` + markup/CSS brain-viz.html + alimentation du worker depuis
        boot.js après fetchGraph (agent zetetic frontend-engineer, Sonnet).
- [x] 3. Tests : conformité trigramme 30/30 (node vs fixture PG, chemin packed
        BigInt inclus), pytest 294 verts.
- [x] 4. Smoke test navigateur (Playwright/Chrome, serveur live 3458) :
        11/11 étapes vertes, 0 erreur console, captures paper + ink.
- [x] 5. Review adversariale (3 lentilles → 20 findings → 14 confirmés par
        vérification contradictoire → fix-pass zetetic) puis remember.

## Review (2026-07-13)

**Décisions amendées pendant la review — toutes vérifiées contre référence :**

1. **Fallback requêtes courtes (containment)** : un token de moins de 3 chars
   (< 4 trigrammes paddés) ne peut jamais atteindre 0.3 en Jaccard
   (mesuré : 'se'/'search' = 0.25). scoreNode utilise pour ces tokens le
   containment |∩|/|Q| — **vérifié égal à `word_similarity()` de pg_trgm sur
   la DB de référence** : py/python 0.667, se/search 0.667, s/search 0.5,
   ab/about 0.667, se/base 0.333 (5/5 exacts).
2. **Invariant z-index** : le dropdown vit dans le contexte d'empilement
   `#chrome-top-right` (z 30) ; `#detail-panel` est à z 200. La classe
   `.search-open` monte le cluster à 220 uniquement pendant que le dropdown
   est ouvert (sous #brain-tip 300 et les modales ; au-dessus du panneau).
3. **Invalidation des réponses en vol** : closeDropdown() fait
   clearTimeout + seqCounter++ ; onWorkerMessage ne rend que msg.seq ===
   seqCounter. Tue les 3 chemins de réouverture fantôme (Escape, Enter,
   effacement).
4. **splitCamel** : la règle acronyme cassait 'userIDs' → ['user','i','ds'] ;
   indexWords unionne désormais les découpes (ids/userIDs score 1.0).
5. Escape dans le champ ne ferme plus les panneaux impact/discussion
   (guards INPUT/SELECT/TEXTAREA ajoutés, convention detail_panel.js).

**Mesures finales** : conformité pg_trgm 30/30 (1e-6) ; bench 300k labels
synthétiques : index ~1.5-2.0 s (une fois, off-thread), scan ~80 ms/requête ;
corpus réel 186 228 nœuds indexé par un vérificateur : cohérent. Smoke test
navigateur 11/11. « standalon » → 1 030 matches, fly-to + détail OK ;
« py » → 134 936 matches honnêtement comptés, ranking utile.

**Réfutés par vérification** (ne pas « re-corriger ») : sémantique
mean-over-tokens (conforme au design gelé) ; sous-estimation du bench
(réfutée en indexant le corpus réel) ; débordement viewport du dropdown
(géométrie fausse : le wrap est l'enfant le plus à gauche du cluster).

## Upgrade path v2 (hors périmètre, nécessiterait Cortex/AP)

- Recherche sémantique des nœuds mémoire : `memories.content` est déjà indexé
  HNSW (pgvector) — il manque un embedder 384-dim côté viz (dépendance nouvelle),
  zéro changement Cortex.
- Recherche hybride AP (BM25+TFIDF+RRF, `search_codebase`) : bloquée par le
  mismatch d'ID documenté (ids search non hashés + paths relatifs vs ids graphe
  hashés + paths absolutisés — workflow_graph_source_ast.py:220 vs
  workflow_graph_schema.py:174) et la couverture 8/21 labels. À traiter comme
  feature séparée si le besoin sémantique/code émerge.

---

# Trace view — anchored expansion and live observed actions (2026-08-05)

## Contract

- Clicking a domain or session expands its neighbours around the clicked node;
  existing nodes and the camera transform stay stable.
- An expanded session receives observed actions while Trace remains open.
- The graph exposes captured prompts, tool/MCP calls, file reads/edits/writes,
  terminal commands, skills, subagents and web calls, with existing detail,
  diff and impact panels reachable from the corresponding nodes.
- PostgreSQL is the primary activity store. Existing degraded fallbacks remain
  available when PostgreSQL is unavailable.
- No synthetic database/API/file activity is inferred when the capture payload
  does not contain evidence for it.

## Root causes pinned before implementation

- [x] The same-view bridge sends `trace.v1` deltas through the Galaxy append
      fast-path. That path mutates nodes/edges but leaves topology, slots,
      adjacency and degrees stale, so session children get a random domain
      anchor and appear to rush toward the centre.
- [x] The session expansion does not initialise its `next_since` cursor, so the
      existing live-tail loop never polls it.
- [x] The SSE client deliberately buffers every activity batch outside Galaxy;
      it has no contract for projecting a batch onto an expanded Trace session.

## Execution

- [x] Add regression tests for topology-aware Trace append and stable viewport.
- [x] Recompute Trace topology/slots/adjacency in place and seed new nodes at
      deterministic session-relative positions without remounting the canvas.
- [x] Initialise per-session live cursors and route matching SSE activity to the
      expanded session while preserving Galaxy buffering.
- [x] Verify activity projection and detail affordances for every supported
      host-neutral observed event kind.
- [x] Verify PostgreSQL replay/live cursor continuity and fallback behaviour.
- [x] Run JS and targeted Python suites plus a browser interaction smoke test.

## Review

Implemented and verified on the real PostgreSQL-backed server:

- Trace append recomputes topology/slots/adjacency in place, preserves every
  existing position and the private zoom transform, pins the new deterministic
  session-relative positions, refreshes hit-testing and never remounts/reheats.
- Expanded sessions initialise their JSONL `next_since` fallback and accept
  only matching directed SSE fragments; full batches remain buffered for
  Galaxy. PostgreSQL ids are now the only durable SSE cursor, including
  Last-Event-ID reconnects and replay/live overlap deduplication.
- Historical Claude JSONL accepts every named tool call, including arbitrary
  MCP and Skill calls. The hook retries a stale registry against the configured
  port and official `3458` default within its existing 0.5 s total budget.
- Public observed vocabulary now includes explicit `mcp_call`, `api_call`,
  `db_read` and `db_write`. Actions expose bounded input/result summaries,
  host provenance and targets; legacy Claude credentials are redacted.
- File nodes retain diff/history/AST/impact drill; reverse-impact files now use
  the same canonical hashed identity as activity and Galaxy nodes.

Evidence:

- Python: 1015 passed, 20 skipped (one pre-existing asyncio resource warning).
- JavaScript: 269/269; ESLint and Ruff clean; `git diff --check` clean.
- Browser + real PG: clicked session position was bit-identical before/after
  expansion (`822.9715, 740.4819`), 168 child events stayed within a 177.06 px
  radius, and a temporary `db_read` appeared live, positioned and adjacent,
  with target/input/result/host in the one detail panel and zero console errors.
  The temporary activity row was deleted and the validation server stopped.

## Visual correction after user review

The first acceptance check was incomplete: it proved that new nodes did not
overlap each other and that the clicked hub stayed fixed, but did not measure
new nodes against the already visible session hubs. The real 206-node
reproduction has 445 new-to-existing overlaps, with 22.52 px worst overlap,
and reveals the complete response in one frame.

- [x] Record the corrected visual contract and the failed validation lesson.
- [x] Reproduce and measure new-to-existing overlap in a real browser.
- [x] Animate a bounded local redistribution that resolves both collision sets.
- [x] Reveal historical chains progressively without dropping live SSE deltas.
- [x] Re-run the real-session browser metric, capture, console check and suites.

Corrected evidence on the same PostgreSQL-backed session:

- 206 historical nodes appeared over 445 animation frames with 187 distinct
  node-count states; the first revealed node occupied 442 distinct positions
  while settling, rather than teleporting to its final slot.
- New-to-existing overlap fell from 445 to 0; new-to-new overlap is also 0,
  including when measured with the engine's stricter `collisionRadius`. The
  minimum final collision gap is tangent within floating-point precision.
- Every node outside the selected cluster moved 0 px. The selected session
  yielded locally by 24.46 px (31.15 px maximum during animation), while the
  canvas zoom transform remained bit-identical.
- A temporary `db_read` SSE action became visible after only one historical
  unit had been revealed (four new nodes total), proving that live activity is
  not blocked behind the 206-node historical queue. Its PostgreSQL row was
  deleted after the check.
- Browser console errors: 0. Final capture:
  `/private/tmp/cortex-viz-trace-layout-after.png`; streaming capture:
  `/private/tmp/cortex-viz-trace-streaming-after.png`.
- JavaScript: 273/273; Python: 1015 passed, 20 skipped; ESLint and diff check
  clean; `workflow_graph.js` remains below the 500-line repository gate.

## Domain-level reflow correction after user review

The selected-session-only pack remains too local: sibling session hubs are
treated as immutable obstacles, so a growing session is confined to leftover
gaps. A Wiki → Trace remount recomputes the whole domain and is visibly clearer.

- [x] Rebind the correction and recall prior layout decisions/failures.
- [x] Measure incremental geometry against the Wiki → Trace remount oracle.
- [x] Share one radius-exact domain packer between full and incremental Trace.
- [x] Animate every node in the affected domain to the shared target layout.
- [x] Prove sibling movement, collision freedom, roundtrip parity, stable camera
      and collision-triggered propagation without moving domain anchors.
- [x] Run the complete JS/Python verification and leave the live server open.

Review evidence:

- Before: the 148-session / 206-action regression fixture held all 147 sibling
  sessions fixed while the canonical remount assigned them different targets.
- After: all 147 sibling sessions move continuously to the shared canonical
  domain targets; the 206 actions plus 149 existing affected-domain nodes have
  zero pairwise overlap at the engine's `collisionRadius`.
- Incremental final positions are bit-identical to a fresh full mount for every
  canonical node, even when input nodes are reordered. A deliberately displaced
  session in a non-colliding domain stays bit-identical after an edge-only append;
  when two domains really collide, neighbouring children yield but both domain
  anchors stay fixed and the global pairwise overlap count remains zero.
- Back-to-back appends before the first frame and interleaved appends after one
  frame both converge: pending targets are merged instead of stranding the first
  domain halfway through its animation.
- The radius-derived spatial grid preserves identical targets while reducing a
  1,000-node synthetic resolver benchmark from 970,660 to 8,987 collision checks
  (108.01x); five runs fell from 51.67 ms to 7.06 ms (7.32x).
- Final interpolation frame assigns the exact target coordinates, so no later
  d3 end event, seed-pinning timer or view destruction can cause a terminal
  snap. Canvas redraw still leaves its private zoom transform untouched.
- JavaScript: 278/278; Python: 1015 passed, 20 skipped with the existing asyncio
  resource warning; ESLint, Ruff and `git diff --check` clean;
  `workflow_graph.js` remains at the 500-line gate.
- The live server serves the corrected asset at
  `http://127.0.0.1:3458/trace`; the integrated browser backend was unavailable
  in this session, so the final subjective visual check is left open to the
  user on that server.

## Animation scheduler correction after user review

The canonical final geometry is accepted, but transitions still over-animate:
micro-batches trigger questionable full-domain motion, and rapid multi-clicks
can keep restarting the interpolation so it never reaches completion.

- [x] Record the correction and recall the prior animation decisions.
- [x] Reproduce sustained retargeting and quantify unnecessary movement.
- [x] Make one coalescing scheduler progress monotonically under target updates.
- [x] Restrict animation to meaningful/new/collision-displaced nodes while
      preserving final remount parity.
- [x] Verify multi-click termination, streaming visibility, collision freedom,
      camera stability and the complete suites on the live server.

Review evidence:

- Before: one retarget created a second queued rAF and incremented the animation
  token; sustained appends reset the 257-frame cooling-derived interpolation
  indefinitely. After: one rAF chain remains queued, existing targets keep a
  monotone deadline, and a target cycle completes during sustained retargeting.
- The minimal-selection fixture fell from five animated same-domain nodes to
  the two nodes supported by evidence: one new node and one changed canonical
  slot. Unchanged nodes are no longer reanimated merely because their domain
  was touched; changed collision/remount targets still converge exactly.
- Trace reflow uses the design system's existing `--dur-panel` budget (300 ms)
  per node. A late node receives its own full transition, but a retarget cannot
  extend an active node's deadline. A 1,000 ms throttled-rAF regression proves
  a late arrival starts on its first painted frame instead of snapping.
- Exact canonical convergence, global collision freedom, remount parity,
  interleaved/back-to-back appends, stable camera redraw and destroy
  cancellation remain covered. Adversarial re-review reports no blocker.
- JavaScript: 282/282; Python: 1015 passed, 20 skipped with the existing asyncio
  resource warning; ESLint and `git diff --check` clean; `workflow_graph.js`
  remains at the 500-line gate.
- `http://127.0.0.1:3458/trace` returns HTTP 200 and serves the corrected
  scheduler directly from the working tree; the subjective motion check is
  ready for a hard browser refresh.

## Trace legend and file-detail correction after user review

The layout and animation are accepted. Incremental Trace data still leaves the
legend stale, and selecting a file node never resolves its detail card.

- [x] Record the correction and recall prior Trace/detail failures.
- [x] Reproduce legend staleness and file-detail non-loading independently.
- [x] Identify and fix the legend's incremental source-of-truth contract.
- [x] Complete the file selection/fetch contract for both path-first and
      sparse-click-before-enrichment races.
- [x] Prove mixed streamed node kinds update the legend and real file details,
      run complete suites, and keep the live server ready for acceptance.

Review evidence:

- Before, the renderer cache still reported one node and zero edges after the
  accumulated graph had two nodes and one edge. After, append and enrichment
  update the live clone and the same event turn refreshes the HUD/sidebar kind,
  node and edge counts; a mixed delta reports domain, memory and discussion.
- A path-rich file click renders immediately. A sparse click now rerenders only
  that selected file when its path arrives, starts the authoritative trace-file
  request, and rejects an older failed node lookup instead of overwriting the
  newer card.
- The PostgreSQL-backed endpoint returned git metadata, versions and file
  metadata for `/Users/cdeust/.claude/tools/memory-tool.sh`.
- JavaScript: 292/292; Python: 1015 passed, 20 skipped with the existing asyncio
  resource warning; ESLint and `git diff --check` clean.

## Causal Trace reflow correction after user review

The accepted collision-free final geometry still reorganizes session content
by packing order rather than by the visible cause -> effect relationships. The
transition therefore looks like repeated placement attempts even when it ends
in a readable state.

- [x] Record the correction and recall prior causal-layout decisions/failures.
- [x] Measure the current layout against a directed causal-order oracle.
- [x] Make causal depth and edge direction the canonical Trace slot authority.
- [x] Preserve deterministic collision resolution, remount parity, bounded
      streaming animation, stable domain anchors and camera position.
- [x] Prove causal monotonicity under streamed/multi-click append, then rerun
      the complete suites and expose the result on the live server.

Review evidence:

- Before, kind-major packing put a later prompt inside an earlier action and
  inverted three of four directed causal edges after collision resolution.
  After, a stable Kahn order drives each session's radial progression and every
  causal edge in the shuffled fixture and 206-action regression progresses
  outward or remains on its allowed convergence layer.
- Historical node permutations, reversed node+edge order and fresh remounts
  yield bit-identical canonical slots. Cycles terminate deterministically and a
  chain longer than the former six-pass propagation limit retains its domain.
- A node shared by two causal branches is placed only after all its predecessors,
  at their collision-resolved convergence centroid. The former first-parent
  edge-order dependency was reproduced, fixed, and pinned by an exact reversed
  node+edge test; adversarial review reports no residual blocker.
- The existing coalescing 300 ms scheduler, camera stability and fixed domain
  anchors remain unchanged. Collision freedom, append termination and causal
  prefix stability stay covered.
- JavaScript: 292/292; Python: 1015 passed, 20 skipped with the existing asyncio
  resource warning; ESLint and `git diff --check` clean. The PostgreSQL-backed
  server is available at `http://127.0.0.1:3458/trace`.

## Causal Trace visual-regression correction after user rejection

The synthetic causal, collision and permutation oracles all passed, but the
real browser behavior is worse than the original. Those checks are necessary
structural invariants, not an acceptance oracle for motion or readability.

- [x] Record the rejected assumption and supersede the prior Cortex decision.
- [x] Reproduce the current trajectory against real PostgreSQL
      data and retain frame-level evidence of the failure.
- [x] Isolate whether the regression comes from radial causal slots, global
      collision displacement, domain-level retargeting, or their composition.
- [x] Replace or revert only the regressing placement rule while preserving the
      already accepted bounded scheduler, streaming, legend and file detail.
- [x] Validate the candidate visually before relying on synthetic invariants,
      then rerun complete suites and leave the server ready for acceptance.

Review evidence:

- Real PostgreSQL fixture: 631 nodes / 958 edges, including 116 file targets;
  42 files have multiple causal parents and the most reused file has 34.
- Rejected projection: mean session radius 627.70 px, maximum 1007.14 px;
  median causal-edge length 990.79 px and maximum 2003.40 px. Branch access
  edges were incorrectly treated as temporal spine progression.
- Accepted compact rollback on the identical payload: mean radius 229.21 px,
  maximum 353.96 px; median edge length 340.20 px and maximum 614.47 px.
  New effects seed tangent to their immediate causal parent instead of the
  session/domain hub, without changing the compact final planner.
- The user visually accepted the live result at `http://127.0.0.1:3458/trace`.
  JavaScript: 292/292; Python: 1015 passed, 20 skipped with the existing asyncio
  subprocess warning; ESLint, Ruff and `git diff --check` clean.
