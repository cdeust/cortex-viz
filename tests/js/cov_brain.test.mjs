// Behaviour tests for ui/brain/js/boot.js (composition root + legend) and
// ui/brain/js/impact.js (impact-diagram shim).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

const CHROME = `
  <div id="loading"><div id="loading-title"></div><div id="loading-sub"></div></div>
  <span id="r-nodes"></span><span id="r-edges"></span><span id="s-sym"></span>
  <div id="legend"></div><button id="reset-btn"></button>`;

function makeData() {
  const nodes = [
    { id: 'd1', kind: 'domain', color: '#111111' },
    { id: 'm1', kind: 'memory', color: '#222222' },
    { id: 'm2', kind: 'memory', color: '#222222' },
    { id: 'm3', type: 'memory', color: '#222222' },
    { id: 'e1', kind: 'entity', entityType: 'person', color: '#333333' },
    { id: 'e2', kind: 'entity', entityType: 'org', color: '#444444' },
    { id: 'e3', kind: 'entity', entityType: 'org', color: '#444444' },
    { id: 'f1', kind: 'file', color: '#555555' },
    { id: 'x', color: '#000' },
  ];
  return {
    nodes,
    edges: [{ source: 'm1', target: 'e1' }],
    domains: [{ id: 'd1' }],
    domainPos: { d1: [0, 0, 0] },
    byKind: { domain: 1, memory: 3, entity: 3, file: 1, symbol: 7 },
  };
}

function setupBoot(opts) {
  opts = opts || {};
  document.body.innerHTML = CHROME;
  const data = opts.data || makeData();
  const calls = [];
  const ncolor = { array: new Float32Array(data.nodes.length * 3), needsUpdate: false };
  window.THREE = {
    Color: class {
      constructor() {
        this.r = this.g = this.b = 0;
      }
      set(h) {
        if (h === 'THROW') throw new Error('bad color');
        this.r = 1;
        this.g = 0.5;
        this.b = 0.25;
      }
    },
  };
  globalThis.THREE = window.THREE;
  const handlers = {};
  window.JUG = globalThis.JUG = {
    _tok: { hub: '#111111', info: '#222222', episodic: '#333333' },
    getNodeColor: opts.noGetColor ? undefined : (n) => n.color,
    on: (ev, fn) => (handlers[ev] = fn),
  };
  if (opts.noJug) delete window.JUG;
  window.BRAIN = globalThis.BRAIN = {
    fetchGraph: vi.fn((cb) => {
      cb({ nodes: 5, node_total: 10, edges: 2 });
      cb({ nodes: 5, edges: 2 });
      return opts.fetchReject ? Promise.reject(new Error('net down')) : Promise.resolve(data);
    }),
    loadBrain: vi.fn(() => Promise.resolve({ box: 1 })),
    buildAtlas: vi.fn(() => ({})),
    buildSurface: vi.fn(() => ({ anchorForDomain: () => [1, 2, 3] })),
    placeNodes: vi.fn(() => ({ positions: new Float32Array(3), regionKey: [], hemi: [] })),
    relaxAssociative: vi.fn(() => ({ moved: 1 })),
    buildPoints: vi.fn(),
    installDetailBridge: vi.fn(),
    buildEdges: vi.fn(),
    initPicking: vi.fn(),
    fitView: vi.fn(),
    repaintPointFilter: vi.fn(),
    repaintEdgeFilter: vi.fn(),
    searchInit: vi.fn(),
    MEMORY_SYSTEMS: [
      { label: 'Hippocampus', repKind: 'memory' },
      { label: 'Cortex', repKind: 'nothing', colorCat: 'hub' },
      { label: 'Other', repKind: 'nothing' },
    ],
    PALETTE: {
      isGraded: (k) => k === 'entity',
      subLabelFor: (n) => n.entityType,
      communityColor: (cid) => '#c0ffee' + cid,
    },
    detectCommunities: vi.fn(() => ({
      count: 1234,
      communityOf: new Map([['m1', 0], ['m2', 0], ['m3', 5]]),
      sizes: new Map([[0, 5], [5, 1]]),
    })),
    MIN_COMMUNITY_SIZE: 2,
    points: {
      geometry: { getAttribute: (n) => (n === 'ncolor' ? ncolor : null) },
    },
  };
  Object.assign(window.BRAIN, opts.brain || {});
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  loadScript('ui/brain/js/boot.js');
  return { data, calls, ncolor, handlers };
}

