// ui/unified/js/spatial_hash.js — the uniform-grid hit-test index (issue #35).
//
// This file states its own correctness invariant in its header: a 3x3 cell
// neighborhood contains every possible hit, PROVIDED `cell` exceeds the largest
// node radius. That is a real claim with a real precondition, and nothing was
// checking either. A regression here does not throw — it silently returns the
// wrong node under the cursor, or none, which reads as "the graph is laggy" or
// "clicking does nothing" rather than as a bug.
import { describe, it, expect } from "vitest";
import { createRequire } from "node:module";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const require = createRequire(import.meta.url);
const { SpatialHash } = require(resolve(REPO_ROOT, "ui/unified/js/spatial_hash.js"));

/** Deterministic LCG — a seeded generator keeps a failure reproducible. */
function lcg(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function makeNodes(count, seed, spread = 4000) {
  const rnd = lcg(seed);
  return Array.from({ length: count }, () => ({
    x: (rnd() - 0.5) * spread,
    y: (rnd() - 0.5) * spread,
  }));
}

/** The O(N) scan the spatial hash replaced: every node within `r` of (x,y). */
function bruteForceHits(nodes, x, y, r) {
  const out = [];
  for (let i = 0; i < nodes.length; i++) {
    const dx = nodes[i].x - x;
    const dy = nodes[i].y - y;
    if (dx * dx + dy * dy <= r * r) out.push(i);
  }
  return out;
}

describe("SpatialHash — the 3x3 sufficiency invariant", () => {
  it("never misses a hit the O(N) scan would find (1000 nodes x 400 probes)", () => {
    // MAX_RADIUS mirrors the header: nodeRadius() tops out at 26, +6 bump,
    // +2 pad = 34 world units. cell=200 satisfies cell > max radius.
    const MAX_RADIUS = 34;
    const nodes = makeNodes(1000, 12345);
    const hash = new SpatialHash(200).build(nodes);

    const rnd = lcg(999);
    let probesWithHits = 0;
    for (let p = 0; p < 400; p++) {
      const x = (rnd() - 0.5) * 4000;
      const y = (rnd() - 0.5) * 4000;
      const expected = bruteForceHits(nodes, x, y, MAX_RADIUS);
      if (expected.length) probesWithHits++;
      const candidates = new Set(hash.queryNeighborhood(x, y));
      for (const idx of expected) {
        expect(
          candidates.has(idx),
          `node ${idx} at (${nodes[idx].x}, ${nodes[idx].y}) is within ${MAX_RADIUS} of ` +
            `probe (${x}, ${y}) but was absent from the 3x3 neighborhood`,
        ).toBe(true);
      }
    }
    // Guard the guard: a probe set that never hits anything would pass the
    // loop above vacuously and prove nothing.
    expect(probesWithHits).toBeGreaterThan(0);
  });

  it("is a filter, not an oracle: candidates may exceed true hits", () => {
    // The contract is a superset — the caller still runs the precise circle
    // test. Asserting equality here would encode a promise the module does
    // not make, and would fail for correct implementations.
    const nodes = makeNodes(300, 7);
    const hash = new SpatialHash(200).build(nodes);
    const candidates = hash.queryNeighborhood(0, 0);
    const truth = bruteForceHits(nodes, 0, 0, 34);
    expect(candidates.length).toBeGreaterThanOrEqual(truth.length);
  });

  it("PRECONDITION: a cell smaller than the max radius breaks 3x3 sufficiency", () => {
    // The header warns "keep cell > max node radius". This pins WHY: with a
    // tiny cell, a node inside the radius can sit more than one cell away, so
    // the 3x3 window no longer contains it. Documents the failure mode rather
    // than leaving the warning unverified.
    const nodes = [{ x: 0, y: 0 }];
    const tiny = new SpatialHash(5).build(nodes);
    // (34, 0) is within radius 34 of the node, but 6 cells away at cell=5.
    expect(bruteForceHits(nodes, 34, 0, 34)).toEqual([0]);
    expect(tiny.queryNeighborhood(34, 0)).not.toContain(0);
  });
});

describe("SpatialHash — build filtering", () => {
  it("skips nodes with absent or non-finite coordinates", () => {
    const nodes = [
      { x: 0, y: 0 }, // 0 keep
      { x: null, y: 0 }, // 1 drop
      { x: 0 }, // 2 drop (y undefined)
      { x: NaN, y: 0 }, // 3 drop
      { x: Infinity, y: 0 }, // 4 drop
      { x: 10, y: 10 }, // 5 keep
    ];
    const hash = new SpatialHash(200).build(nodes);
    const got = hash.queryNeighborhood(0, 0).sort((a, b) => a - b);
    expect(got).toEqual([0, 5]);
  });

  it("preserves draw order, so the caller's topmost-wins tiebreak holds", () => {
    // Indices are stored, not nodes, and a higher index was drawn later. If
    // build() ever reordered within a bucket, the topmost node would stop
    // winning the hit test and selection would pick a node underneath.
    const nodes = [
      { x: 1, y: 1 },
      { x: 2, y: 2 },
      { x: 3, y: 3 },
    ];
    const hash = new SpatialHash(200).build(nodes);
    expect(hash.queryNeighborhood(2, 2)).toEqual([0, 1, 2]);
  });

  it("rebuild clears the previous contents", () => {
    const hash = new SpatialHash(200);
    hash.build([{ x: 0, y: 0 }]);
    hash.build([{ x: 1000, y: 1000 }]);
    expect(hash.queryNeighborhood(0, 0)).toEqual([]);
    expect(hash.queryNeighborhood(1000, 1000)).toEqual([0]);
  });

  it("an empty graph yields no candidates and does not throw", () => {
    const hash = new SpatialHash(200).build([]);
    expect(hash.queryNeighborhood(0, 0)).toEqual([]);
  });

  it("defaults to cell=200 when constructed without a size", () => {
    expect(new SpatialHash().cell).toBe(200);
    expect(new SpatialHash(0).cell).toBe(200); // 0 is falsy -> default, not a divide-by-zero
  });
});
