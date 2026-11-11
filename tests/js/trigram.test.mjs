// @vitest-environment node
//
// pg_trgm conformance for ui/brain/js/trigram.js + the cost of one query in
// ui/brain/js/search_worker.js.
//
// Absorbs the former side-channel harness tests/js/run_trigram_conformance.mjs
// (and its pytest wrapper tests/test_trigram_conformance.py) into the JS suite
// wired to CI (issue #35 AC#3). Two checks:
//   1. similarity(a,b) AND the BigInt-packed path reproduce every pair in
//      tests/fixtures/pg_trgm_reference.json (real PostgreSQL 17.9 pg_trgm
//      output) within float tolerance 1e-6.
//   2. Query cost: the real search_worker.js indexes a synthetic 300k-label
//      corpus and answers one query; the work it does is counted, not timed.
//
// The fixture (tests/fixtures/pg_trgm_reference.json) is ground truth: it was
// generated from PostgreSQL 17.9's real pg_trgm extension (DB cortex, verified
// via pg_extension). trigram.js must match it, never the reverse. Regenerate by
// re-running, against a live pg_trgm Postgres, the query whose VALUES list is
// exactly the fixture's {a,b} pairs:
//     SELECT a, b, similarity(a, b) FROM (VALUES ('http','http'), …) AS pairs(a, b);
import { describe, expect, it } from 'vitest';
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