describe('brain boot', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, '', '/');
  });

  it('boots the whole pipeline and fills stats and legend', async () => {
    const { ncolor } = setupBoot();
    await flush();
    const B = window.BRAIN;
    expect(document.getElementById('loading-sub').textContent).toMatch(/relaxing|routing/);
    expect(B.searchInit).toHaveBeenCalled();
    expect(B.buildPoints).toHaveBeenCalled();
    expect(B.initPicking).toHaveBeenCalled();
    expect(B.fitView).toHaveBeenCalled();
    expect(document.getElementById('loading').classList.contains('gone')).toBe(true);
    expect(document.getElementById('r-nodes').textContent).toBe('9');
    expect(document.getElementById('r-edges').textContent).toBe('1');
    expect(document.getElementById('s-sym').textContent).toBe('7');
    expect(B.indexOfId.get('m2')).toBe(2);
    expect(B.nodeKindByRow[3]).toBe('memory');
    expect(B.relaxAssociative).toHaveBeenCalled();

    const legend = document.getElementById('legend');
    expect(legend.textContent).toContain('Hippocampus');
    expect(legend.textContent).toContain('1,234 communities detected');
    expect(legend.textContent).toContain('memory — coloured by association community');
    // graded entity kind: header + per-sub rows
    expect(legend.querySelector('.leg-subhead[data-kind="entity"]')).not.toBeNull();
    expect(legend.querySelectorAll('.leg-item.sub').length).toBe(2);
    // dots painted from category tags via JUG._tok
    const dots = legend.querySelectorAll('.leg-dot[data-color-cat]');
    expect(dots.length).toBeGreaterThan(3);
    expect(Array.from(dots).some((d) => d.style.background !== '')).toBe(true);

    // surface change re-inks the colour buffer
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(ncolor.needsUpdate).toBe(true);
    expect(ncolor.array[0]).toBe(1);
  });

  it('legend click and keyboard toggle the kind filter', async () => {
    setupBoot();
    await flush();
    const B = window.BRAIN;
    const row = document.querySelector('#legend .leg-item[data-kind="file"]');
    row.querySelector('.leg-label').click();
    expect(B.filterKind).toBe('file');
    expect(B.repaintPointFilter).toHaveBeenCalled();
    expect(B.repaintEdgeFilter).toHaveBeenCalled();
    expect(row.classList.contains('leg-active')).toBe(true);
    expect(row.getAttribute('aria-pressed')).toBe('true');
    // Enter on same row toggles off
    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(B.filterKind).toBeNull();
    expect(row.getAttribute('aria-pressed')).toBe('false');
    row.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    expect(B.filterKind).toBe('file');
    // other keys and non-kind targets are ignored
    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', bubbles: true }));
    expect(B.filterKind).toBe('file');
    document.querySelector('#legend .leg-head').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );
    document.querySelector('#legend .leg-head').click();
    expect(B.filterKind).toBe('file');
    B.toggleFilterKind('file');
    expect(B.filterKind).toBeNull();
  });

  it('selecting a node hides the legend, deselecting restores it', async () => {
    const { handlers } = setupBoot();
    await flush();
    const legend = document.getElementById('legend');
    handlers['graph:selectNode']();
    expect(legend.style.display).toBe('none');
    handlers['graph:deselectNode']();
    expect(legend.style.display).toBe('');
  });

  it('without community colouring memory is broken down per stage', async () => {
    setupBoot({ brain: { detectCommunities: undefined, PALETTE: undefined } });
    await flush();
    const legend = document.getElementById('legend');
    expect(legend.textContent).not.toContain('communities detected');
    expect(legend.querySelector('[data-kind="memory"]')).not.toBeNull();
  });

  it('works without JUG: falls back to node colour and CSS token', async () => {
    document.documentElement.style.setProperty('--field-point', ' #abcdef ');
    setupBoot({ noJug: true, brain: { detectCommunities: undefined } });
    await flush();
    expect(window.BRAIN.buildPoints).toHaveBeenCalled();
    expect(document.getElementById('loading').classList.contains('gone')).toBe(true);
  });

  it('uses CortexPalette.hex for the neutral fallback', async () => {
    window.CortexPalette = { hex: () => '#010203' };
    const data = makeData();
    data.nodes = [{ id: 'q', kind: 'file' }, { id: 'r', kind: 'file', color: 'THROW' }];
    data.edges = [];
    setupBoot({ noJug: true, data, brain: { detectCommunities: undefined } });
    await flush();
    expect(window.BRAIN.buildPoints).toHaveBeenCalled();
    delete window.CortexPalette;
  });

  it('survives ASSOC relax off and missing search/relax hooks', async () => {
    window.history.replaceState({}, '', '/?assoc=0');
    setupBoot({ brain: { searchInit: undefined } });
    await flush();
    expect(window.BRAIN.ASSOC_RELAX_ON).toBe(false);
    expect(window.BRAIN.relaxAssociative).not.toHaveBeenCalled();
  });

  it('shows an error when the graph fails to load', async () => {
    setupBoot({ fetchReject: true });
    await flush();
    const el = document.getElementById('loading');
    expect(el.classList.contains('error')).toBe(true);
    expect(document.getElementById('loading-title').textContent).toBe('Could not load brain view');
    expect(document.getElementById('loading-sub').textContent).toBe('net down');
  });

  it('shows an error when the graph is empty', async () => {
    const data = makeData();
    data.nodes = [];
    setupBoot({ data });
    await flush();
    expect(document.getElementById('loading-sub').textContent).toBe('graph returned 0 nodes');
  });

  it('repaint is a no-op with no attribute or points', async () => {
    const { ncolor } = setupBoot({ brain: {} });
    await flush();
    window.BRAIN.points.geometry.getAttribute = () => null;
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    window.BRAIN.points = null;
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(ncolor).toBeDefined();
  });
});

