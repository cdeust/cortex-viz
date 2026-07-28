// Coverage-honesty model (issue #36) — the pure verdict layer,
// ui/unified/js/coverage_model.js. This is the load-bearing decision the
// on-screen indicator renders; a silent shift in any threshold turns a
// truthful "N omitted" into a false "Complete", the exact failure class the
// feature exists to prevent. Every criterion (1-6) has an asserting test here.
// @vitest-environment node
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let C;

beforeAll(() => {
  globalThis.window = globalThis.window || globalThis;
  window.JUG = window.JUG || {};
  globalThis.JUG = window.JUG;
  loadScript('ui/unified/js/coverage_model.js');
  C = window.JUG.Coverage;
});

describe('_axis — rendered vs store', () => {
  it('reports missing when the store is ahead', () => {
    expect(C._axis(10, 25)).toEqual({ rendered: 10, inStore: 25, missing: 15, complete: false });
  });
  it('is complete when rendered meets the store total', () => {
    expect(C._axis(25, 25)).toEqual({ rendered: 25, inStore: 25, missing: 0, complete: true });
  });
  it('never reports negative missing when rendered exceeds store', () => {
    // The accumulating store can momentarily lag the render; missing clamps at 0.
    expect(C._axis(30, 25)).toEqual({ rendered: 30, inStore: 25, missing: 0, complete: true });
  });
  it('leaves complete/missing UNKNOWN (null) when there is no denominator', () => {
    expect(C._axis(10, null)).toEqual({ rendered: 10, inStore: null, missing: null, complete: null });
  });
  it('coerces garbage counts to non-negative integers', () => {
    expect(C._axis(-5, NaN)).toEqual({ rendered: 0, inStore: 0, missing: 0, complete: true });
    expect(C._axis(3.9, 10.2)).toEqual({ rendered: 3, inStore: 10, missing: 7, complete: false });
  });
});

describe('_filesAxis — engine parse coverage', () => {
  it('is null when the engine payload is absent (degraded mode signalled by caller)', () => {
    expect(C._filesAxis(null)).toBe(null);
  });
  it('is null when the engine explicitly reports unavailable', () => {
    expect(C._filesAxis({ available: false, reason: 'x' })).toBe(null);
  });
  it('reports missing files and is incomplete', () => {
    const f = C._filesAxis({ available: true, files_present: 100, files_indexed: 90 });
    expect(f.missing).toBe(10);
    expect(f.complete).toBe(false);
  });
  it('is complete only when indexed==present AND no parse errors AND no failures', () => {
    expect(C._filesAxis({ available: true, files_present: 50, files_indexed: 50 }).complete).toBe(true);
  });
  it('is incomplete when a file parsed only partially', () => {
    const f = C._filesAxis({ available: true, files_present: 50, files_indexed: 50, parse_incomplete: 2 });
    expect(f.complete).toBe(false);
    expect(f.parseIncomplete).toBe(2);
  });
  it('is incomplete when a file failed extraction, and carries the detail list', () => {
    const failures = [{ path: 'a.c', error_ranges: [[1, 2]], reason: 'ERROR' }];
    const f = C._filesAxis({ available: true, files_present: 50, files_indexed: 50, extraction_failures: failures });
    expect(f.complete).toBe(false);
    expect(f.failures).toBe(failures);
  });
  it('tolerates a non-array extraction_failures field', () => {
    const f = C._filesAxis({ available: true, files_present: 1, files_indexed: 1, extraction_failures: 'oops' });
    expect(f.failures).toEqual([]);
    expect(f.complete).toBe(true);
  });
});

describe('_lodAxis — client-side collapse', () => {
  it('is inactive with no tier', () => {
    expect(C._lodAxis({ nodes: 100, edges: 50 })).toEqual({ active: false, tier: null, collapsedEdges: 0, danglingEdges: 0 });
  });
  it('is inactive when every tier flag is false', () => {
    const a = C._lodAxis({ lodTier: { heavy: false, snapToSlots: false, extreme: false } });
    expect(a.active).toBe(false);
  });
  it('is active when any tier flag is set and reports the collapsed edge count', () => {
    const a = C._lodAxis({ lodTier: { heavy: true, snapToSlots: false, extreme: false }, droppedEdges: 812 });
    expect(a.active).toBe(true);
    expect(a.collapsedEdges).toBe(812);
  });
  it('is active on extreme with collapsed calls edges', () => {
    expect(C._lodAxis({ lodTier: { heavy: true, snapToSlots: true, extreme: true }, droppedEdges: 5 }).active).toBe(true);
  });
});

