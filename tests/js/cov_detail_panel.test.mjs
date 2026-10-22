// Behaviour tests for ui/unified/js/detail_panel.js using the real state bus,
// config tokens and detail_format.js; only network and sibling renderers
// (trace detail, discussion detail, memory science) are stubbed.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let J;
let graphNodes;

const panel = () => document.getElementById('detail-panel');
const content = () => document.getElementById('detail-content');
const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

function load(opts) {
  opts = opts || {};
  document.body.innerHTML = `
    <div id="detail-panel"><div id="detail-content"></div></div>
    <button id="minimize-detail"></button><div id="detail-peek"></div>
    <button id="close-detail"></button>`;
  J = {};
  globalThis.JUG = window.JUG = J;
  loadScript('ui/unified/js/state.js');
  loadScript('ui/unified/js/config.js');
  loadScript('ui/unified/js/detail_format.js');
  graphNodes = [];
  J.getGraph = () => ({ graphData: () => ({ nodes: graphNodes }) });
  J.selectNodeById = vi.fn();
  J.deselectNode = vi.fn(() => J.emit('graph:deselectNode'));
  J._currentEdges = [];
  J._traceDetail = {
    build: vi.fn(() => '<div class="trace-built">T</div>'),
    wire: vi.fn(),
  };
  J._disc = {
    buildDiscussionDetail: vi.fn(() => '<div class="disc-built">D</div>'),
    openConversationModal: vi.fn(),
  };
  if (opts.memSci) J._memSci = opts.memSci;
  loadScript('ui/unified/js/detail_panel.js');
  document.dispatchEvent(new Event('DOMContentLoaded'));
}

// Each load() re-runs the IIFE, which registers fresh window/document
// listeners; remove the previous generation so handlers do not stack.
const registered = [];
beforeEach(() => {
  vi.restoreAllMocks();
  for (const t of [window, document]) {
    const orig = t.addEventListener.bind(t);
    vi.spyOn(t, 'addEventListener').mockImplementation((type, fn, o) => {
      registered.push([t, type, fn]);
      return orig(type, fn, o);
    });
  }
});
afterEach(() => {
  delete globalThis.fetch;
  registered.splice(0).forEach(([t, type, fn]) => t.removeEventListener(type, fn));
});

