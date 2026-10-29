// Behaviour tests for ui/unified/js/trace.js: boot, domain/session expansion,
// historical reveal batching, live tail, activity batches, impact diagram.
// Real state bus; only fetch and the graph merge/renderer entry points are stubs.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let J;
let routes;
let applied; // every appendGraphDelta call as {nodes, edges}

const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
const bad = () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
const status = () => document.getElementById('status-text').textContent;
const appliedIds = () => applied.flatMap((a) => a.nodes.map((n) => n.id));

async function tick(ms) {
  await vi.advanceTimersByTimeAsync(ms);
}

function load(opts) {
  opts = opts || {};
  document.body.innerHTML = `
    <div id="graph-container"></div><div id="status-text"></div>
    <div id="flow-panel"><div id="flow-title"></div><div id="flow-content"></div></div>
    <button id="flow-close"></button><div id="detail-panel" class="open"></div>`;
  applied = [];
  window.JUG = {
    appendGraphDelta: vi.fn((n, e) => applied.push({ nodes: n, edges: e })),
    setGraphData: vi.fn(),
  };
  globalThis.JUG = window.JUG;
  loadScript('ui/unified/js/state.js');
  J = window.JUG;
  if (opts.view) J.state.activeView = opts.view;
  loadScript('ui/unified/js/trace.js');
  document.dispatchEvent(new Event('DOMContentLoaded'));
}

const registered = [];
beforeEach(() => {
  vi.useFakeTimers();
  routes = {};
  globalThis.fetch = vi.fn((url) => {
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((k) => String(url).startsWith(k));
    return key ? routes[key](url) : Promise.reject(new Error('no route ' + url));
  });
  for (const t of [window, document]) {
    const orig = t.addEventListener.bind(t);
    vi.spyOn(t, 'addEventListener').mockImplementation((type, fn, o) => {
      registered.push([t, type, fn]);
      return orig(type, fn, o);
    });
  }
});
afterEach(() => {
  if (window.TraceView) window.TraceView.setLive(false);
  registered.splice(0).forEach(([t, type, fn]) => t.removeEventListener(type, fn));
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete globalThis.fetch;
  delete window.CortexPalette;
});

const domainsPayload = {
  nodes: [
    { id: 'domain:a', kind: 'domain', label: 'A' },
    { id: 'act:x', kind: 'action', tool: 'Edit' },
    { id: 'act:y', type: 'action', tool: 'Unknown' },
  ],
  edges: [],
};