describe('brain impact shim', () => {
  const IMPACT_DOM = `
    <div id="flow-panel"><div id="flow-title"></div><div id="flow-content"></div>
    <button id="flow-close"></button></div><div id="detail-panel" class="open"></div>`;

  beforeEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = IMPACT_DOM;
    window.BRAIN = globalThis.BRAIN = {};
    const idx = new Map([
      ['n1', { kind: 'file', path: '/repo/src/a/b.py' }],
      ['n2', { type: 'file', path: '/repo/README.md' }],
      ['n3', { kind: 'file', path: '/other/README.md' }],
      ['n4', { kind: 'memory', path: '/x' }],
      ['n5', { kind: 'file', path: '/repo/lib/only.py' }],
      ['n6', { kind: 'file', path: '/long/src/a/b.py' }],
    ]);
    window.JUG = globalThis.JUG = { _nodeIndex: idx, selectNodeById: vi.fn() };
    delete window.TraceView;
    loadScript('ui/brain/js/impact.js');
    document.dispatchEvent(new Event('DOMContentLoaded'));
  });

  it('esc escapes quotes; group caps at 60 and shows arrows', () => {
    const { esc, group } = window.TraceView._impactTest;
    expect(esc('a"b\'<&>')).toBe('a&quot;b&#39;&lt;&amp;&gt;');
    expect(esc(null)).toBe('');
    expect(group('T', [], 'up')).toBe('');
    expect(group('T')).toBe('');
    const items = Array.from({ length: 62 }, (_, i) => ({ file: 'f' + i, confidence: 0.5 }));
    const h = group('Title', items, 'up');
    expect(h).toContain('←');
    expect(h).toContain('50%');
    expect(h).toContain('2 more');
    expect(group('T', [{ name: 'n' }], 'down')).toContain('→');
    expect(group('T', [{}], 'flat')).toContain('?');
  });

  it('resolveNodeId resolves exact, suffix, and unique basename only', () => {
    const r = window.TraceView._resolveNodeId;
    expect(r('')).toBeNull();
    expect(r(null)).toBeNull();
    expect(r('#frag')).toBeNull();
    expect(r('/repo/src/a/b.py')).toBe('n1');
    expect(r('a/b.py')).toBe('n1'); // shortest suffix match
    expect(r('./lib/only.py:sym')).toBe('n5');
    expect(r('README.md')).toBeNull(); // ambiguous basename
    expect(r('only.py')).toBe('n5');
    expect(r('missing.py')).toBeNull();
  });

  it('showImpact renders groups, wires clickable and dead boxes', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            center: { label: 'b.py' },
            depends_on: [{ file: 'lib/only.py', label: 'only', kind: 'imports', confidence: 0.9 }],
            depended_on_by: [{ file: 'nowhere.py' }],
          }),
      })
    );
    window.TraceView.showImpact('src/a/very/long/directory/name/with/path/b.py');
    const panel = document.getElementById('flow-panel');
    expect(panel.classList.contains('open')).toBe(true);
    expect(panel.classList.contains('with-detail')).toBe(true);
    expect(document.getElementById('flow-content').textContent).toContain('analyzing');
    expect(document.getElementById('flow-title').textContent).toMatch(/^Impact · /);
    await flush();
    const content = document.getElementById('flow-content');
    expect(content.querySelector('.impact-center').textContent).toBe('b.py');
    const boxes = content.querySelectorAll('.impact-box');
    expect(boxes.length).toBe(2);
    boxes[0].click();
    expect(window.JUG.selectNodeById).toHaveBeenCalledWith('n5');
    expect(boxes[1].classList.contains('impact-box--dead')).toBe(true);
    expect(fetch.mock.calls[0][0]).toContain('/api/trace/impact?path=');
  });

  it('showImpact handles empty, null and failed responses', async () => {
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }));
    window.TraceView.showImpact('x.py');
    await flush();
    expect(document.getElementById('flow-content').textContent).toContain('No dependencies found');

    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
    window.TraceView.showImpact('x.py');
    await flush();
    expect(document.getElementById('flow-content').textContent).toContain('No dependency data');

    globalThis.fetch = vi.fn(() => Promise.reject(new Error('boom<')));
    window.TraceView.showImpact('x.py');
    await flush();
    expect(document.getElementById('flow-content').innerHTML).toContain('Impact failed: boom&lt;');
  });

  it('showImpact without a detail panel or title still works; missing panel is a no-op', async () => {
    document.getElementById('detail-panel').remove();
    document.getElementById('flow-title').remove();
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
    window.TraceView.showImpact('x.py');
    expect(document.getElementById('flow-panel').classList.contains('with-detail')).toBe(false);
    await flush();
    document.body.innerHTML = '';
    window.TraceView.showImpact('y.py');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('close button and Escape close the flow panel, but not from inputs', () => {
    const panel = document.getElementById('flow-panel');
    panel.classList.add('open');
    document.getElementById('flow-close').click();
    expect(panel.classList.contains('open')).toBe(false);
    panel.classList.add('open');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(panel.classList.contains('open')).toBe(false);
    panel.classList.add('open');
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(panel.classList.contains('open')).toBe(true);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    expect(panel.classList.contains('open')).toBe(true);
  });
});