describe('openDetailPanel — generic memory path', () => {
  it('renders sections and opens the panel', () => {
    load();
    J.state.activeView = 'graph';
    const mem = {
      id: 'm1', type: 'memory', label: 'A memory', content: 'Body text',
      emotion: 'discovery', arousal: 0.5, heat: 0.7, tags: ['x'],
    };
    J.openDetailPanel(mem);
    expect(panel().classList.contains('open')).toBe(true);
    expect(content().textContent).toContain('A memory');
    expect(content().querySelector('.emo-card')).not.toBeNull();
    expect(content().textContent).toContain('This memory marks a discovery or insight');
    expect(content().querySelector('.bio-fill').style.width).toBe('50%');
  });

  it('is a no-op without data or DOM', () => {
    load();
    J.openDetailPanel(null);
    expect(panel().classList.contains('open')).toBe(false);
    document.body.innerHTML = '';
    J.openDetailPanel({ id: 'x', type: 'memory' });
  });

  it('skips emotion for neutral, tolerates an unknown emotion', () => {
    load();
    J.state.activeView = 'graph';
    J.openDetailPanel({ id: 'a', type: 'memory', emotion: 'neutral' });
    expect(content().querySelector('.emo-card')).toBeNull();
    J.openDetailPanel({ id: 'b', type: 'memory', emotion: 'weird' });
    expect(content().querySelector('.emo-name').textContent).toBe('Weird');
    expect(content().querySelector('.emo-meter')).toBeNull();
  });

  it('appends memory-science components when provided', () => {
    const mk = (cls) => () => {
      const d = document.createElement('div');
      d.className = cls;
      return d;
    };
    load({
      memSci: {
        buildEmotionChip: mk('chip'),
        buildMeaningSection: mk('meaning'),
        buildExplainedPanel: mk('explained'),
      },
    });
    J.state.activeView = 'graph';
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'L' });
    expect(content().querySelector('.chip').classList.contains('ms-emotion--detail')).toBe(true);
    expect(content().querySelector('.meaning')).not.toBeNull();
    expect(content().querySelector('.explained')).not.toBeNull();
    J._memSci = { buildEmotionChip: () => null, buildMeaningSection: () => null, buildExplainedPanel: () => null };
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'L' });
    expect(content().querySelector('.chip')).toBeNull();
    J._memSci = {};
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'L' });
  });

  it('routes structural kinds to the trace detail renderer', () => {
    load();
    J.openDetailPanel({ id: 'f', type: 'file', kind: 'file' });
    expect(content().querySelector('.trace-built')).not.toBeNull();
    expect(J._traceDetail.wire).toHaveBeenCalled();
    expect(panel().classList.contains('open')).toBe(true);
  });

  it('routes memory/discussion and activity kinds to trace only in trace view', () => {
    load();
    J.state.activeView = 'graph';
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'x' });
    expect(J._traceDetail.build).not.toHaveBeenCalled();
    J.openDetailPanel({ id: 'm', kind: 'mcp' });
    expect(J._traceDetail.build).not.toHaveBeenCalled();
    J.state.activeView = 'trace';
    J.openDetailPanel({ id: 'm', type: 'memory' });
    J.openDetailPanel({ id: 'd', type: 'discussion' });
    J.openDetailPanel({ id: 't', kind: 'tool_hub' });
    expect(J._traceDetail.build).toHaveBeenCalledTimes(3);
  });

  it('renders the discussion layout', () => {
    load();
    J.state.activeView = 'graph';
    J.openDetailPanel({ id: 'd', type: 'discussion', label: 'Session' });
    expect(content().querySelector('.disc-built')).not.toBeNull();
  });

  it('renders wiki page fields', () => {
    load();
    J.state.activeView = 'graph';
    J.openDetailPanel({
      id: 'w', type: 'wiki', label: 'Page', page_kind: 'adr', domain: 'core', status: 'live', path: 'wiki/x.md',
    });
    const t = content().textContent;
    expect(t).toContain('adr');
    expect(t).toContain('core');
    expect(t).toContain('live');
    expect(t).toContain('wiki/x.md');
    J.openDetailPanel({ id: 'w2', type: 'wiki', label: 'Bare' });
    expect(content().textContent).not.toContain('Status');
  });
});

