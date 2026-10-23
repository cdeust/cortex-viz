// Behaviour tests for workflow_graph_filters.js (DOM wiring: layer select,
// reset, legacy buttons, domain select, search box) and
// workflow_graph_bridge.js (renderer takeover, incremental append, debounce,
// view switching). The real state bus is used; only the renderer is a stub.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

const registered = [];
function trackListeners() {
  for (const t of [window, document, document.body]) {
    const orig = t.addEventListener.bind(t);
    vi.spyOn(t, 'addEventListener').mockImplementation((type, fn, o) => {
      registered.push([t, type, fn]);
      return orig(type, fn, o);
    });
  }
}
function dropListeners() {
  registered.splice(0).forEach(([t, type, fn]) => t.removeEventListener(type, fn));
}

function freshJUG(extra) {
  window.JUG = Object.assign({}, extra);
  globalThis.JUG = window.JUG;
  loadScript('ui/unified/js/state.js');
  return window.JUG;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  trackListeners();
});
afterEach(() => {
  dropListeners();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('filters wiring', () => {
  let J;
  let applied;

  const FILTER_DOM = `
    <select id="wfg-filter-select"><option value="all">all</option><option value="L6">L6</option>
      <option value="kind:file">f</option><option value="edge:calls">e</option><option value="L3">L3</option></select>
    <button id="wfg-filter-reset"></button>
    <button class="filter-btn" data-wfg-filter="L3"><span id="inner"></span></button>
    <button class="filter-btn" data-wfg-filter="all"></button>
    <select id="domain-select"><option value="">All Domains</option></select>
    <input id="search-box">`;

  function load(extra) {
    document.body.innerHTML = FILTER_DOM;
    applied = [];
    J = freshJUG(Object.assign({ wfgApplyFilter: (p) => applied.push(p) }, extra));
    loadScript('ui/unified/js/workflow_graph_filters.js');
  }

  const lastPred = () => applied[applied.length - 1];
  const ctx = { byId: {}, edges: [] };

  it('layer select applies a predicate for the chosen layer', () => {
    load();
    const sel = document.getElementById('wfg-filter-select');
    sel.value = 'L3';
    sel.dispatchEvent(new Event('change'));
    expect(applied.length).toBe(1);
    expect(lastPred()({ kind: 'file' }, ctx)).toBe(true);
    expect(lastPred()({ kind: 'skill' }, ctx)).toBe(false);
  });

  it('symbol-revealing selections load deferred symbols first', async () => {
    const load6 = vi.fn(() => Promise.resolve());
    load({ loadSymbolPhases: load6, hasDeferredSymbols: () => true });
    const sel = document.getElementById('wfg-filter-select');
    for (const v of ['L6', 'kind:file', 'edge:calls']) {
      sel.value = v;
      sel.dispatchEvent(new Event('change'));
    }
    // kind:file does not need symbols
    expect(load6).toHaveBeenCalledTimes(2);
    expect(applied.length).toBe(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(applied.length).toBe(3);
  });

  it('does not load symbols when none are deferred', () => {
    const load6 = vi.fn(() => Promise.resolve());
    load({ loadSymbolPhases: load6, hasDeferredSymbols: () => false });
    const sel = document.getElementById('wfg-filter-select');
    sel.value = 'L6';
    sel.dispatchEvent(new Event('change'));
    expect(load6).not.toHaveBeenCalled();
    expect(applied.length).toBe(1);
  });

  it('reset returns to all and legacy buttons update selection and active class', () => {
    load();
    const sel = document.getElementById('wfg-filter-select');
    document.getElementById('inner').click();
    expect(sel.value).toBe('L3');
    expect(document.querySelector('.filter-btn[data-wfg-filter="L3"]').classList.contains('active')).toBe(true);
    expect(lastPred()({ kind: 'skill' }, ctx)).toBe(false);
    document.getElementById('wfg-filter-reset').click();
    expect(sel.value).toBe('all');
    expect(lastPred()({ kind: 'skill' }, ctx)).toBe(true);
    // clicks elsewhere are ignored
    const n = applied.length;
    document.body.click();
    expect(applied.length).toBe(n);
  });

  it('domain select populates from selectable domains and filters', () => {
    load();
    J.state.lastData = {
      nodes: [
        { id: 'domain:b', kind: 'domain', label: 'Beta', selectableDomain: true },
        { id: 'domain:a', kind: 'domain', selectableDomain: true },
        { id: 'domain:g', kind: 'domain' },
      ],
    };
    const sel = document.getElementById('domain-select');
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(['', 'Beta', 'a']);
    sel.value = 'Beta';
    sel.dispatchEvent(new Event('change'));
    const byId = { 'domain:b': { label: 'Beta' } };
    expect(lastPred()({ kind: 'domain', id: 'domain:b', label: 'Beta' }, { byId, edges: [] })).toBe(true);
    expect(lastPred()({ kind: 'file', id: 'f', domain_id: 'domain:zz' }, { byId, edges: [] })).toBe(false);
    // repopulate keeps the selection; empty graph never wipes the dropdown
    J.state.lastData = { nodes: [{ id: 'domain:b', kind: 'domain', label: 'Beta', selectableDomain: true }] };
    expect(sel.value).toBe('Beta');
    J.state.lastData = { nodes: [] };
    expect(sel.options.length).toBe(2);
    J.state.lastData = { notNodes: 1 };
    expect(sel.options.length).toBe(2);
  });

  it('domain selection loads that domain symbols when deferred', async () => {
    const forDomain = vi.fn(() => Promise.resolve());
    load({ loadSymbolPhasesForDomain: forDomain, hasDeferredSymbols: () => true });
    const sel = document.getElementById('domain-select');
    sel.innerHTML = '<option value="">All</option><option value="Beta">Beta</option>';
    sel.value = 'Beta';
    sel.dispatchEvent(new Event('change'));
    expect(forDomain).toHaveBeenCalledWith('Beta');
    await vi.advanceTimersByTimeAsync(0);
    expect(applied.length).toBe(1);
    sel.value = '';
    sel.dispatchEvent(new Event('change'));
    expect(forDomain).toHaveBeenCalledTimes(1);
    expect(applied.length).toBe(2);
  });

  it('search debounces input and applies a text predicate', () => {
    load();
    const box = document.getElementById('search-box');
    box.value = ' alpha ';
    box.dispatchEvent(new Event('input'));
    box.value = ' beta ';
    box.dispatchEvent(new Event('input'));
    vi.advanceTimersByTime(130);
    expect(applied.length).toBe(1);
    expect(lastPred()({ label: 'BETA thing', id: 'x' }, ctx)).toBe(true);
    expect(lastPred()({ label: 'alpha', id: 'x' }, ctx)).toBe(false);
  });

  it('new lastData triggers a delayed apply; apply is inert without wfgApplyFilter', () => {
    load();
    J.state.lastData = { nodes: [] };
    vi.advanceTimersByTime(60);
    expect(applied.length).toBe(1);
    delete J.wfgApplyFilter;
    J.state.lastData = { nodes: [{ id: 'q' }] };
    vi.advanceTimersByTime(60);
    expect(applied.length).toBe(1);
  });

  it('missing controls are tolerated and boot waits for the JUG bus', () => {
    document.body.innerHTML = '';
    window.JUG = {};
    globalThis.JUG = window.JUG;
    loadScript('ui/unified/js/workflow_graph_filters.js');
    vi.advanceTimersByTime(60); // retry: still no bus
    loadScript('ui/unified/js/state.js');
    vi.advanceTimersByTime(60); // retry succeeds with no DOM controls
    document.body.click();
  });

  it('defers boot to DOMContentLoaded while the document is loading', () => {
    const spy = vi.spyOn(document, 'readyState', 'get').mockReturnValue('loading');
    document.body.innerHTML = FILTER_DOM;
    applied = [];
    J = freshJUG({ wfgApplyFilter: (p) => applied.push(p) });
    loadScript('ui/unified/js/workflow_graph_filters.js');
    document.getElementById('wfg-filter-reset').click();
    expect(applied.length).toBe(0);
    spy.mockRestore();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    document.getElementById('wfg-filter-reset').click();
    expect(applied.length).toBe(1);
  });
});

describe('bridge', () => {
  let J;
  let handle;
  let renders;

  const wfgData = (n = 2, extra) => ({
    nodes: Array.from({ length: n }, (_, i) => ({ id: 'n' + i, kind: 'file' })),
    edges: [],
    meta: { schema: 'workflow_graph.v1' },
    ...extra,
  });
  const traceData = () => ({ nodes: [{ id: 't', kind: 'session' }], edges: [], meta: { schema: 'trace.v1' } });

  function load(opts) {
    opts = opts || {};
    document.body.innerHTML = '<div id="graph-container"><canvas id="legacy"></canvas></div>';
    renders = [];
    handle = { destroy: vi.fn(), append: vi.fn(), reflow: vi.fn() };
    J = freshJUG(
      opts.noRenderer
        ? {}
        : {
            renderWorkflowGraph: vi.fn((wrapper, data) => {
              renders.push(data);
              return handle;
            }),
          }
    );
    Object.assign(J, opts.jug || {});
    loadScript('ui/unified/js/workflow_graph_bridge.js');
  }

  it('isWorkflowGraph recognises schemas and known kinds', () => {
    load();
    const is = J.isWorkflowGraph;
    expect(is(null)).toBe(false);
    expect(is({ nodes: [] })).toBe(false);
    expect(is({ nodes: [{ id: 'x' }], meta: { schema: 'trace.v1' } })).toBe(true);
    expect(is({ nodes: [{ id: 'x' }], meta: { schema: 'workflow_graph.v1' } })).toBe(true);
    expect(is({ nodes: [{ id: 'x', kind: 'mcp' }] })).toBe(true);
    expect(is({ nodes: [{ id: 'x', type: 'memory' }, { id: 'y', kind: 'weird' }] })).toBe(false);
  });

  it('renders into a wrapper, evicts legacy children, flags wfg active', async () => {
    load();
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(true);
    const host = document.getElementById('graph-container');
    expect(document.getElementById('wfg-container')).not.toBeNull();
    expect(document.getElementById('legacy')).toBeNull();
    expect(J.__wfgActive).toBe(true);
    // legacy children that re-materialise are evicted by the observer
    host.appendChild(document.createElement('canvas'));
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    expect(host.querySelectorAll('canvas').length).toBe(0);
    // re-render destroys the previous handle
    J.renderWorkflowGraphIntoTab(wfgData(3));
    expect(handle.destroy).toHaveBeenCalledTimes(1);
  });

  it('pauses the legacy force graph when present and survives its errors', () => {
    const pause = vi.fn(() => {
      throw new Error('x');
    });
    load({ jug: { getGraph: () => ({ pauseAnimation: pause }) } });
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(true);
    expect(pause).toHaveBeenCalled();
    J.getGraph = () => null;
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(true);
  });

  it('a throwing destroy does not stop the new render', () => {
    load();
    J.renderWorkflowGraphIntoTab(wfgData());
    handle.destroy = vi.fn(() => {
      throw new Error('d');
    });
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(true);
  });

  it('fails soft without a container, or when the renderer throws', () => {
    load();
    document.body.innerHTML = '';
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(false);
    document.body.innerHTML = '<div id="graph-container"></div>';
    J.renderWorkflowGraph = () => {
      throw new Error('render');
    };
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(false);
    expect(console.error).toHaveBeenCalled();
  });

  it('retries until the renderer is defined', () => {
    load({ noRenderer: true });
    expect(J.renderWorkflowGraphIntoTab(wfgData())).toBe(false);
    J.renderWorkflowGraph = vi.fn(() => handle);
    vi.advanceTimersByTime(100);
    expect(J.renderWorkflowGraph).toHaveBeenCalledTimes(1);
  });

  it('renders already-present lastData on attach', () => {
    document.body.innerHTML = '<div id="graph-container"></div>';
    renders = [];
    handle = { destroy() {}, append: vi.fn() };
    window.JUG = {};
    globalThis.JUG = window.JUG;
    loadScript('ui/unified/js/state.js');
    window.JUG.renderWorkflowGraph = (w, d) => {
      renders.push(d);
      return handle;
    };
    window.JUG.state.lastData = wfgData();
    loadScript('ui/unified/js/workflow_graph_bridge.js');
    expect(renders.length).toBe(1);
  });

  it('waits for the JUG bus before attaching', () => {
    document.body.innerHTML = '<div id="graph-container"></div>';
    window.JUG = {};
    globalThis.JUG = window.JUG;
    loadScript('ui/unified/js/workflow_graph_bridge.js');
    loadScript('ui/unified/js/state.js');
    window.JUG.renderWorkflowGraph = vi.fn(() => ({}));
    vi.advanceTimersByTime(60);
    window.JUG.state.lastData = wfgData();
    vi.advanceTimersByTime(500);
    expect(window.JUG.renderWorkflowGraph).toHaveBeenCalled();
  });

  it('debounces data changes: first render after 400ms, later after 500ms', () => {
    load();
    J.state.lastData = wfgData(2);
    vi.advanceTimersByTime(399);
    expect(renders.length).toBe(0);
    vi.advanceTimersByTime(2);
    expect(renders.length).toBe(1);
    J.state.lastData = wfgData(3);
    J.state.lastData = wfgData(4);
    vi.advanceTimersByTime(499);
    expect(renders.length).toBe(1);
    vi.advanceTimersByTime(2);
    expect(renders.length).toBe(2);
    expect(renders[1].nodes.length).toBe(4);
  });

  it('non-workflow payloads are ignored', () => {
    load();
    J.state.lastData = { nodes: [{ id: 'x', type: 'memory' }] };
    vi.advanceTimersByTime(1000);
    expect(renders.length).toBe(0);
  });

  it('a safety deadline forces a render while data keeps streaming', () => {
    load();
    for (let i = 0; i < 20; i++) {
      J.state.lastData = wfgData(2 + i);
      vi.advanceTimersByTime(300);
    }
    expect(renders.length).toBeGreaterThanOrEqual(1);
  });

  it('big graphs rebuild on the slow cadence', () => {
    load();
    J.state.lastData = wfgData(2);
    vi.advanceTimersByTime(450);
    expect(renders.length).toBe(1);
    vi.advanceTimersByTime(5000); // let the first safety deadline lapse
    J.state.lastData = wfgData(15001);
    vi.advanceTimersByTime(7000);
    expect(renders.length).toBe(1);
    vi.advanceTimersByTime(1100);
    expect(renders.length).toBe(2);
  });

  it('streamed deltas append incrementally once rendered, without remounting', () => {
    load();
    J.state.lastData = wfgData(2);
    vi.advanceTimersByTime(450);
    expect(renders.length).toBe(1);
    const data = J.state.lastData;
    J.emit('state:lastData', {
      value: data,
      delta: { nodes: [{ id: 'new', kind: 'file' }], edges: [{ source: 'a', target: 'b' }] },
    });
    expect(handle.append).toHaveBeenCalledWith(
      [{ id: 'new', kind: 'file' }],
      [{ source: 'a', target: 'b' }],
      { topologyAware: false }
    );
    vi.advanceTimersByTime(1000);
    expect(renders.length).toBe(1);
    // edges-only delta, and trace payload deltas are topology aware
    J.emit('state:lastData', { value: data, delta: { edges: [{ source: 'a', target: 'b' }] } });
    expect(handle.append).toHaveBeenLastCalledWith([], [{ source: 'a', target: 'b' }], { topologyAware: false });
  });

  it('falls back to a remount when append throws or the view differs', () => {
    load();
    J.state.lastData = wfgData(2);
    vi.advanceTimersByTime(450);
    handle.append = vi.fn(() => {
      throw new Error('append');
    });
    const data = J.state.lastData;
    J.emit('state:lastData', { value: data, delta: { nodes: [{ id: 'z' }] } });
    expect(console.warn).toHaveBeenCalled();
    vi.advanceTimersByTime(600);
    expect(renders.length).toBe(2);
    handle.append = vi.fn();
    // trace payload while the handle holds a graph payload: no append
    const t = traceData();
    J.emit('state:lastData', { value: t, delta: { nodes: [{ id: 'z' }] } });
    expect(handle.append).not.toHaveBeenCalled();
    vi.advanceTimersByTime(600);
    expect(renders[renders.length - 1]).toBe(t);
    // now the handle holds trace: a trace delta is topology aware
    J.emit('state:lastData', { value: t, delta: { nodes: [{ id: 'q' }] } });
    expect(handle.append).toHaveBeenCalledWith([{ id: 'q' }], [], { topologyAware: true });
  });

  it('view switching toggles container display and re-renders that view payload', () => {
    load();
    const g = wfgData(2);
    const t = traceData();
    J.state.lastData = g;
    vi.advanceTimersByTime(450);
    J.state.lastData = t;
    vi.advanceTimersByTime(600);
    const base = renders.length;
    const host = document.getElementById('graph-container');
    J.state.activeView = 'board';
    expect(host.style.display).toBe('none');
    J.state.activeView = 'graph';
    expect(host.style.display).toBe('');
    expect(document.getElementById('wfg-container').style.display).toBe('block');
    expect(renders[renders.length - 1]).toBe(g);
    J.state.activeView = 'trace';
    expect(renders[renders.length - 1]).toBe(t);
    expect(renders.length).toBe(base + 2);
  });

  it('switching to the view that was just rendered re-renders its cached payload', () => {
    load();
    J.state.lastData = wfgData(2);
    vi.advanceTimersByTime(450);
    J.state.activeView = 'graph';
    expect(renders.length).toBe(2);
  });

  it('view switch with nothing rendered yet does nothing', () => {
    load();
    J.state.activeView = 'graph';
    J.state.activeView = 'trace';
    expect(renders.length).toBe(0);
    document.body.innerHTML = '';
    J.state.activeView = 'board';
  });

  it('defers attach to DOMContentLoaded while the document is loading', () => {
    const spy = vi.spyOn(document, 'readyState', 'get').mockReturnValue('loading');
    load();
    J.state.lastData = wfgData();
    vi.advanceTimersByTime(1000);
    expect(renders.length).toBe(0);
    spy.mockRestore();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    expect(renders.length).toBe(1); // attach renders the existing lastData
    J.state.lastData = wfgData(3);
    vi.advanceTimersByTime(500);
    expect(renders.length).toBe(2);
  });
});