describe('trigram query cost', () => {
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

  // Load the real worker script against a fake worker global, the way a
  // browser runs it: importScripts() evaluates trigram.js onto `self`, then the
  // worker installs self.onmessage. Returns the fake global and what it posted.
  function loadWorker(mutate) {
    const sources = {
      trigram: readFileSync(path.join(repoRoot, 'ui/brain/js/trigram.js'), 'utf8'),
      worker: readFileSync(path.join(repoRoot, 'ui/brain/js/search_worker.js'), 'utf8'),
    };
    if (mutate) mutate(sources);
    const posted = [];
    const self = { postMessage: (message) => posted.push(message) };
    const importScripts = () => {
      // eslint-disable-next-line no-new-func -- repo-committed source, not user data
      new Function('self', 'module', sources.trigram).call(self, self, undefined);
    };
    // eslint-disable-next-line no-new-func -- repo-committed source, not user data
    new Function('self', 'importScripts', sources.worker)(self, importScripts);
    return { self, posted };
  }

  // Index `count` synthetic labels and run one query through the worker,
  // counting the work instead of timing it. A wall-clock bound measures the
  // scheduler as much as the scan (the former `query < 500 ms` assertion read
  // 157-977 ms for the same code under CPU contention, 2026-11-11); these
  // counters are the same number on every machine and every run.
  //   scoreCalls        TRGM.scoreNode calls during the query
  //   tokenizeCalls     TRGM.indexWords calls during the query
  //   trigramCalls      TRGM.wordTrigrams calls during the query
  //   setsBuilt         `new Set` during the query
  //   queryReads        element reads on the query's trigram lists (opt-in:
  //                     each read goes through a Proxy, so it is slow)
  //   mergeReadCeiling  what a sorted-merge intersection can read at most:
  //                     a pass over lists of m and n trigrams takes at most
  //                     m + n steps and reads the query list at most twice per
  //                     step (`a[i] === b[j]`, then `a[i] < b[j]`), summed over
  //                     every (query token, indexed word) pair
  function countQueryWork(count, { countReads = false, mutate } = {}) {
    const { self, posted } = loadWorker(mutate);
    const T = self.TRGM;
    const real = { wordTrigrams: T.wordTrigrams, indexWords: T.indexWords, scoreNode: T.scoreNode };
    const work = { scoreCalls: 0, tokenizeCalls: 0, trigramCalls: 0, setsBuilt: 0, queryReads: 0 };
    let querying = false;
    let indexedWords = 0;
    let indexedTrigrams = 0;
    let queryTrigrams = 0;
    const isIndex = (key) => typeof key === 'string' && /^\d+$/.test(key);
    T.wordTrigrams = (word) => {
      const list = real.wordTrigrams(word);
      if (!querying) {
        indexedWords += 1;
        indexedTrigrams += list.length;
        return list;
      }
      work.trigramCalls += 1;
      queryTrigrams += list.length;
      if (!countReads) return list;
      return new Proxy(list, {
        get(target, key, receiver) {
          if (isIndex(key)) work.queryReads += 1;
          return Reflect.get(target, key, receiver);
        },
      });
    };
    T.indexWords = (text) => {
      if (querying) work.tokenizeCalls += 1;
      return real.indexWords(text);
    };
    T.scoreNode = (queryTri, nodeTri) => {
      work.scoreCalls += 1;
      return real.scoreNode(queryTri, nodeTri);
    };

    const nodes = syntheticLabels(count).map((label, i) => ({ id: 'n' + i, label }));
    self.onmessage({ data: { type: 'index', nodes } });
    const RealSet = globalThis.Set;
    globalThis.Set = class extends RealSet {
      constructor(...args) {
        super(...args);
        work.setsBuilt += 1;
      }
    };
    querying = true;
    try {
      self.onmessage({ data: { type: 'query', q: 'searchNode', seq: 1, limit: 20 } });
    } finally {
      globalThis.Set = RealSet;
    }
    const [ready, results] = posted;
    const queryTokens = work.trigramCalls;
    const mergeReadCeiling = 2 * (indexedWords * queryTrigrams + queryTokens * indexedTrigrams);
    return { ...work, mergeReadCeiling, ready, results };
  }

  // What the design fixes for one query (tasks/todo.md "Décisions de
  // conception" §2: "Index construit une fois ... Par requête : scan +
  // intersection de listes triées"; trigram.js wordTrigrams: "intersect two
  // words' trigrams in O(m+n) instead of building a hash set per call"), each
  // stated as a count. `empty` is the same query over an empty index: whatever
  // the query costs before it meets a node.
  function expectDesignedQueryCost(count, measured, empty) {
    // One score per node: the scan is a single pass, no node is scored twice.
    expect(measured.scoreCalls).toBe(count);
    // The index is built once: a query tokenizes and packs only its own text.
    expect(measured.tokenizeCalls).toBe(empty.tokenizeCalls);
    expect(measured.trigramCalls).toBe(empty.trigramCalls);
    // No hash set per node or per word pair.
    expect(measured.setsBuilt).toBe(empty.setsBuilt);
  }

  const QUERY_TOKENS = 2; // 'searchNode' -> 'search', 'node'

  it(
    'answers a query over a 300k-label corpus with one merge-scored pass',
    () => {
      const N = 300000;
      const empty = countQueryWork(0);
      const full = countQueryWork(N);

      // Measure-and-report (todo.md §2): the worker's own timings, printed and
      // never asserted.
      // eslint-disable-next-line no-console
      console.log(
        `[trigram-bench] N=${N} index_build=${full.ready.elapsed_ms.toFixed(1)}ms ` +
          `query_scan=${full.results.elapsed_ms.toFixed(1)}ms results=${full.results.total}`
      );
      expect(empty.trigramCalls).toBe(QUERY_TOKENS);
      expect(full.ready.count).toBe(N);
      expect(full.results.total).toBeGreaterThan(0);
      expect(full.results.items).toHaveLength(20);
      expectDesignedQueryCost(N, full, empty);
    },
    30000
  );

  it('intersects trigram lists with a sorted merge, not a nested scan', () => {
    // The ceiling is a property of each (token, word) pair, so it does not
    // need the 300k corpus: 3,000 labels are 100 turns of the 30-part label
    // cycle, 9,000 indexed words against both query tokens.
    const N = 3000;
    const sample = countQueryWork(N, { countReads: true });

    expectDesignedQueryCost(N, sample, countQueryWork(0));
    expect(sample.queryReads).toBeGreaterThan(0);
    expect(sample.queryReads).toBeLessThanOrEqual(sample.mergeReadCeiling);
  });

  // The counters above only protect the design if a slower algorithm moves
  // them. Each case rewrites the shipped source into one such algorithm, runs
  // it through the same counting, and names the counter that catches it. All
  // four still return the right results; only their cost differs.
  describe('a slower algorithm is caught by its count', () => {
    const N = 3000;
    const MERGE = /function trigramIntersectionCount\(a, b\) \{[\s\S]*?return inter;\n {2}\}/;

    function rewrite(file, pattern, replacement) {
      return (sources) => {
        const before = sources[file];
        sources[file] = before.replace(pattern, replacement);
        if (sources[file] === before) throw new Error(`rewrite of ${file} matched nothing`);
      };
    }

    function measure(mutate) {
      const reference = countQueryWork(N, { countReads: true });
      const slow = countQueryWork(N, { countReads: true, mutate });
      expect(slow.results.total).toBe(reference.results.total);
      expect(slow.results.items).toEqual(reference.results.items);
      return { slow, empty: countQueryWork(0, { mutate }) };
    }

    it('a nested scan in place of the sorted merge reads past the merge ceiling', () => {
      const { slow } = measure(rewrite('trigram', MERGE,
        'function trigramIntersectionCount(a, b) { var inter = 0;'
        + ' for (var i = 0; i < a.length; i++) for (var j = 0; j < b.length; j++)'
        + ' if (a[i] === b[j]) inter++; return inter; }'));
      expect(slow.queryReads).toBeGreaterThan(slow.mergeReadCeiling);
    });

    it('a hash set per word pair builds sets in proportion to the corpus', () => {
      const { slow, empty } = measure(rewrite('trigram', MERGE,
        'function trigramIntersectionCount(a, b) { var s = new Set(b); var inter = 0;'
        + ' for (var i = 0; i < a.length; i++) if (s.has(a[i])) inter++; return inter; }'));
      expect(slow.setsBuilt).toBeGreaterThan(empty.setsBuilt + N);
    });

    it('packing node trigrams at query time packs once per indexed word', () => {
      const { slow, empty } = measure((sources) => {
        rewrite('worker', 'tri[w] = TRGM.wordTrigrams(words[w]);', 'tri[w] = words[w];')(sources);
        rewrite('worker', 'TRGM.scoreNode(queryTri, node.wordTriLists)',
          'TRGM.scoreNode(queryTri, node.wordTriLists.map(TRGM.wordTrigrams))')(sources);
      });
      expect(slow.trigramCalls).toBeGreaterThan(empty.trigramCalls + N);
    });

    it('rescanning the index for each hit scores nodes more than once', () => {
      const { slow } = measure(rewrite('worker', 'path: node.path, score: score });',
        'path: node.path, score: index.reduce(function (sum, other) {'
        + ' return other.id === node.id ? sum + TRGM.scoreNode(queryTri, other.wordTriLists) : sum;'
        + ' }, 0) });'));
      expect(slow.scoreCalls).toBe(N + slow.results.total);
    });
  });
});