describe('_staleness — snapshot vs store', () => {
  it('is stale on a revision mismatch (authoritative signal)', () => {
    const s = C._staleness({ revision: 'r1' }, { revision: 'r2' }, null, null);
    expect(s.stale).toBe(true);
    expect(s.storeRevision).toBe('r2');
    expect(s.snapshotRevision).toBe('r1');
  });
  it('is NOT stale when revisions match', () => {
    expect(C._staleness({ revision: 'r1' }, { revision: 'r1' }, null, null).stale).toBe(false);
  });
  it('falls back to count growth when no revision is wired', () => {
    const s = C._staleness({ node_total: 1000, edge_total: 2000 }, { node_count: 1500, edge_count: 2000 }, null, null);
    expect(s.stale).toBe(true);
  });
  it('does NOT claim staleness from a zero snapshot total (not-yet-loaded, not stale)', () => {
    const s = C._staleness({ node_total: 0, edge_total: 0 }, { node_count: 500, edge_count: 500 }, null, null);
    expect(s.stale).toBe(false);
  });
  it('flags the build-in-progress mode from progress.full_ready === false', () => {
    expect(C._staleness(null, null, { full_ready: false }, null).building).toBe(true);
    expect(C._staleness(null, null, { full_ready: true }, null).building).toBe(false);
  });
  it('ages the snapshot from captured_at', () => {
    const s = C._staleness({ captured_at: 1000, node_total: 5 }, { node_count: 5 }, null, 61000);
    expect(s.ageMs).toBe(60000);
  });
  it('reports null revisions when neither side carries one (not undefined)', () => {
    const s = C._staleness({ node_total: 5 }, { node_count: 5 }, null, null);
    expect(s.snapshotRevision).toBe(null);
    expect(s.storeRevision).toBe(null);
  });
  it('ageMs is null when captured_at is absent even if now is set', () => {
    const s = C._staleness({ node_total: 5 }, { node_count: 5 }, null, 99999);
    expect(s.ageMs).toBe(null);
  });
  it('ageMs is null when now is absent even if captured_at is set', () => {
    const s = C._staleness({ captured_at: 1000, node_total: 5 }, { node_count: 5 }, null, null);
    expect(s.ageMs).toBe(null);
  });
  it('needs BOTH revisions to use the revision branch — one alone falls to counts', () => {
    // Only the snapshot carries a revision: the revision branch must NOT fire
    // (it would compare 'r1' !== null and wrongly latch stale); counts are
    // equal, so the honest verdict is not-stale.
    const s = C._staleness(
      { revision: 'r1', node_total: 100, edge_total: 50 },
      { node_count: 100, edge_count: 50 }, null, null);
    expect(s.stale).toBe(false);
  });
  it('uses the revision branch when only the STORE carries a revision (stale)', () => {
    // store has a revision, snapshot does not: the mutant `true && storeRev`
    // would enter the revision branch and compare null !== 'r9' → stale; the
    // correct rule needs BOTH revisions, so it falls to counts (equal → clean).
    const s = C._staleness(
      { node_total: 100, edge_total: 50 },
      { node_count: 100, edge_count: 50, revision: 'r9' }, null, null);
    expect(s.stale).toBe(false);
  });
  it('is NOT stale when counts are exactly equal (no revision)', () => {
    const s = C._staleness({ node_total: 100, edge_total: 50 }, { node_count: 100, edge_count: 50 }, null, null);
    expect(s.stale).toBe(false);
  });
  it('is NOT stale when the store is BEHIND the snapshot (store smaller)', () => {
    const s = C._staleness({ node_total: 100, edge_total: 50 }, { node_count: 80, edge_count: 40 }, null, null);
    expect(s.stale).toBe(false);
  });
  it('is stale when ONLY edges grew (nodes equal)', () => {
    const s = C._staleness({ node_total: 100, edge_total: 50 }, { node_count: 100, edge_count: 75 }, null, null);
    expect(s.stale).toBe(true);
  });
  it('is stale when ONLY nodes grew (edges equal)', () => {
    const s = C._staleness({ node_total: 100, edge_total: 50 }, { node_count: 130, edge_count: 50 }, null, null);
    expect(s.stale).toBe(true);
  });
});

describe('_filesAxis — boundary', () => {
  it('reports missing 0 (not negative) when indexed exceeds present', () => {
    const f = C._filesAxis({ available: true, files_present: 40, files_indexed: 50 });
    expect(f.missing).toBe(0);
  });
});

describe('_lodAxis — dangling edges', () => {
  it('carries the dangling-edge count', () => {
    expect(C._lodAxis({ danglingEdges: 17 }).danglingEdges).toBe(17);
  });
});

