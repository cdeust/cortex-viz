// Behaviour tests for ui/unified/js/config.js (token hydration, label cleaning)
// and ui/unified/js/graph.js (filtering, edge map, expand, delta append).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

describe('config.js with a palette', () => {
  beforeEach(() => {
    delete window.JUG;
    window.CortexSurface = { EVENT: 'cortex:surface-change' };
    let n = 0;
    window.CortexPalette = {
      hex: vi.fn((name) => (name === '--border-strong' ? '#101010' : '#a0000' + (n++ % 10))),
      stages: () => ({ labile: '#aa0000', 'early-ltp': '#bb0000', consolidated: '' }),
    };
    loadScript('ui/unified/js/config.js');
  });
  afterEach(() => {
    delete window.CortexPalette;
    delete window.CortexSurface;
  });

  it('hydrates tokens, edge colours and consolidation colours from the palette', () => {
    const J = window.JUG;
    expect(J._tok.hub).toMatch(/^#a0000/);
    expect(J.EDGE_COLORS.calls).toBe('#101010');
    expect(J.getEdgeColor({ type: 'calls' })).toBe('#101010');
    expect(J.getEdgeColor({ type: 'unknown-xyz' })).toBe('#101010');
    expect(J.CONSOLIDATION_COLORS.labile).toBe('#aa0000');
    expect(J.CONSOLIDATION_COLORS.early_ltp).toBe('#bb0000');
    // empty stage token keeps the literal fallback
    expect(J.CONSOLIDATION_COLORS.consolidated).toBe('#E8B840');
  });

  it('re-hydrates on cortex:surface-change', () => {
    const J = window.JUG;
    const before = J._tok.hub;
    window.CortexPalette.hex = () => '#123456';
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(J._tok.hub).toBe('#123456');
    expect(J._tok.hub).not.toBe(before);
    expect(J.EDGE_COLORS.imports).toBe('#123456');
  });

  it('keeps fallbacks when the palette returns empty values', () => {
    delete window.JUG;
    window.CortexPalette = { hex: () => '', stages: () => ({}) };
    loadScript('ui/unified/js/config.js');
    expect(window.JUG._tok.hub).toBe('#8D6D00');
    expect(window.JUG.EDGE_COLORS.calls).toBe('#5A5A5A');
    expect(window.JUG.CONSOLIDATION_COLORS.late_ltp).toBe('#40D870');
  });
});

describe('config.js label helpers', () => {
  let J;
  beforeEach(() => {
    delete window.JUG;
    loadScript('ui/unified/js/config.js');
    J = window.JUG;
  });

  it('getNodeColor falls back to info for null', () => {
    expect(J.getNodeColor(null)).toBe(J._tok.info);
  });

  it('cleanText strips markdown and collapses whitespace', () => {
    expect(J.cleanText('')).toBe('');
    expect(J.cleanText('## **bold**   and `code`\n text')).toBe('bold and code text');
  });

  it('cleans tool captures into short labels', () => {
    expect(J.cleanText('Tool: Edit File: /a/b/c/file.py Output: x')).toBe('Edit file.py');
    expect(J.cleanText('Tool: Write File: /a/b/new.ts')).toBe('Write new.ts');
    expect(J.cleanText('Tool: Read File: /a/b/r.md')).toBe('Read r.md');
    expect(J.cleanText('Tool: Bash Command: ls -la Output: stuff')).toBe('ls -la');
    expect(J.cleanText('Tool: Bash Command: ' + 'x'.repeat(50))).toBe('x'.repeat(33) + '...');
    expect(J.cleanText('Tool: WebSearch query')).toBe('Web search');
    expect(J.cleanText('Tool: Grep pattern')).toBe('Search files');
    expect(J.cleanText('File: /deep/path/thing.js')).toBe('thing.js');
    expect(J.cleanText('plain text')).toBe('plain text');
  });

  it('extractFilePath handles absolute, windows, relative and none', () => {
    expect(J._extractFilePath('see "/a/b.py" ok')).toBe('/a/b.py');
    expect(J._extractFilePath('C:\\dir\\f.txt now')).toBe('C:\\dir\\f.txt');
    expect(J._extractFilePath('nothing')).toBe('');
  });

  it('shortFile handles empty, single and nested paths', () => {
    expect(J._shortFile('')).toBe('');
    expect(J._shortFile('"name.py"')).toBe('name.py');
    expect(J._shortFile('/a/b/c.py')).toBe('c.py');
  });

  it('bestNodeLabel prefers a clean label, then content, then id', () => {
    expect(J._bestNodeLabel({ label: 'Good label' })).toBe('Good label');
    expect(J._bestNodeLabel({ label: 'trunc...', content: 'Full content text' })).toBe('Full content text');
    expect(J._bestNodeLabel({ label: 'ab', content: 'xy' })).toBe('ab');
    expect(J._bestNodeLabel({ id: 'nid' })).toBe('nid');
    expect(J._bestNodeLabel({})).toBe('');
  });
});

describe('graph.js', () => {
  let J;
  const sets = [];
  beforeEach(() => {
    vi.spyOn(console, 'log').mockImplementation(() => {});
    J = makeJUG({
      state: { activeFilter: 'all', searchQuery: '', lastData: null },
      STRUCTURAL_TYPES: { domain: true },
      setGraphData: vi.fn((n, e) => sets.push([n, e])),
      logNodes: vi.fn(),
    });
    sets.length = 0;
    globalThis.JUG = window.JUG = J;
    loadScript('ui/unified/js/graph.js');
  });

  const data = () => ({
    nodes: [
      { id: 'd', type: 'domain', label: 'D' },
      { id: 'm1', type: 'memory', label: 'alpha', heat: 0.9, emotion: 'joy', isProtected: true, isGlobal: true, content: 'zzz' },
      { id: 'm2', type: 'memory', label: 'beta', heat: 0.1, emotion: 'neutral' },
      { id: 'e1', type: 'entity', label: 'ent' },
      { id: 'di', type: 'discussion', label: 'disc', domain: 'Zed' },
      { id: 'c1', type: 'memory', label: 'collapsed', collapsed: true, _parentId: 'e1' },
    ],
    edges: [
      { source: 'd', target: 'm1', weight: 0.8, type: 'x' },
      { source: { id: 'm1' }, target: { id: 'e1' } },
      { source: 'm2', target: 'nope' },
    ],
  });

  function ids() {
    return J.allNodes.map((n) => n.data.id);
  }

  it('builds with no filter: hides collapsed, builds edge map and active edges', () => {
    J.buildGraph(data());
    expect(ids()).toEqual(['d', 'm1', 'm2', 'e1', 'di']);
    expect(J.getActiveEdges().length).toBe(2);
    expect(J.getActiveEdges()[0]).toMatchObject({ srcIdx: 0, tgtIdx: 1, weight: 0.8, type: 'x' });
    expect(J.getActiveEdges()[1]).toMatchObject({ weight: 0.3, type: 'default' });
    expect(J.edgeNodeMap[1]).toEqual([0, 1]);
    expect(J.logNodes).toHaveBeenCalled();
    expect(sets[0][1].length).toBe(2);
  });

  it.each([
    ['methodology', ['d']],
    ['memories', ['d', 'm1', 'm2']],
    ['knowledge', ['d', 'e1']],
    ['discussions', ['d', 'di']],
    ['emotional', ['d', 'm1']],
    ['protected', ['d', 'm1']],
    ['hot', ['d', 'm1']],
    ['global', ['d', 'm1']],
  ])('filter %s', (f, expected) => {
    J.state.activeFilter = f;
    J.buildGraph(data());
    expect(ids()).toEqual(expected);
  });

  it('search matches label, domain or content', () => {
    J.state.searchQuery = 'ALPHA';
    J.buildGraph(data());
    expect(ids()).toEqual(['d', 'm1']);
    J.state.searchQuery = 'zed';
    J.buildGraph(data());
    expect(ids()).toEqual(['d', 'di']);
    J.state.searchQuery = 'zzz';
    J.buildGraph(data());
    expect(ids()).toEqual(['d', 'm1']);
  });

  it('applies extra filters and tolerates empty data', () => {
    J._applyExtraFilters = (ns) => ns.filter((n) => n.type === 'domain');
    J.buildGraph(data());
    expect(ids()).toEqual(['d']);
    delete J.logNodes;
    J.buildGraph({});
    expect(J.allNodes).toEqual([]);
  });

  it('state listeners rebuild only when lastData exists', () => {
    J.emit('state:activeFilter');
    expect(sets.length).toBe(0);
    J.state.lastData = data();
    J.emit('state:activeFilter');
    J.emit('state:searchQuery');
    expect(sets.length).toBe(2);
  });

  it('toggleExpand arranges children in a ring and unpins later', () => {
    vi.useFakeTimers();
    const d = data();
    d.nodes.push({ id: 'c2', type: 'memory', label: 'c2x', collapsed: true, _parentId: 'e1', heat: 0.9 });
    d.nodes.find((n) => n.id === 'e1').x = 100;
    J.state.lastData = d;
    J.toggleExpand('e1');
    const kids = d.nodes.filter((n) => n._parentId === 'e1');
    expect(kids.every((k) => typeof k.fx === 'number')).toBe(true);
    expect(J._fadeInNodes).toEqual({ c1: true, c2: true });
    expect(J._fadeInDuration).toBe(800);
    expect(ids()).toContain('c1');
    vi.advanceTimersByTime(1300);
    expect(kids.every((k) => k.fx === undefined)).toBe(true);
    J.toggleExpand('e1');
    expect(ids()).not.toContain('c1');
    vi.useRealTimers();
  });

  it('toggleExpand uses a spiral for large sets and ignores unknown parents', () => {
    vi.useFakeTimers();
    const d = { nodes: [{ id: 'p', type: 'topic' }], edges: [] };
    for (let i = 0; i < 25; i++) d.nodes.push({ id: 'k' + i, type: 'memory', collapsed: true, _parentId: 'p' });
    J.state.lastData = d;
    J.toggleExpand('ghost');
    expect(J._fadeInNodes).toBeUndefined();
    J.toggleExpand('p');
    expect(Object.keys(J._fadeInNodes).length).toBe(25);
    expect(d.nodes[1].fx).not.toBe(d.nodes[2].fx);
    vi.useRealTimers();
  });

  it('toggleExpand without lastData is harmless', () => {
    J.toggleExpand('x');
    expect(J._expandedParents.x).toBe(true);
  });

  describe('appendGraphDelta', () => {
    it('seeds lastData, dedups, counts and emits a delta', () => {
      const emitted = [];
      J.on('state:lastData', (_e, v) => emitted.push(v));
      J.__wfgActive = true;
      J.appendGraphDelta(
        [{ id: 'a', kind: 'domain' }, { id: 'b', type: 'memory' }, { id: 'c', type: 'discussion' }, { id: 'd' }, null, {}, { id: 'a' }],
        [{ source: 'a', target: 'b' }, { source: { id: 'a' }, target: { id: 'b' } }, null, { source: 'b', target: 'c', kind: 'calls' }]
      );
      expect(J.state.lastData.nodes.length).toBe(4);
      expect(J.state.lastData.edges.length).toBe(2);
      expect(J.state.lastData.links.length).toBe(2);
      expect(J._statCounts).toMatchObject({ domain: 1, memory: 1, discussion: 1, totalNodes: 4, totalEdges: 2 });
      expect(emitted[0].delta.nodes.length).toBe(4);
      expect(emitted[0].delta.edges.length).toBe(2);
    });

    it('fills missing fields on existing nodes and reports them as enriched', () => {
      const emitted = [];
      J.on('state:lastData', (_e, v) => emitted.push(v));
      J.__wfgActive = true;
      J.appendGraphDelta([{ id: 'a', path: null }]);
      J.appendGraphDelta([{ id: 'a', path: '/p', label: 'L', other: undefined }]);
      expect(J.state.lastData.nodes[0]).toMatchObject({ path: '/p', label: 'L' });
      expect(emitted[1].delta.nodes.length).toBe(1);
      J.appendGraphDelta([{ id: 'a', path: '/other' }]);
      expect(J.state.lastData.nodes[0].path).toBe('/p');
      expect(emitted[2].delta.nodes.length).toBe(0);
    });

    it('schedules a coalesced legacy rebuild when the wfg renderer is inactive', () => {
      const raf = vi.fn((fn) => fn());
      globalThis.requestAnimationFrame = raf;
      J.appendGraphDelta([{ id: 'a', type: 'memory' }]);
      J.appendGraphDelta([{ id: 'b', type: 'memory' }]);
      expect(raf).toHaveBeenCalledTimes(2);
      expect(sets.length).toBe(2);
      delete globalThis.requestAnimationFrame;
    });

    it('falls back to setTimeout without rAF and coalesces', () => {
      vi.useFakeTimers();
      const saved = globalThis.requestAnimationFrame;
      globalThis.requestAnimationFrame = undefined;
      J.appendGraphDelta([{ id: 'a', type: 'memory' }]);
      J.appendGraphDelta([{ id: 'b', type: 'memory' }]);
      expect(J._rebuildQueued).toBe(true);
      vi.advanceTimersByTime(20);
      expect(J._rebuildQueued).toBe(false);
      expect(sets.length).toBe(1);
      globalThis.requestAnimationFrame = saved;
      vi.useRealTimers();
    });

    it('survives subscriber exceptions and a missing emit', () => {
      J.__wfgActive = true;
      J.on('state:lastData', () => {
        throw new Error('sub');
      });
      expect(() => J.appendGraphDelta([{ id: 'a' }])).not.toThrow();
      J.emit = undefined;
      expect(() => J.appendGraphDelta(null, null)).not.toThrow();
    });

    it('addBatchToGraph delegates, logs, and ignores empty batches', () => {
      J.__wfgActive = true;
      J.addBatchToGraph({ nodes: [] });
      expect(J.state.lastData).toBeNull();
      J.addBatchToGraph({ nodes: [{ id: 'z' }], edges: [] });
      expect(J.state.lastData.nodes.length).toBe(1);
      expect(J.logNodes).toHaveBeenCalledWith([{ id: 'z' }]);
      delete J.logNodes;
      J.addBatchToGraph({ nodes: [{ id: 'y' }] });
      expect(J.state.lastData.nodes.length).toBe(2);
    });
  });
});