describe('boot', () => {
  it('loads domains on mount, seeds the trace schema and colours actions', async () => {
    document.documentElement.style.setProperty('--tool-edit', ' #ff0000 ');
    routes['/api/trace/domains'] = () => ok(domainsPayload);
    load();
    expect(status()).toBe('Loading domains...');
    expect(J.state.lastData.meta.schema).toBe('trace.v1');
    expect(J.setGraphData).toHaveBeenCalledWith([], []);
    await tick(0);
    expect(status()).toBe('3 domains - click to expand');
    expect(applied[0].nodes[1].color).toBe('#ff0000');
    expect(applied[0].nodes[2].color).toBeUndefined();
  });

  it('colours actions through CortexPalette when present', async () => {
    window.CortexPalette = { hex: vi.fn(() => '#00ff00') };
    routes['/api/trace/domains'] = () => ok(domainsPayload);
    load();
    await tick(0);
    expect(applied[0].nodes[1].color).toBe('#00ff00');
  });

  it('reports a failed load and can be retried with reload()', async () => {
    routes['/api/trace/domains'] = () => bad();
    load();
    await tick(0);
    expect(status()).toBe('Trace load failed: HTTP 500');
    routes['/api/trace/domains'] = () => ok({ nodes: [{ id: 'domain:z', kind: 'domain' }] });
    window.TraceView.reload();
    await tick(0);
    expect(status()).toBe('1 domains - click to expand');
    window.TraceView.boot(); // already booted: no-op
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does not boot while another view is active, and boots on switch to trace', async () => {
    routes['/api/trace/domains'] = () => ok(domainsPayload);
    load({ view: 'board' });
    expect(fetch).not.toHaveBeenCalled();
    J.state.activeView = 'trace';
    await tick(0);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('waits for the JUG bus before attaching', async () => {
    window.JUG = {};
    globalThis.JUG = window.JUG;
    document.body.innerHTML = '<div id="status-text"></div>';
    loadScript('ui/unified/js/trace.js');
    document.dispatchEvent(new Event('DOMContentLoaded'));
    await tick(70);
    loadScript('ui/unified/js/state.js');
    window.JUG.appendGraphDelta = vi.fn();
    routes['/api/trace/domains'] = () => ok({ nodes: [] });
    await tick(70);
    expect(window.TraceView).toBeDefined();
  });
});

describe('expansion', () => {
  const sessions = {
    nodes: [
      { id: 'session:s1', kind: 'session', session_id: 's1' },
      { id: 'session:s2', kind: 'session' },
    ],
    edges: [
      { source: 'domain:a', target: 'session:s1', kind: 'has_session' },
      { source: { id: 'domain:a' }, target: 'session:s2', kind: 'has_session' },
    ],
  };

  beforeEach(() => {
    routes['/api/trace/domains'] = () => ok({ nodes: [{ id: 'domain:a', kind: 'domain' }], edges: [] });
  });

  it('expanding a domain reveals its sessions frame by frame', async () => {
    routes['/api/trace/sessions'] = () => ok(sessions);
    load();
    await tick(0);
    applied.length = 0;
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    expect(status()).toBe('Loading sessions...');
    await tick(1);
    expect(status()).toMatch(/Revealing 2 sessions|2 sessions/);
    await tick(100);
    expect(status()).toBe('2 sessions');
    expect(appliedIds()).toEqual(expect.arrayContaining(['session:s1', 'session:s2']));
    const edgeCount = applied.reduce((n, a) => n + a.edges.length, 0);
    expect(edgeCount).toBe(2);
    // second click on the same domain is a no-op
    const calls = fetch.mock.calls.length;
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    expect(fetch.mock.calls.length).toBe(calls);
  });

  it('a failed domain expansion can be retried', async () => {
    routes['/api/trace/sessions'] = () => bad();
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    await tick(1);
    expect(status()).toBe('Sessions failed: HTTP 500');
    routes['/api/trace/sessions'] = () => ok(sessions);
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    await tick(100);
    expect(status()).toBe('2 sessions');
  });

  it('expanding a session reveals the chain in causal batches with a tail', async () => {
    const chain = {
      nodes: [
        { id: 'file:f', kind: 'file' },
        { id: 'act:2', kind: 'action', seq: 2, tool: 'Read' },
        { id: 'p:1', kind: 'prompt', seq: 1 },
        { id: 'p:1b', kind: 'prompt', seq: 1 },
        { id: 'loose', kind: 'memory' },
        null,
      ],
      edges: [
        { source: 'session:s1', target: 'p:1', kind: 'step' },
        { source: 'p:1', target: 'act:2', kind: 'next' },
        { source: { id: 'act:2' }, target: { id: 'file:f' }, kind: 'read' },
        { source: 'act:2', target: 'ghost', kind: 'read' },
        { source: 'orphan', target: 'orphan2', kind: 'x' },
        null,
      ],
      meta: { event_count: 3 },
      next_since: 42,
    };
    routes['/api/trace/chain'] = () => ok(chain);
    load();
    await tick(0);
    applied.length = 0;
    J.emit('graph:selectNode', { id: 'session:s1', kind: 'session', session_id: 's1' });
    expect(status()).toBe('Loading chain...');
    await tick(1);
    expect(status()).toMatch(/Revealing chain|chain - 3 steps/);
    await tick(200);
    expect(status()).toBe('chain - 3 steps');
    const ids = appliedIds();
    expect(ids.indexOf('p:1')).toBeLessThan(ids.indexOf('act:2'));
    expect(ids).toContain('file:f'); // revealed with the action that observed it
    expect(ids).toContain('loose'); // tail batch
    // every edge is delivered exactly once (dangling ones in the tail batch)
    expect(applied.reduce((n, a) => n + a.edges.length, 0)).toBe(6);
    // the live timer was armed from next_since
    routes['/api/trace/chain?session=s1&since=42'] = () =>
      ok({ nodes: [{ id: 'act:3', kind: 'action', tool: 'Bash' }, { id: 'p:3', type: 'prompt' }, { id: 'f3', kind: 'file' }], edges: [], next_since: 50 });
    routes['/api/trace/sessions'] = () => ok({ nodes: [] });
    await tick(4000);
    expect(status()).toBe('● live · 2 new in s1');
    expect(appliedIds()).toContain('act:3');
    // cursor advanced
    routes['/api/trace/chain?session=s1&since=50'] = () => ok({ nodes: [], edges: [], next_since: 50 });
    await tick(4000);
    expect(fetch.mock.calls.some((c) => String(c[0]).includes('since=50'))).toBe(true);
  });

  it('session without session_id derives it from the node id; failures reset expansion', async () => {
    routes['/api/trace/chain'] = () => bad();
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'session:abc', type: 'session' });
    await tick(1);
    expect(fetch.mock.calls.some((c) => c[0] === '/api/trace/chain?session=abc')).toBe(true);
    expect(status()).toBe('Chain failed: HTTP 500');
    const n = fetch.mock.calls.length;
    J.emit('graph:selectNode', { id: 'session:abc', type: 'session' });
    expect(fetch.mock.calls.length).toBe(n + 1); // retry allowed
  });

  it('ignores ids-less nodes, files and non-trace selections; unmounted views do not expand', async () => {
    load();
    await tick(0);
    const n = fetch.mock.calls.length;
    J.emit('graph:selectNode', null);
    J.emit('graph:selectNode', {});
    J.emit('graph:selectNode', { id: 'file:x', kind: 'file' });
    J.emit('graph:selectNode', { id: 'file:x', kind: 'file' });
    expect(fetch.mock.calls.length).toBe(n);
    J.state.activeView = 'board';
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    expect(fetch.mock.calls.length).toBe(n);
  });

  it('hiding the view cancels in-flight reveals so they can be retried', async () => {
    const many = {
      nodes: Array.from({ length: 6 }, (_, i) => ({ id: 'a' + i, kind: 'action', seq: i })),
      edges: [],
    };
    routes['/api/trace/chain'] = () => ok(many);
    load();
    await tick(0);
    applied.length = 0;
    // a performance clock that jumps past the frame budget forces one batch per frame
    let t = 0;
    vi.spyOn(window.performance, 'now').mockImplementation(() => (t += 30));
    J.emit('graph:selectNode', { id: 'session:s9', kind: 'session', session_id: 's9' });
    await tick(1);
    await tick(17);
    const partial = applied.length;
    expect(partial).toBeGreaterThan(0);
    expect(partial).toBeLessThan(6);
    J.state.activeView = 'board'; // hide: cancel + allow retry
    await tick(200);
    expect(applied.length).toBe(partial);
    J.state.activeView = 'trace';
    routes['/api/trace/domains'] = () => ok({ nodes: [], edges: [] });
    J.emit('graph:selectNode', { id: 'session:s9', kind: 'session', session_id: 's9' });
    await tick(300);
    expect(applied.length).toBeGreaterThan(partial);
  });

  it('a late response after the view was cleared is discarded', async () => {
    let release;
    routes['/api/trace/sessions'] = () =>
      new Promise((r) => {
        release = () => r({ ok: true, json: () => Promise.resolve(sessions) });
      });
    load();
    await tick(0);
    applied.length = 0;
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    window.TraceView.reload(); // _clearGraph bumps the epoch
    release();
    await tick(100);
    expect(appliedIds()).not.toContain('session:s1');

    let release2;
    routes['/api/trace/chain'] = () =>
      new Promise((r) => {
        release2 = () => r({ ok: true, json: () => Promise.resolve({ nodes: [{ id: 'q', seq: 1 }], edges: [] }) });
      });
    J.emit('graph:selectNode', { id: 'session:z', kind: 'session', session_id: 'z' });
    window.TraceView.reload();
    release2();
    await tick(100);
    expect(appliedIds()).not.toContain('q');
  });

  it('a response that lands after unmount re-opens the node for later', async () => {
    let release;
    routes['/api/trace/sessions'] = () =>
      new Promise((r) => {
        release = () => r({ ok: true, json: () => Promise.resolve(sessions) });
      });
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    J.state.activeView = 'board';
    release();
    await tick(100);
    expect(appliedIds()).not.toContain('session:s1');
  });

  it('works without a frame scheduler (setTimeout fallback)', async () => {
    const raf = window.requestAnimationFrame;
    window.requestAnimationFrame = undefined;
    routes['/api/trace/sessions'] = () => ok(sessions);
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    await tick(100);
    expect(status()).toBe('2 sessions');
    J.state.activeView = 'board'; // cancel path via clearTimeout
    window.requestAnimationFrame = raf;
  });
});