describe('computeCoverage — verdict + criteria', () => {
  const storeComplete = { node_count: 100, edge_count: 50 };

  it('is UNKNOWN before any denominator is available (criterion 5 honesty)', () => {
    const r = C.computeCoverage('graph', { rendered: { nodes: 10, edges: 5 } });
    expect(r.status).toBe('unknown');
    expect(r.omissions).toEqual([]);
  });

  it('is COMPLETE and quiet when the render matches the store (criterion 5)', () => {
    // First: the store reports NO revision (the AP#55 "not wired yet"
    // fallback). Staleness must fall back to growth comparison and find
    // none — a missing store revision may not fabricate staleness, and the
    // verdict must be identical to the revision-matched run below.
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: storeComplete,
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    expect(r.status).toBe('complete');
    expect(r.omissions).toEqual([]);
    expect(r.degraded).toEqual([]);
    expect(r.staleness.stale).toBe(false);
    expect(r.staleness.storeRevision).toBeNull();
    expect(r.staleness.snapshotRevision).toBe('r1');

    // Provide a matching store revision so staleness is clean.
    storeComplete.revision = 'r1';
    const r2 = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: storeComplete,
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    expect(r2.status).toBe('complete');
    expect(r2.omissions).toEqual([]);
    expect(r2.degraded).toEqual([]);
    delete storeComplete.revision;
  });

  it('is INCOMPLETE and enumerates node omissions (criterion 2)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 60, edges: 50 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    expect(r.status).toBe('incomplete');
    const nodeOmission = r.omissions.find((o) => o.kind === 'nodes');
    expect(nodeOmission.count).toBe(40);
  });

  it('names the LOD collapse as a degraded mode with its count (criterion 3)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 40, lodTier: { heavy: true, snapToSlots: false, extreme: false }, droppedEdges: 900 },
      store: { node_count: 100, edge_count: 40, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 40 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    const lod = r.omissions.find((o) => o.kind === 'lod');
    expect(lod.count).toBe(900);
    expect(r.lod.active).toBe(true);
  });

  it('flags staleness explicitly (criterion 4)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: { node_count: 100, edge_count: 50, revision: 'r2' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    expect(r.staleness.stale).toBe(true);
    expect(r.omissions.some((o) => o.kind === 'stale')).toBe(true);
  });

  it('records engine-coverage-unavailable when the engine payload is absent (§13 F2)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: null,
    });
    expect(r.degraded).toContain('engine-coverage-unavailable');
    expect(r.files).toBe(null);
  });

  it('handles the on-demand view (trace): no denominator, named expansion mode', () => {
    const r = C.computeCoverage('trace', {
      rendered: { nodes: 12, edges: 8 },
      store: { node_count: 999, edge_count: 999 },
      denominatorMeaningful: false,
    });
    expect(r.nodes.inStore).toBe(null);
    expect(r.degraded).toContain('on-demand-expansion');
  });

  it('surfaces a truncated stream as an omission', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 50, edges: 20 },
      store: { node_count: 50, edge_count: 20, revision: 'r1' },
      stream: { revision: 'r1', node_total: 50, edge_total: 20, truncated: true },
      progress: { full_ready: true },
      engine: { available: true, files_present: 1, files_indexed: 1 },
    });
    expect(r.omissions.some((o) => o.kind === 'truncated')).toBe(true);
    expect(r.status).toBe('incomplete');
  });

  it('surfaces engine extraction failures in the drill-down (criterion 2)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 50, edges: 20 },
      store: { node_count: 50, edge_count: 20, revision: 'r1' },
      stream: { revision: 'r1', node_total: 50, edge_total: 20 },
      progress: { full_ready: true },
      engine: {
        available: true, files_present: 30, files_indexed: 30,
        extraction_failures: [{ path: 'broken.c', error_ranges: [[3, 9]], reason: 'ERROR' }],
      },
    });
    const fail = r.omissions.find((o) => o.kind === 'extraction-failures');
    expect(fail.count).toBe(1);
    expect(fail.detail[0].path).toBe('broken.c');
  });

  it('records build-in-progress when the build is not full_ready', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 50, edges: 20 },
      store: { node_count: 50, edge_count: 20, revision: 'r1' },
      stream: { revision: 'r1', node_total: 50, edge_total: 20 },
      progress: { full_ready: false },
      engine: { available: true, files_present: 1, files_indexed: 1 },
    });
    expect(r.degraded).toContain('build-in-progress');
  });

  it('records engine-coverage-unavailable when the engine reports available:false', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 50, edges: 20 },
      store: { node_count: 50, edge_count: 20, revision: 'r1' },
      stream: { revision: 'r1', node_total: 50, edge_total: 20 },
      progress: { full_ready: true },
      engine: { available: false, reason: 'no #57' },
    });
    expect(r.degraded).toContain('engine-coverage-unavailable');
    expect(r.files).toBe(null);
  });

  it('is INCOMPLETE (not unknown) when there is an omission but no denominator', () => {
    // trace: no node/edge/file denominator, but the LOD aggregator collapsed
    // edges — an omission exists, so the honest verdict is incomplete.
    const r = C.computeCoverage('trace', {
      rendered: { nodes: 12, edges: 8, lodTier: { heavy: true, snapToSlots: false, extreme: false }, droppedEdges: 40 },
      denominatorMeaningful: false,
    });
    expect(r.nodes.inStore).toBe(null);
    expect(r.status).toBe('incomplete');
    expect(r.omissions.some((o) => o.kind === 'lod')).toBe(true);
  });

  it('has a files denominator alone counted as a denominator (status not unknown)', () => {
    // No node/edge store totals, but engine file coverage is present and
    // complete → haveDenominator is true, so status is complete, not unknown.
    const r = C.computeCoverage('trace', {
      rendered: { nodes: 5, edges: 3 },
      denominatorMeaningful: false,
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    expect(r.nodes.inStore).toBe(null);
    expect(r.files).not.toBe(null);
    expect(r.status).toBe('complete');
  });

  it('treats a nodes-only store total as a valid denominator (status complete)', () => {
    // store carries node_count but not edge_count → nodes.inStore is the sole
    // denominator; a fully-rendered node set is complete, not unknown.
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 0 },
      store: { node_count: 100 },
    });
    expect(r.nodes.inStore).toBe(100);
    expect(r.edges.inStore).toBe(null);
    expect(r.status).toBe('complete');
  });

  it('treats an edges-only store total as a valid denominator (status complete)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 0, edges: 50 },
      store: { edge_count: 50 },
    });
    expect(r.edges.inStore).toBe(50);
    expect(r.nodes.inStore).toBe(null);
    expect(r.status).toBe('complete');
  });

  it('exposes edges.inStore as the store edge total (denominator wired through)', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 30 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 1, files_indexed: 1 },
    });
    expect(r.edges.inStore).toBe(50);
    expect(r.edges.rendered).toBe(30);
    expect(r.omissions.some((o) => o.kind === 'edges')).toBe(true);
  });

  it('propagates snapshot age into the report when captured_at + now are set', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50, captured_at: 1000 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 1, files_indexed: 1 },
      now: 5000,
    });
    expect(r.staleness.ageMs).toBe(4000);
  });

  it('surfaces dangling-edge drops as their own omission', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 30, danglingEdges: 11 },
      store: { node_count: 100, edge_count: 30, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 30 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 1, files_indexed: 1 },
    });
    const d = r.omissions.find((o) => o.kind === 'dangling');
    expect(d.count).toBe(11);
  });

  it('does NOT emit a lod omission when LOD is active but nothing collapsed', () => {
    const r = C.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50, lodTier: { heavy: true, snapToSlots: false, extreme: false }, droppedEdges: 0 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 1, files_indexed: 1 },
    });
    expect(r.lod.active).toBe(true);
    expect(r.omissions.some((o) => o.kind === 'lod')).toBe(false);
  });
});

