// Cortex Brain View — trigram similarity engine (pure functions, no DOM).
//
// Two tokenizers, deliberately kept separate (tasks/todo.md "Décisions de
// conception" §1):
//   1. pg_trgm-EXACT (pgWords/trigramSet/similarity) — reproduces PostgreSQL's
//      contrib/pg_trgm module exactly, so it can be conformance-tested against
//      tests/fixtures/pg_trgm_reference.json (generated from the live pg_trgm
//      extension on the cortex DB, PostgreSQL 17.9, 2026-07-13). pg_trgm does
//      NOT split camelCase — this tokenizer must not either, or conformance
//      breaks.
//   2. Indexing tokenizer (indexWords/wordTrigrams/scoreNode) — used to build
//      the brain-view node search index. Same alnum-run word splitting as
//      pg_trgm, PLUS a camelCase split, because node labels/paths are code
//      identifiers (precedent: automatised-pipeline src/search/vector.rs
//      tokenizes symbols on `_`, `::`, camelCase).
//
// Loadable both via importScripts (Web Worker) and require() (Node, for the
// conformance test) — see the UMD-style export at the bottom.

(function (root) {
  'use strict';

  // pg_trgm defaults: LPADDING=2, RPADDING=1 (contrib/pg_trgm trgm_op.c).
  var LPAD = '  ';
  var RPAD = ' ';

  // contrib/pg_trgm.similarity_threshold documented default.
  // source: PostgreSQL docs, pg_trgm — "similarity_threshold (real) ... 0.3".
  var SIMILARITY_THRESHOLD = 0.3;

  // ---- pg_trgm-exact reference implementation ----------------------------

  // pg_trgm word = maximal run of alphanumeric (Unicode letter/digit) chars;
  // everything else (whitespace, punctuation, `_`, `/`, `:`, ...) is a
  // separator and contributes no trigrams of its own.
  function pgWords(s) {
    return String(s).toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  }

  function padWord(w) {
    return LPAD + w + RPAD;
  }

  // Per-string trigram SET (deduplicated), unioned across all of the
  // string's words. Verified against every pair in the reference fixture
  // (union-based Jaccard, not multiset — e.g. 'route'/'routes' = 5/8).
  function trigramSet(s) {
    var words = pgWords(s);
    var set = new Set();
    for (var i = 0; i < words.length; i++) {
      var p = padWord(words[i]);
      for (var j = 0; j + 3 <= p.length; j++) {
        set.add(p.slice(j, j + 3));
      }
    }
    return set;
  }

  // similarity(a,b) = |A∩B| / |A∪B| over the two strings' trigram sets.
  function similarity(a, b) {
    var A = trigramSet(a);
    var B = trigramSet(b);
    if (A.size === 0 && B.size === 0) return 0; // no trigrams either side
    var inter = 0;
    A.forEach(function (t) {
      if (B.has(t)) inter++;
    });
    var union = A.size + B.size - inter;
    return union === 0 ? 0 : inter / union;
  }

  // ---- indexing tokenizer (camelCase-augmented) ---------------------------

  // Split an alnum run on camelCase boundaries: lower/digit→upper ('fooBar'
  // -> 'foo Bar') and acronym→titlecase ('HTTPServer' -> 'HTTP Server').
  function splitCamel(word) {
    return word
      .replace(/([\p{Ll}\p{N}])(\p{Lu})/gu, '$1 $2')
      .replace(/(\p{Lu}+)(\p{Lu}\p{Ll})/gu, '$1 $2')
      .split(' ')
      .filter(Boolean);
  }

  // Same lower/digit->upper boundary as splitCamel, WITHOUT the acronym rule
  // (\p{Lu}+)(\p{Lu}\p{Ll}). The acronym rule backtracks an all-caps run to a
  // single leading letter before a trailing lowercase suffix ('userIDs' ->
  // 'user'+'I'+'Ds'), which makes the acronym itself ('ids') unfindable as a
  // whole word. indexWords unions this rule's parts with splitCamel's so the
  // unsplit acronym+suffix run survives as an extra indexed word.
  function splitLowerUpperOnly(word) {
    return word
      .replace(/([\p{Ll}\p{N}])(\p{Lu})/gu, '$1 $2')
      .split(' ')
      .filter(Boolean);
  }

  // Words for indexed node text / query text: pg_trgm alnum-run splitting,
  // then each run split on camelCase boundaries two ways (full split
  // including the acronym rule, and lower/digit->upper only) with the
  // results unioned and deduplicated. For runs with no acronym+suffix
  // boundary the two splits agree and dedup collapses them back to the
  // original single split; for runs like 'userIDs' the union keeps both
  // ['user','i','ds'] (full split) and ['user','ids'] (boundary-only split),
  // so the acronym 'ids' is indexed as a whole word too.
  function indexWords(s) {
    var runs = String(s).match(/[\p{L}\p{N}]+/gu) || [];
    var out = [];
    var seen = Object.create(null);
    function pushWord(w) {
      var lw = w.toLowerCase();
      if (!seen[lw]) {
        seen[lw] = true;
        out.push(lw);
      }
    }
    for (var i = 0; i < runs.length; i++) {
      var camelParts = splitCamel(runs[i]);
      var boundaryParts = splitLowerUpperOnly(runs[i]);
      for (var j = 0; j < camelParts.length; j++) pushWord(camelParts[j]);
      for (var k = 0; k < boundaryParts.length; k++) pushWord(boundaryParts[k]);
    }
    return out;
  }

  // ---- packed-trigram encoding (typed-array-friendly) --------------------

  function codePoints(str) {
    return Array.from(str).map(function (ch) {
      return ch.codePointAt(0);
    });
  }

  // Pack 3 Unicode code points (each < 2^21, since max code point 0x10FFFF
  // < 2^21) into one 63-bit integer: cp0*2^42 + cp1*2^21 + cp2. Stored as
  // BigInt — a true 64-bit integer — rather than Number (53-bit mantissa),
  // so the packed value is always exact and two different trigrams can never
  // collide to the same integer.
  function packTrigram(cp0, cp1, cp2) {
    return (BigInt(cp0) << 42n) | (BigInt(cp1) << 21n) | BigInt(cp2);
  }

  function bigintCompare(a, b) {
    return a < b ? -1 : a > b ? 1 : 0;
  }

  // Sorted (ascending), deduplicated array of packed trigrams for one
  // already-split, already-lowercased word. Sorted so callers can intersect
  // two words' trigrams in O(m+n) instead of building a hash set per call.
  function wordTrigrams(word) {
    var cps = codePoints(padWord(word));
    var set = new Set();
    for (var i = 0; i + 3 <= cps.length; i++) {
      set.add(packTrigram(cps[i], cps[i + 1], cps[i + 2]));
    }
    var arr = Array.from(set);
    arr.sort(bigintCompare);
    return arr;
  }

  // Count of shared elements between two sorted packed-trigram arrays via a
  // single merge pass. Shared by trigramSimilarityPacked (Jaccard) and
  // scoreNode's short-token containment fallback below.
  function trigramIntersectionCount(a, b) {
    var i = 0;
    var j = 0;
    var inter = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) {
        inter++;
        i++;
        j++;
      } else if (a[i] < b[j]) {
        i++;
      } else {
        j++;
      }
    }
    return inter;
  }

  // Jaccard similarity between two sorted packed-trigram arrays — same
  // result as trigramSet-based similarity(), just faster for precomputed
  // per-word arrays.
  function trigramSimilarityPacked(a, b) {
    var inter = trigramIntersectionCount(a, b);
    var union = a.length + b.length - inter;
    return union === 0 ? 0 : inter / union;
  }

  // A word of length L has L+1 padded trigrams (LPAD 2 + RPAD 1, window 3:
  // (L+3)-3+1 = L+1); a 3-char window word (L>=3) always has >=4 trigrams, so
  // a query token with fewer than 4 trigrams has L<3. Below that window a
  // union-based Jaccard can never reach a useful score (verified: 's' vs
  // 'search' = 0.125, 'se' vs 'search' = 0.25, both < SIMILARITY_THRESHOLD),
  // even though the token is a genuine prefix. source: PostgreSQL pg_trgm
  // docs, word_similarity — for a short query, containment
  // (|intersection|/|query|) equals word_similarity's best-extent value
  // whenever the matched trigrams are contiguous in the target, and is what
  // recovers query 'se' matching 'search'/'base' at the thresholds pg_trgm
  // itself would report.
  var SHORT_TOKEN_TRIGRAM_COUNT = 4;

  // Node score = mean over query tokens of (max over node words of the
  // word-pair similarity). tasks/todo.md §1: word_similarity semantics
  // approximated as max-per-word, since node labels are long identifiers and
  // queries are short. Tokens below the trigram window use containment
  // instead of Jaccard (see SHORT_TOKEN_TRIGRAM_COUNT above); longer tokens
  // keep the original Jaccard scoring.
  function scoreNode(queryTriLists, nodeWordTriLists) {
    if (queryTriLists.length === 0 || nodeWordTriLists.length === 0) return 0;
    var sum = 0;
    for (var i = 0; i < queryTriLists.length; i++) {
      var q = queryTriLists[i];
      var isShortToken = q.length < SHORT_TOKEN_TRIGRAM_COUNT;
      var best = 0;
      for (var j = 0; j < nodeWordTriLists.length; j++) {
        var w = nodeWordTriLists[j];
        var s = isShortToken
          ? (q.length === 0 ? 0 : trigramIntersectionCount(q, w) / q.length)
          : trigramSimilarityPacked(q, w);
        if (s > best) best = s;
      }
      sum += best;
    }
    return sum / queryTriLists.length;
  }

  var TRGM = {
    SIMILARITY_THRESHOLD: SIMILARITY_THRESHOLD,
    pgWords: pgWords,
    trigramSet: trigramSet,
    similarity: similarity,
    indexWords: indexWords,
    wordTrigrams: wordTrigrams,
    trigramSimilarityPacked: trigramSimilarityPacked,
    scoreNode: scoreNode,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = TRGM;
  } else {
    root.TRGM = TRGM;
  }
})(typeof self !== 'undefined' ? self : this);