describe('live tail', () => {
  beforeEach(() => {
    routes['/api/trace/domains'] = () => ok({ nodes: [{ id: 'domain:a', kind: 'domain' }], edges: [] });
  });

  it('surfaces new sessions in an expanded domain', async () => {
    let count = 1;
    routes['/api/trace/sessions'] = () =>
      ok({ nodes: Array.from({ length: count }, (_, i) => ({ id: 'session:' + i, kind: 'session' })), edges: [] });
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    await tick(100);
    count = 2;
    await tick(4000);
    expect(status()).toBe('● live · +new session in a');
    // failures during the tick are swallowed
    routes['/api/trace/sessions'] = () => bad();
    await tick(4000);
  });

  it('setLive pauses and resumes the poller and reports status', async () => {
    routes['/api/trace/sessions'] = () => ok({ nodes: [{ id: 'session:1', kind: 'session' }], edges: [] });
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'domain:a', kind: 'domain' });
    await tick(100);
    window.TraceView.setLive(false);
    expect(window.TraceView.isLive()).toBe(false);
    expect(status()).toBe('○ live paused');
    const calls = fetch.mock.calls.length;
    await tick(9000);
    expect(fetch.mock.calls.length).toBe(calls);
    window.TraceView.setLive(true);
    expect(status()).toBe('● live on');
    await tick(4100);
    expect(fetch.mock.calls.length).toBeGreaterThan(calls);
    // switching away stops the timer; switching back re-arms it
    J.state.activeView = 'board';
    const c2 = fetch.mock.calls.length;
    await tick(9000);
    expect(fetch.mock.calls.length).toBe(c2);
  });
});