describe('connections list', () => {
  function setup() {
    load();
    J.state.activeView = 'graph';
    graphNodes = [
      { id: 'n1', type: 'entity', label: 'run_benchmark.py', path: '/a/benchmarks/locomo/run_benchmark.py' },
      { id: 'n2', type: 'entity', label: 'run_benchmark.py', path: '/a/benchmarks/beam/run_benchmark.py' },
      { id: 'n3', type: 'entity', label: 'same' },
      { id: 'n4', type: 'entity', label: 'same' },
      { id: 'n5', type: 'entity', label: 'unique' },
      { id: 'n6', type: 'memory', label: 'mem six' },
      { id: 'n7', type: 'entity', label: 'long', path: '/p/z' },
      { id: 'longidentifier-123456789', type: 'entity', label: 'same' },
    ];
    J._currentEdges = [
      { source: 'm', target: 'n1', type: 'imports', confidence: 0.8, reason: 'heuristic<' },
      { source: { id: 'n2' }, target: { id: 'm' }, type: 'imports', confidence: 1.0, reason: 'direct-ast' },
      { source: 'm', target: 'n3', type: 'custom_kind' },
      { source: 'm', target: 'n4', type: 'custom_kind' },
      { source: 'm', target: 'longidentifier-123456789', type: 'custom_kind' },
      { source: 'm', target: 'n5', type: 'memory-entity' },
      { source: 'm', target: 'n6' },
      { source: 'm', target: 'ghost', type: 'calls' },
      { source: 'x', target: 'y' },
    ];
  }

  it('groups by edge type, labels them, disambiguates homonyms, shows chips', () => {
    setup();
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'Me', content: 'c' });
    const c = content();
    expect(c.textContent).toContain('Connections (8)');
    expect(c.textContent).toContain('Mentioned in');
    expect(c.textContent).toContain('custom kind');
    expect(c.textContent).toContain('related');
    // path-based disambiguation
    const subs = Array.from(c.querySelectorAll('.conn-sub')).map((s) => s.textContent);
    expect(subs).toContain('locomo');
    expect(subs).toContain('beam');
    // id-based disambiguation (no path), long ids shortened
    expect(subs).toContain('n3');
    expect(subs.some((s) => s.startsWith('…'))).toBe(true);
    // confidence + reason chips only for non-structural defaults
    expect(c.querySelectorAll('.conn-confidence').length).toBe(1);
    expect(c.querySelector('.conn-reason').innerHTML).toBe('heuristic&lt;');
    // unique label gets no sub
    const unique = c.querySelector('.conn-item[data-node-id="n5"]');
    expect(unique.querySelector('.conn-sub')).toBeNull();
    expect(unique.title).toBe('n5');
  });

  it('clicking a connection selects that node', () => {
    setup();
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'Me', content: 'c' });
    content().querySelector('.conn-item[data-node-id="n5"]').click();
    expect(J.selectNodeById).toHaveBeenCalledWith('n5');
  });

  it('uses the node index when a renderer publishes one', () => {
    setup();
    J._nodeIndex = new Map([['n5', graphNodes[4]]]);
    J._currentEdges = [{ source: 'm', target: 'n5', type: 'calls' }, { source: 'm', target: 'zz', type: 'calls' }];
    J.openDetailPanel({ id: 'm', type: 'memory', label: 'Me', content: 'c' });
    expect(content().querySelectorAll('.conn-item').length).toBe(1);
  });

});

describe('button wiring via rendered markup', () => {
  it('toggles expand, raw and opens the conversation modal', () => {
    load();
    J.state.activeView = 'graph';
    J._fmt.content = () =>
      '<div id="detail-content-text" class="collapsed"></div><button id="detail-expand-btn">Show more</button>' +
      '<div id="detail-raw-text" class="hidden"></div><button id="detail-raw-btn">Show raw</button>';
    J._fmt.header = () => '<button class="disc-view-btn" data-session-id="s9"></button>';
    J.openDetailPanel({ id: 'm', type: 'memory' });
    const exp = document.getElementById('detail-expand-btn');
    exp.click();
    expect(document.getElementById('detail-content-text').classList.contains('collapsed')).toBe(false);
    expect(exp.textContent).toBe('Show less');
    exp.click();
    expect(exp.textContent).toBe('Show more');
    const raw = document.getElementById('detail-raw-btn');
    raw.click();
    expect(raw.textContent).toBe('Hide raw');
    raw.click();
    expect(raw.textContent).toBe('Show raw');
    content().querySelector('.disc-view-btn').click();
    expect(J._disc.openConversationModal).toHaveBeenCalledWith('s9');
  });

  it('tolerates buttons whose target blocks are missing', () => {
    load();
    J.state.activeView = 'graph';
    J._fmt.content = () => '<button id="detail-expand-btn"></button><button id="detail-raw-btn"></button>';
    J.openDetailPanel({ id: 'm', type: 'memory' });
    document.getElementById('detail-expand-btn').click();
    document.getElementById('detail-raw-btn').click();
  });
});

