import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

afterEach(() => {
  vi.restoreAllMocks();
  delete globalThis.JUG;
});

describe('graph delta enrichment', () => {
  it('forwards a field-changing duplicate to the incremental renderer', () => {
    window.JUG = { setGraphData: vi.fn() };
    globalThis.JUG = window.JUG;
    loadScript('ui/unified/js/state.js');
    const sparse = { id: 'file:deadbeef00', kind: 'file', label: 'x.py' };
    JUG.state.lastData = {
      nodes: [sparse], edges: [], links: [], meta: { schema: 'trace.v1' },
    };
    JUG.__wfgActive = true;
    loadScript('ui/unified/js/graph.js');
    const events = [];
    JUG.on('state:lastData', (event) => events.push(event));

    JUG.appendGraphDelta([
      { id: sparse.id, kind: 'file', path: '/repo/x.py' },
    ], []);

    expect(JUG.state.lastData.nodes).toHaveLength(1);
    expect(JUG.state.lastData.nodes[0].path).toBe('/repo/x.py');
    expect(events.at(-1).delta.nodes).toHaveLength(1);
    expect(events.at(-1).delta.nodes[0]).toBe(JUG.state.lastData.nodes[0]);

    JUG.appendGraphDelta([
      { id: sparse.id, kind: 'file', path: '/other/x.py' },
    ], []);
    expect(JUG.state.lastData.nodes[0].path).toBe('/repo/x.py');
    expect(events.at(-1).delta.nodes).toHaveLength(0);
  });
});
