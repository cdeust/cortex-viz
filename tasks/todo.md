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