describe('acceptActivityBatch', () => {
  beforeEach(() => {
    routes['/api/trace/domains'] = () => ok({ nodes: [], edges: [] });
    routes['/api/trace/chain'] = () => ok({ nodes: [{ id: 'p', seq: 1 }], edges: [], next_since: 1 });
  });

  async function expanded() {
    load();
    await tick(0);
    J.emit('graph:selectNode', { id: 'session:s1', kind: 'session', session_id: 's1' });
    await tick(100);
    applied.length = 0;
  }

  it('accepts only fragments rooted at an expanded session and follows outgoing edges', async () => {
    await expanded();
    const A = window.TraceView.acceptActivityBatch;
    const nodes = [
      { id: 'session:s1', kind: 'session' },
      { id: 'act:1', kind: 'action', tool: 'Bash' },
      { id: 'tool:t', type: 'tool_hub' },
      { id: 'session:other', kind: 'session' },
      { id: 'act:other', kind: 'action' },
      null,
    ];
    const edges = [
      { source: 'session:s1', target: 'act:1', kind: 'did' },
      { source: { id: 'act:1' }, target: { id: 'tool:t' }, type: 'call' },
      { source: 'session:other', target: 'act:other', kind: 'did', id: 'e-other' },
      { source: 'act:1', target: null, kind: 'broken' },
      { target: 'x' },
      null,
    ];
    expect(A(nodes, edges)).toBe(true);
    expect(appliedIds().sort()).toEqual(['act:1', 'session:s1', 'tool:t']);
    expect(applied[0].edges.length).toBe(2);
    expect(status()).toBe('● live · 1 new live action');
    // two actions pluralise
    applied.length = 0;
    A([{ id: 'session:s1', kind: 'session' }, { id: 'a1', kind: 'action' }, { id: 'a2', type: 'prompt' }],
      [{ source: 'session:s1', target: 'a1', id: 'ea1' }, { source: 'a1', target: 'a2', id: 'ea2' }]);
    expect(status()).toBe('● live · 2 new live actions');
  });

  it('accepts a root that is only named by an edge; rejects unknown sessions', async () => {
    await expanded();
    const A = window.TraceView.acceptActivityBatch;
    expect(A([{ id: 'act:z', kind: 'file' }], [{ source: 'session:s1', target: 'act:z', kind: 'did' }])).toBe(true);
    expect(A([{ id: 'session:nope', kind: 'session' }], [])).toBe(false);
    expect(A(undefined, undefined)).toBe(false);
    expect(A([{ id: 'session:s1', kind: 'session' }], [])).toBe(true);
  });

  it('rejects everything while paused or unmounted', async () => {
    await expanded();
    const A = window.TraceView.acceptActivityBatch;
    window.TraceView.setLive(false);
    expect(A([{ id: 'session:s1', kind: 'session' }], [])).toBe(false);
    window.TraceView.setLive(true);
    J.state.activeView = 'board';
    expect(A([{ id: 'session:s1', kind: 'session' }], [])).toBe(false);
  });
});

