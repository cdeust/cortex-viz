// @vitest-environment node
//
// pg_trgm conformance + scale benchmark for ui/brain/js/trigram.js.
//
// Absorbs the former side-channel harness tests/js/run_trigram_conformance.mjs
// (and its pytest wrapper tests/test_trigram_conformance.py) into the JS suite
// wired to CI (issue #35 AC#3). Two checks:
//   1. similarity(a,b) AND the BigInt-packed path reproduce every pair in
//      tests/fixtures/pg_trgm_reference.json (real PostgreSQL 17.9 pg_trgm
//      output) within float tolerance 1e-6.
//   2. Scale benchmark: index-build + one query-scan over a synthetic 300k
//      label corpus, mirroring search_worker.js's scoreNode path.
//
// The fixture (tests/fixtures/pg_trgm_reference.json) is ground truth: it was
// generated from PostgreSQL 17.9's real pg_trgm extension (DB cortex, verified
// via pg_extension). trigram.js must match it, never the reverse. Regenerate by
// re-running, against a live pg_trgm Postgres, the query whose VALUES list is
// exactly the fixture's {a,b} pairs:
//     SELECT a, b, similarity(a, b) FROM (VALUES ('http','http'), …) AS pairs(a, b);
import { describe, expect, inject, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..');

// Load trigram.js's UMD export directly through its CommonJS branch, bypassing
// vitest's ESM loader (which leaves `module` undefined and sends the file down
// its browser-`root` branch). This mirrors how the Web Worker importScripts it.
function loadUMD(rel) {
  const code = readFileSync(path.join(repoRoot, rel), 'utf8');
  const mod = { exports: {} };
  // eslint-disable-next-line no-new-func -- repo-committed source, not user data
  const fn = new Function('module', 'exports', 'self', 'window', code);
  fn(mod, mod.exports, undefined, undefined);
  return mod.exports;
}

const TRGM = loadUMD('ui/brain/js/trigram.js');
const fixture = JSON.parse(
  readFileSync(path.join(repoRoot, 'tests/fixtures/pg_trgm_reference.json'), 'utf8')
);

// source: PostgreSQL pg_trgm reference values are exact rationals; 1e-6 covers
// IEEE-754 rounding in the JS reimplementation. Matches the retired harness.
const TOLERANCE = 1e-6;

// source: tasks/todo.md §2 — a single off-thread query scan is not on the
// 16.7 ms Three.js frame budget; 500 ms is a coarse regression guard.
const QUERY_SCAN_BOUND_MS = 500.0;

describe('trigram pg_trgm conformance', () => {
  it('has a non-empty reference fixture', () => {
    expect(Array.isArray(fixture.pairs)).toBe(true);
    expect(fixture.pairs.length).toBeGreaterThan(0);
  });

  // One assertion per fixture pair: the string reference path AND — where both
  // sides reduce to exactly one pg_trgm word — the BigInt packed path (what
  // scoreNode scores with in production) must both match pg_trgm.
  for (const pair of fixture.pairs) {
    it(`similarity(${JSON.stringify(pair.a)}, ${JSON.stringify(pair.b)}) matches pg_trgm`, () => {
      const got = TRGM.similarity(pair.a, pair.b);
      expect(Math.abs(got - pair.similarity)).toBeLessThanOrEqual(TOLERANCE);

      const wa = TRGM.pgWords(pair.a);
      const wb = TRGM.pgWords(pair.b);
      if (wa.length === 1 && wb.length === 1) {
        const packed = TRGM.trigramSimilarityPacked(
          TRGM.wordTrigrams(wa[0]),
          TRGM.wordTrigrams(wb[0])
        );
        expect(Math.abs(packed - pair.similarity)).toBeLessThanOrEqual(TOLERANCE);
      }
    });
  }
});

// The indexing tokenizer's contract, as stated by trigram.js's own comments
// but previously unasserted: mutating either split loop, or widening the
// alnum-run class, left the suite green (Stryker survivors on the #153/#154
// change). These pin what those comments claim.
describe('trigram indexing tokenizer', () => {
  it('treats every non-alphanumeric character as a separator', () => {
    expect(TRGM.indexWords('a/b-c_d')).toEqual(['a', 'b', 'c', 'd']);
    expect(TRGM.indexWords('a/b/file.py::handle')).toEqual([
      'a',
      'b',
      'file',
      'py',
      'handle',
    ]);
  });

  it('yields no words when the string holds no alphanumeric run', () => {
    expect(TRGM.indexWords('')).toEqual([]);
    expect(TRGM.indexWords('---')).toEqual([]);
    expect(TRGM.indexWords('   ')).toEqual([]);
  });

  it('unions both camelCase splits so an acronym stays findable whole', () => {
    // Full split (with the acronym rule) then boundary-only split, deduped in
    // that order. 'userIDs' is the case the union exists for: the acronym rule
    // alone yields 'i'+'ds', which loses 'ids' as a searchable word.
    expect(TRGM.indexWords('userIDs')).toEqual(['user', 'i', 'ds', 'ids']);
    expect(TRGM.indexWords('HTTPServer')).toEqual(['http', 'server', 'httpserver']);
    // Where the two splits agree, dedup collapses them back to one split.
    expect(TRGM.indexWords('fooBar')).toEqual(['foo', 'bar']);
  });
});

// Corpus text is arbitrary: a node can be labelled "constructor" or a path
// segment can be "__proto__". Both dedup maps in the index path (indexWords'
// internal `seen`, and uniqueWords over label+path words) are therefore keyed
// by attacker-or-corpus-controlled strings. Backed by an object they would
// resolve inherited names — `{}['constructor']` is truthy before anything is
// written — and silently drop the word, making the node unfindable. These
// tests fail if either map is ever changed back to an object.
// Origin: CodeQL js/remote-property-injection alerts #153/#154.
describe('trigram dedup does not collide with inherited property names', () => {
  // Names that exist on Object.prototype (or are special to it) AND survive
  // indexWords' alnum-run tokenizer, so they can reach the map as-is.
  const INHERITED = ['constructor', 'valueof', 'tostring', 'hasownproperty', 'isprototypeof'];

  it('indexWords keeps a word that names an inherited property', () => {
    for (const name of INHERITED) {
      expect(TRGM.indexWords(name)).toContain(name);
    }
    // Repeating the word must dedup it, not drop it.
    expect(TRGM.indexWords('constructor constructor')).toEqual(['constructor']);
  });

  it('uniqueWords keeps inherited and dunder names, deduped and in order', () => {
    expect(TRGM.uniqueWords(['constructor', 'constructor', 'node'])).toEqual([
      'constructor',
      'node',
    ]);
    expect(TRGM.uniqueWords(['__proto__', '__proto__'])).toEqual(['__proto__']);
    expect(TRGM.uniqueWords(['prototype', '__proto__', 'constructor', 'tostring'])).toEqual([
      'prototype',
      '__proto__',
      'constructor',
      'tostring',
    ]);
  });

  it('uniqueWords preserves first-occurrence order and is idempotent', () => {
    const input = ['b', 'a', 'b', 'c', 'a', ''];
    const once = TRGM.uniqueWords(input);
    expect(once).toEqual(['b', 'a', 'c', '']);
    expect(TRGM.uniqueWords(once)).toEqual(once);
    expect(TRGM.uniqueWords([])).toEqual([]);
  });

  it('indexing a node labelled "constructor" leaves it findable by search', () => {
    // End-to-end over the same path search_worker.js walks: label -> words ->
    // trigrams -> scoreNode against the query's trigrams.
    const words = TRGM.uniqueWords(TRGM.indexWords('constructor'));
    const nodeTri = words.map(TRGM.wordTrigrams);
    const queryTri = TRGM.indexWords('constructor').map(TRGM.wordTrigrams);
    expect(nodeTri.length).toBeGreaterThan(0);
    expect(TRGM.scoreNode(queryTri, nodeTri)).toBeGreaterThanOrEqual(TRGM.SIMILARITY_THRESHOLD);
  });

  it('does not pollute Object.prototype while deduping hostile keys', () => {
    const before = Object.getOwnPropertyNames(Object.prototype).sort().join(',');
    TRGM.uniqueWords(['__proto__', 'constructor', 'prototype', 'polluted']);
    TRGM.indexWords('__proto__ constructor prototype polluted');
    expect(Object.getOwnPropertyNames(Object.prototype).sort().join(',')).toBe(before);
    expect({}.polluted).toBeUndefined();
    expect(Object.getPrototypeOf({})).toBe(Object.prototype);
  });
});

describe('trigram scale benchmark', () => {
  const PARTS = [
    'get', 'set', 'build', 'parse', 'render', 'fetch', 'graph', 'node', 'edge',
    'brain', 'search', 'worker', 'index', 'trigram', 'similarity', 'score',
    'handler', 'stream', 'domain', 'memory', 'wiki', 'session', 'entity',
    'layout', 'scene', 'camera', 'impact', 'anatomy', 'detail', 'panel',
  ];

  function syntheticLabels(n) {
    const labels = new Array(n);
    for (let i = 0; i < n; i++) {
      const a = PARTS[i % PARTS.length];
      const b = PARTS[(i * 7 + 3) % PARTS.length];
      const c = PARTS[(i * 13 + 5) % PARTS.length];
      labels[i] =
        a + b.charAt(0).toUpperCase() + b.slice(1) + c.charAt(0).toUpperCase() + c.slice(1) + i;
    }
    return labels;
  }

  function buildIndex(nodes) {
    const out = new Array(nodes.length);
    for (let i = 0; i < nodes.length; i++) {
      const words = TRGM.indexWords(nodes[i].label);
      const tri = new Array(words.length);
      for (let w = 0; w < words.length; w++) tri[w] = TRGM.wordTrigrams(words[w]);
      out[i] = { id: nodes[i].id, wordTriLists: tri };
    }
    return out;
  }

  function scan(index, q, limit) {
    const queryTri = TRGM.indexWords(q).map(TRGM.wordTrigrams);
    const scored = [];
    for (let i = 0; i < index.length; i++) {
      const score = TRGM.scoreNode(queryTri, index[i].wordTriLists);
      if (score >= TRGM.SIMILARITY_THRESHOLD) scored.push({ id: index[i].id, score });
    }
    scored.sort((x, y) => y.score - x.score);
    return scored.slice(0, limit);
  }

  it(
    'scans a 300k-label corpus within the regression bound',
    (ctx) => {
      // V8 coverage instrumentation roughly doubles this scan, so the bound is
      // unmeasurable under it. Report the skip; never pass silently.
      if (inject('coverageEnabled')) {
        // eslint-disable-next-line no-console
        console.log('[trigram-bench] SKIPPED: perf bound is invalid under coverage instrumentation');
        ctx.skip();
        return;
      }
      const N = 300000;
      const nodes = syntheticLabels(N).map((label, i) => ({ id: 'n' + i, label }));

      const t0 = performance.now();
      const index = buildIndex(nodes);
      const indexMs = performance.now() - t0;

      const t1 = performance.now();
      const results = scan(index, 'searchNode', 20);
      const queryMs = performance.now() - t1;

      // Reported regardless of pass/fail (measure-and-report, todo.md §2).
      // eslint-disable-next-line no-console
      console.log(
        `[trigram-bench] N=${N} index_build=${indexMs.toFixed(1)}ms ` +
          `query_scan=${queryMs.toFixed(1)}ms results=${results.length}`
      );
      expect(index.length).toBe(N);
      expect(queryMs).toBeLessThan(QUERY_SCAN_BOUND_MS);
    },
    30000
  );
});