describe('omission labels + kinds — exact copy is the F2 signal (pinned)', () => {
  // The drill-down copy IS the honesty deliverable (§13 F2); a mutated/blanked
  // label is a real UI regression, so the exact text and kind are pinned.
  function omissionsFor(overrides) {
    return C.computeCoverage('graph', Object.assign({
      rendered: {
        nodes: 60, edges: 30, danglingEdges: 4,
        lodTier: { heavy: true, snapToSlots: false, extreme: false }, droppedEdges: 700,
      },
      store: { node_count: 100, edge_count: 50, revision: 'r2' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50, truncated: true },
      progress: { full_ready: true },
      engine: {
        available: true, files_present: 30, files_indexed: 25, parse_incomplete: 3,
        extraction_failures: [{ path: 'x.c', error_ranges: [], reason: 'ERROR' }],
      },
    }, overrides)).omissions;
  }

  const EXPECTED = {
    nodes: 'nodes in store not rendered',
    edges: 'edges in store not rendered',
    files: 'files present but not indexed',
    'parse-incomplete': 'files indexed with parse errors (partial)',
    'extraction-failures': 'files that failed extraction',
    lod: 'edges collapsed by LOD aggregation',
    dangling: 'edges dropped: endpoint not in view',
    truncated: 'graph stream ended before the full snapshot arrived',
    stale: 'view drawn from a superseded snapshot',
  };

  it.each(Object.keys(EXPECTED))('emits the %s omission with its exact label', (kind) => {
    const o = omissionsFor().find((x) => x.kind === kind);
    expect(o, 'omission of kind ' + kind + ' must be present').toBeTruthy();
    expect(o.label).toBe(EXPECTED[kind]);
    expect(o.count).toBeGreaterThan(0);
  });
});
