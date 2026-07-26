// LOD aggregation thresholds (issue #35 AC#4: LOD aggregation thresholds) for
// ui/unified/js/workflow_graph.js. wfgLodTier(nodeCount) is the pure decision
// the mount() layout routes through to progressively shed work as the graph
// grows; a silent shift of any bound changes what the user sees at scale.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let lodTier;

beforeAll(() => {
  window.JUG = window.JUG || {};
  globalThis.JUG = window.JUG;
  // issue #41: wfgLodTier moved to the workflow_graph_lod.js sibling module
  // (the pure LOD/edge-coverage seam), still published on JUG._wfg.lodTier.
  loadScript('ui/unified/js/workflow_graph_lod.js');
  lodTier = window.JUG._wfg.lodTier;
});

describe('wfgLodTier — boundaries', () => {
  it('is all-false for a small graph', () => {
    expect(lodTier(0)).toEqual({ heavy: false, snapToSlots: false, extreme: false });
    expect(lodTier(8000)).toEqual({ heavy: false, snapToSlots: false, extreme: false });
  });

  it('enters heavy strictly above 8000', () => {
    expect(lodTier(8001).heavy).toBe(true);
    expect(lodTier(8001).snapToSlots).toBe(false);
    expect(lodTier(8001).extreme).toBe(false);
  });

  it('enters snapToSlots strictly above 15000', () => {
    expect(lodTier(15000).snapToSlots).toBe(false);
    expect(lodTier(15001).snapToSlots).toBe(true);
    expect(lodTier(15001).extreme).toBe(false);
  });

  it('enters extreme strictly above 25000', () => {
    expect(lodTier(25000).extreme).toBe(false);
    expect(lodTier(25001)).toEqual({ heavy: true, snapToSlots: true, extreme: true });
  });
});

describe('wfgLodTier — monotonic containment', () => {
  // Each tier is a strict superset of workload of the previous: extreme implies
  // snapToSlots implies heavy. A regression that reordered the bounds breaks
  // this invariant even if an individual boundary test still passed.
  it('extreme ⇒ snapToSlots ⇒ heavy across a sweep', () => {
    for (const n of [0, 5000, 8001, 12000, 15001, 20000, 25001, 60000]) {
      const t = lodTier(n);
      if (t.extreme) expect(t.snapToSlots).toBe(true);
      if (t.snapToSlots) expect(t.heavy).toBe(true);
    }
  });
});