describe('impact diagram', () => {
  beforeEach(() => {
    routes['/api/trace/domains'] = () => ok({ nodes: [], edges: [] });
    load();
  });

  const full = {
    available: true,
    center: { label: 'core.py' },
    depends_on: [{ file: 'a.py', label: 'a', edges: 3, kinds: ['imports', 'calls'] }, { edges: 0 }],
    depended_on_by: Array.from({ length: 41 }, (_, i) => ({ file: 'u' + i + '.py' })),
    references: [{ file: 'doc.md', name: 'doc', kind: 'uses', confidence: 0.5 }],
    referenced_by: [{ file: 'r.md', label: 'r', kind: 'zz', confidence: 1 }],
    processes: [{ label: 'flow', kind: 'http', depth: 2, symbol_count: 4 }, { entry: 'e' }].concat(
      Array.from({ length: 30 }, () => ({}))
    ),
    downstream: Array.from({ length: 62 }, (_, i) => ({ file: 'd' + i, kind: 'calls' })),
    upstream: [{ file: 'up.py', kind: 'member_of', confidence: 0.2 }],
    members: [{ name: 'M' }],
    versions: { available: true, versions: [{ subject: 'init', sha: 'abc', date: '2026-01-01T00:00' }] },
  };

  it('renders every group and wires box clicks to node selection', async () => {
    routes['/api/trace/impact'] = () => ok(full);
    J.state.lastData = { nodes: [{ id: 'file:a.py', kind: 'file' }] };
    window.TraceView.showImpact('/repo/src/a_very_long_file_name_for_title_truncation.py');
    const panel = document.getElementById('flow-panel');
    expect(panel.classList.contains('open')).toBe(true);
    expect(panel.classList.contains('with-detail')).toBe(true);
    expect(document.getElementById('flow-content').textContent).toContain('analyzing');
    await tick(0);
    const c = document.getElementById('flow-content');
    expect(c.querySelector('.impact-center').textContent).toBe('core.py');
    expect(c.textContent).toContain('Depends on (files)');
    expect(c.textContent).toContain('3 · imports/calls');
    expect(c.textContent).toContain('1 more'); // 41 depended-on-by, cap 40
    expect(c.textContent).toContain('2 more'); // 62 downstream, cap 60
    expect(c.textContent).toContain('Causal chains');
    expect(c.textContent).toContain('http · d2 · 4 sym');
    expect(c.textContent).toContain('d? · ? sym');
    expect(c.textContent).toContain('Versions');
    expect(c.textContent).toContain('2026-01-01');
    expect(c.textContent).toContain('50%');
    expect(c.textContent).not.toContain('100%');
    const seen = [];
    J.on('graph:selectNode', (n) => seen.push(n.id));
    c.querySelector('.impact-box[data-file="a.py"]').click();
    c.querySelector('.impact-box[data-file="r.md"]').click();
    expect(seen).toEqual(['file:a.py']);
    document.getElementById('detail-panel').classList.remove('open');
    window.TraceView.showImpact('x.py');
    expect(panel.classList.contains('with-detail')).toBe(false);
  });

  it('renders unavailable, empty and failed states', async () => {
    routes['/api/trace/impact'] = () => ok({ available: false, reason: 'not indexed yet<' });
    window.TraceView.showImpact('x.py');
    await tick(0);
    expect(document.getElementById('flow-content').innerHTML).toContain('not indexed yet&lt;');
    routes['/api/trace/impact'] = () => ok(null);
    window.TraceView.showImpact('x.py');
    await tick(0);
    expect(document.getElementById('flow-content').textContent).toContain('not indexed');
    routes['/api/trace/impact'] = () => ok({ available: true });
    window.TraceView.showImpact('x.py');
    await tick(0);
    expect(document.getElementById('flow-content').textContent).toContain('No dependencies found');
    expect(document.querySelector('.impact-center').textContent).toBe('this file');
    routes['/api/trace/impact'] = () => bad();
    window.TraceView.showImpact('x.py');
    await tick(0);
    expect(document.getElementById('flow-content').textContent).toContain('Impact failed: HTTP 500');
  });

  it('is inert without a flow panel, and the close button closes it', () => {
    document.getElementById('flow-panel').classList.add('open');
    document.getElementById('flow-close').click();
    expect(document.getElementById('flow-panel').classList.contains('open')).toBe(false);
    document.getElementById('flow-panel').remove();
    window.TraceView.showImpact('x.py');
  });
});
