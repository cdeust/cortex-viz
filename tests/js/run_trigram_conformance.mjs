// Conformance + scale harness for ui/brain/js/trigram.js.
//
// Two checks, one JSON result on stdout (consumed by
// tests/test_trigram_conformance.py):
//   1. similarity(a,b) reproduces every pair in
//      tests/fixtures/pg_trgm_reference.json (real PostgreSQL 17.9 pg_trgm
//      output) within float tolerance 1e-6.
//   2. Scale benchmark: index-build time and one query-scan time over a
//      synthetic 300k-label corpus, mirroring what search_worker.js does
//      (tasks/todo.md §2 — "mesurer et rapporter le temps réel").
//
// trigram.js is loaded via require() (its UMD export supports both
// importScripts and CommonJS) — createRequire lets an ESM harness call it
// without adding a build step to the vanilla-JS browser file.

import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..');

const TRGM = require(path.join(repoRoot, 'ui/brain/js/trigram.js'));
const fixture = JSON.parse(
  readFileSync(path.join(repoRoot, 'tests/fixtures/pg_trgm_reference.json'), 'utf8')
);

const TOLERANCE = 1e-6;

// checkConformance validates BOTH scoring paths against the same fixture:
//   1. TRGM.similarity — the string-Set reference implementation.
//   2. wordTrigrams + trigramSimilarityPacked — the BigInt packed path that
//      search_worker.js's scoreNode actually scores with in production. The
//      string path alone does not protect the packed path from regressions
//      (e.g. a wrong shift constant in packTrigram, or a broken merge), so a
//      pair only "passes" when both agree with the fixture within tolerance.
// The packed cross-check applies only where both sides reduce to exactly one
// pg_trgm word (wordTrigrams operates on a single already-split word) — 28 of
// the 30 fixture pairs. Words are derived via pgWords() (lowercases), never
// passed raw, or the ('BRAIN','brain') pair would spuriously fail.
function checkConformance() {
  const details = [];
  let failed = 0;
  for (const pair of fixture.pairs) {
    const got = TRGM.similarity(pair.a, pair.b);
    let ok = Math.abs(got - pair.similarity) <= TOLERANCE;

    const wa = TRGM.pgWords(pair.a);
    const wb = TRGM.pgWords(pair.b);
    let gotPacked = null;
    if (wa.length === 1 && wb.length === 1) {
      gotPacked = TRGM.trigramSimilarityPacked(TRGM.wordTrigrams(wa[0]), TRGM.wordTrigrams(wb[0]));
      if (Math.abs(gotPacked - pair.similarity) > TOLERANCE) ok = false;
    }

    if (!ok) failed++;
    details.push({ a: pair.a, b: pair.b, expected: pair.similarity, got: got, gotPacked: gotPacked, ok: ok });
  }
  return { passed: fixture.pairs.length - failed, failed: failed, details: details };
}

// Synthetic identifier-like corpus: permutations of common code-identifier
// fragments, camelCase-joined, so the benchmark exercises both tokenizers
// the way real brain-view node labels (functions/files/symbols) do.
function syntheticLabels(n) {
  const parts = [
    'get', 'set', 'build', 'parse', 'render', 'fetch', 'graph', 'node', 'edge',
    'brain', 'search', 'worker', 'index', 'trigram', 'similarity', 'score',
    'handler', 'stream', 'domain', 'memory', 'wiki', 'session', 'entity',
    'layout', 'scene', 'camera', 'impact', 'anatomy', 'detail', 'panel',
  ];
  const labels = new Array(n);
  for (let i = 0; i < n; i++) {
    const a = parts[i % parts.length];
    const b = parts[(i * 7 + 3) % parts.length];
    const c = parts[(i * 13 + 5) % parts.length];
    labels[i] = a + b.charAt(0).toUpperCase() + b.slice(1) + c.charAt(0).toUpperCase() + c.slice(1) + i;
  }
  return labels;
}

function buildBenchIndex(nodes) {
  const out = new Array(nodes.length);
  for (let i = 0; i < nodes.length; i++) {
    const words = TRGM.indexWords(nodes[i].label);
    const tri = new Array(words.length);
    for (let w = 0; w < words.length; w++) tri[w] = TRGM.wordTrigrams(words[w]);
    out[i] = { id: nodes[i].id, label: nodes[i].label, wordTriLists: tri };
  }
  return out;
}

function scanQuery(index, q, limit) {
  const tokens = TRGM.indexWords(q);
  const queryTri = tokens.map(TRGM.wordTrigrams);
  const scored = [];
  for (let i = 0; i < index.length; i++) {
    const score = TRGM.scoreNode(queryTri, index[i].wordTriLists);
    if (score >= TRGM.SIMILARITY_THRESHOLD) scored.push({ id: index[i].id, score: score });
  }
  scored.sort((x, y) => y.score - x.score);
  return scored.slice(0, limit);
}

function runBenchmark() {
  const N = 300000;
  const labels = syntheticLabels(N);
  const nodes = labels.map((label, i) => ({ id: 'n' + i, label: label }));

  const t0 = performance.now();
  const index = buildBenchIndex(nodes);
  const indexMs = performance.now() - t0;

  const t1 = performance.now();
  const results = scanQuery(index, 'searchNode', 20);
  const queryMs = performance.now() - t1;

  return { n: N, index_build_ms: indexMs, query_scan_ms: queryMs, result_count: results.length };
}

const conformance = checkConformance();
const bench = runBenchmark();
process.stdout.write(
  JSON.stringify({
    passed: conformance.passed,
    failed: conformance.failed,
    details: conformance.details,
    bench: bench,
  })
);