describe('selection events and enrichment', () => {
  const record = {
    found: true,
    record: { content: 'rich body', heat_base: 0.4, consolidation_stage: 'late', tags: ['t'], importance: 0.2 },
  };

  it('enriches a sparse selected node from /api/graph/node and caches it', async () => {
    load();
    J.state.activeView = 'graph';
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(record) })
    );
    const node = { id: 'mm1', type: 'memory', label: 'sparse' };
    J.state.selectedNode = node;
    J.emit('graph:selectNode', node);
    expect(panel().classList.contains('open')).toBe(true);
    await flush();
    expect(fetch.mock.calls[0][0]).toBe('/api/graph/node?id=mm1');
    expect(node.body).toBe('rich body');
    expect(node.heat).toBe(0.4);
    expect(content().textContent).toContain('rich body');

    // second selection of a fresh sparse object hits the cache, no fetch
    const again = { id: 'mm1', type: 'memory' };
    J.emit('graph:selectNode', again);
    expect(again.body).toBe('rich body');
    expect(fetch).toHaveBeenCalledTimes(1);

    // already rich: no fetch
    J.emit('graph:selectNode', { id: 'rich', type: 'memory', content: 'x' });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not re-render when the node is no longer selected, and ignores misses', async () => {
    load();
    J.state.activeView = 'graph';
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(record) }));
    const html = () => content().innerHTML;
    J.emit('graph:selectNode', { id: 'q1', type: 'memory', label: 'qq' });
    const before = html();
    await flush();
    expect(html()).toBe(before);

    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ found: false }) }));
    J.emit('graph:selectNode', { id: 'q2', type: 'memory' });
    await flush();
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
    J.emit('graph:selectNode', { id: 'q3', type: 'memory' });
    await flush();
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('x')));
    J.emit('graph:selectNode', { id: 'q4', type: 'memory' });
    await flush();
    expect(panel().classList.contains('open')).toBe(true);
  });

  it('trace kinds open without enrichment; id-less nodes do not fetch', () => {
    load();
    globalThis.fetch = vi.fn();
    J.state.activeView = 'trace';
    J.emit('graph:selectNode', { id: 'f1', kind: 'file' });
    J.emit('graph:selectNode', { id: 'm1', type: 'memory' });
    J.state.activeView = 'graph';
    J.emit('graph:selectNode', { type: 'entity', label: 'noid' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('refreshes only the still-selected card on nodeUpdated', () => {
    load();
    J.state.activeView = 'graph';
    J.emit('graph:selectNode', { id: 'u1', type: 'entity', label: 'before', content: 'c' });
    const refreshed = { id: 'u1', type: 'entity', label: 'after', content: 'c' };
    J.emit('graph:nodeUpdated', refreshed);
    expect(content().textContent).toContain('after');
    J.emit('graph:nodeUpdated', { id: 'other', type: 'entity', label: 'nope', content: 'c' });
    expect(content().textContent).not.toContain('nope');
    J.emit('graph:nodeUpdated', { value: { id: 'u1', type: 'entity', label: 'wrapped', content: 'c' } });
    J.emit('graph:nodeUpdated', null);
  });

  it('deselect closes the panel', () => {
    load();
    panel().classList.add('open', 'minimized');
    J.emit('graph:deselectNode');
    expect(panel().classList.contains('open')).toBe(false);
    expect(panel().classList.contains('minimized')).toBe(false);
  });
});

describe('minimize / restore / close / keyboard', () => {
  it('minimize button, peek restore and close button', () => {
    load();
    panel().classList.add('open');
    document.getElementById('minimize-detail').click();
    expect(panel().classList.contains('minimized')).toBe(true);
    expect(panel().classList.contains('open')).toBe(false);
    document.getElementById('detail-peek').click();
    expect(panel().classList.contains('open')).toBe(true);
    document.getElementById('close-detail').click();
    expect(J.deselectNode).toHaveBeenCalled();
    expect(panel().classList.contains('open')).toBe(false);
  });

  it('Escape minimizes an open panel, then deselects from minimized', () => {
    load();
    panel().classList.add('open');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(panel().classList.contains('minimized')).toBe(true);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(panel().classList.contains('minimized')).toBe(false);
    expect(J.deselectNode).toHaveBeenCalled();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); // closed: nothing
  });

  it('ignores keys typed into inputs and non-Escape keys', () => {
    load();
    panel().classList.add('open');
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(panel().classList.contains('open')).toBe(true);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    expect(panel().classList.contains('open')).toBe(true);
  });

  it('missing chrome elements are tolerated', () => {
    load();
    document.body.innerHTML = '';
    J.closeDetailPanel();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    J.emit('graph:deselectNode');
  });
});
