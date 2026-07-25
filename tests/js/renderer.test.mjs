// Force-layout renderer contracts (issue #35 AC#4: force layout / neighbour-set
// computation) for ui/unified/js/renderer.js. Exercises the pure seams the file
// publishes on JUG._rendererTest — the SAME functions the live link styling and
// selection code route through.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

let computeNeighborSet;
let linkTier;

beforeAll(() => {
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  loadScript('ui/unified/js/renderer.js');
  ({ computeNeighborSet, linkTier } = window.JUG._rendererTest);
});

describe('computeNeighborSet', () => {
  const edges = [
    { source: 'a', target: 'b' },
    { source: 'c', target: 'a' },
    { source: 'b', target: 'c' },
    // hydrated-object endpoints (force-graph mutates source/target in place)
    { source: { id: 'a' }, target: { id: 'd' } },
  ];

  it('always includes the node itself', () => {
    expect(computeNeighborSet('a', edges).a).toBe(true);
  });

  it('collects neighbours across both edge directions', () => {
    const set = computeNeighborSet('a', edges);
    expect(set.b).toBe(true); // a -> b (a is source)
    expect(set.c).toBe(true); // c -> a (a is target)
    expect(set.d).toBe(true); // {a} -> {d} (object endpoints)
  });

  it('excludes non-neighbours: an edge touching neither endpoint adds nothing', () => {
    // Kills the "if (sid===nodeId) → true" / "if (tid===nodeId) → true"
    // mutants: an edge {x,w} where neither is the node must not leak x or w.
    const set = computeNeighborSet('a', [
      { source: 'a', target: 'b' },
      { source: 'x', target: 'w' },
    ]);
    expect(set.b).toBe(true);
    expect(set.x).toBeUndefined();
    expect(set.w).toBeUndefined();
  });

  it('adds the target when the node is the SOURCE (directional, both endpoints)', () => {
    // Node as source with object endpoints — kills the line-26 condition and
    // the source-side object/typeof mutants.
    const set = computeNeighborSet('a', [{ source: { id: 'a' }, target: { id: 'p' } }]);
    expect(set.p).toBe(true);
  });

  it('adds the source when the node is the TARGET (directional, both endpoints)', () => {
    // Node as target with object endpoints — kills the line-27 condition and
    // the target-side object/typeof mutants.
    const set = computeNeighborSet('a', [{ source: { id: 'q' }, target: { id: 'a' } }]);
    expect(set.q).toBe(true);
  });

  // Negative assertion (AC#6): empty graph → the set is JUST the node, no
  // spurious neighbours conjured from nothing.
  it('on an empty edge list yields only the node itself', () => {
    const set = computeNeighborSet('a', []);
    expect(Object.keys(set)).toEqual(['a']);
  });

  it('tolerates a missing edge list', () => {
    expect(computeNeighborSet('a', undefined)).toEqual({ a: true });
  });
});

describe('linkTier', () => {
  const edge = { source: 'a', target: 'b' };

  it('is "default" when nothing is focused', () => {
    expect(linkTier(edge, null)).toBe('default');
  });

  it('is "active" when the edge touches the focused node (either endpoint)', () => {
    expect(linkTier(edge, 'a')).toBe('active');
    expect(linkTier(edge, 'b')).toBe('active');
  });

  // Object endpoints, matched via SOURCE only (target does not match) — kills
  // the source-side `typeof ... === 'object'` / 'object' string mutants that a
  // target-side match would otherwise mask.
  it('resolves object endpoints on the SOURCE side', () => {
    expect(linkTier({ source: { id: 'a' }, target: { id: 'b' } }, 'a')).toBe('active');
  });

  // Object endpoints, matched via TARGET only (source does not match) — kills
  // the target-side object/typeof mutants.
  it('resolves object endpoints on the TARGET side', () => {
    expect(linkTier({ source: { id: 'a' }, target: { id: 'b' } }, 'b')).toBe('active');
  });

  // Negative assertion (AC#6): "no accent colour on unselected edges" — an edge
  // that does not touch the focus is dimmed, never active.
  it('is "dimmed" (never active) for edges not touching the focus', () => {
    expect(linkTier({ source: 'x', target: 'y' }, 'a')).toBe('dimmed');
  });
});
