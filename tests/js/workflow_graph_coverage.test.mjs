// wfgEdgeCoverage (issue #36) — the pure edge-reduction + coverage accounting
// in ui/unified/js/workflow_graph.js that feeds JUG.__wfgRendered.droppedEdges
// / danglingEdges, which the coverage indicator DISPLAYS (criterion 3). The
// exhaustiveness invariant (droppedEdges + danglingEdges + rendered === input)
// is what guarantees no edge is silently unaccounted for.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let edgeCoverage;

beforeAll(() => {
  window.JUG = window.JUG || {};
  globalThis.JUG = window.JUG;
  // issue #41: wfgEdgeCoverage moved to the workflow_graph_lod.js sibling
  // module (the pure LOD/edge-coverage seam), still on JUG._wfg.edgeCoverage.
  loadScript('ui/unified/js/workflow_graph_lod.js');
  edgeCoverage = window.JUG._wfg.edgeCoverage;
});

function idSet(ids) {
  const s = {};
  ids.forEach((i) => (s[i] = 1));
  return s;
}

describe('wfgEdgeCoverage — non-extreme (no LOD calls-drop)', () => {
  it('renders every edge whose endpoints exist; drops none', () => {
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }];
    const cov = edgeCoverage(edges, idSet(['a', 'b', 'c']), false);
    expect(cov.rendered.length).toBe(2);
    expect(cov.droppedEdges).toBe(0);
    expect(cov.danglingEdges).toBe(0);
  });

  it('counts dangling edges (missing endpoint) separately from LOD', () => {
    const edges = [{ source: 'a', target: 'b' }, { source: 'a', target: 'GONE' }];
    const cov = edgeCoverage(edges, idSet(['a', 'b']), false);
    expect(cov.rendered.length).toBe(1);
    expect(cov.droppedEdges).toBe(0);
    expect(cov.danglingEdges).toBe(1);
  });

  it('resolves object-form endpoints ({id}) as well as raw ids', () => {
    const edges = [{ source: { id: 'a' }, target: { id: 'b' } }];
    const cov = edgeCoverage(edges, idSet(['a', 'b']), false);
    expect(cov.rendered.length).toBe(1);
    expect(cov.danglingEdges).toBe(0);
  });
});

describe('wfgEdgeCoverage — extreme (LOD sheds `calls`)', () => {
  it('drops calls edges as LOD, keeps the rest, and the drop is NOT counted as dangling', () => {
    const edges = [
      { source: 'a', target: 'b', kind: 'calls' },
      { source: 'a', target: 'b', kind: 'defined_in' },
    ];
    const cov = edgeCoverage(edges, idSet(['a', 'b']), true);
    expect(cov.rendered.length).toBe(1);
    // The SURVIVING edge is the non-calls one — pins that `calls` (not its
    // complement) is what the extreme tier sheds.
    expect(cov.rendered[0].kind).toBe('defined_in');
    expect(cov.droppedEdges).toBe(1);
    expect(cov.danglingEdges).toBe(0);
  });

  it('keeps calls edges when NOT extreme (the tier gate is load-bearing)', () => {
    const edges = [{ source: 'a', target: 'b', kind: 'calls' }];
    const cov = edgeCoverage(edges, idSet(['a', 'b']), false);
    expect(cov.droppedEdges).toBe(0);
    expect(cov.rendered.length).toBe(1);
  });
});

describe('wfgEdgeCoverage — exhaustiveness invariant', () => {
  it('drop + dangling + rendered === input across a mixed set', () => {
    const edges = [
      { source: 'a', target: 'b', kind: 'calls' },     // LOD-dropped under extreme
      { source: 'a', target: 'GONE', kind: 'calls' },  // dropped by LOD first
      { source: 'a', target: 'GONE2', kind: 'defined_in' }, // dangling
      { source: 'a', target: 'b', kind: 'defined_in' },     // rendered
    ];
    const cov = edgeCoverage(edges, idSet(['a', 'b']), true);
    expect(cov.droppedEdges + cov.danglingEdges + cov.rendered.length).toBe(edges.length);
  });

  it('is empty-safe (no edges, no nodes)', () => {
    const cov = edgeCoverage([], {}, false);
    expect(cov).toEqual({ rendered: [], droppedEdges: 0, danglingEdges: 0 });
    // null input coerces to [] — the accounting must be all-zero, not a
    // sentinel-filled array (kills the `|| ["…"]` default mutant).
    const cov2 = edgeCoverage(null, {}, true);
    expect(cov2).toEqual({ rendered: [], droppedEdges: 0, danglingEdges: 0 });
  });
});
