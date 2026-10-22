// Behaviour tests for ui/unified/js/polling.js: progress poll, stats HUD,
// system vitals, benchmark summary, clock, view-driven polling.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

const IDS = [
  's-dom', 's-mem', 's-mem-raw', 's-ent', 's-disc', 's-nodes', 's-edge', 'sb-nodes', 'sb-edges',
  'status-text', 'status-time', 'sv-heat', 'sv-labile', 'sv-eltp', 'sv-lltp', 'sv-cons',
  'sv-recon', 'sv-skills', 'sv-inferred', 'sv-confab', 'sv-habituated', 'sv-extinguished',
  'sv-conflict', 'sv-familiarity', 'sv-sleepphase', 'sv-tmr', 'sv-stress', 'sv-goal',
  'sv-prederr', 'sv-attn', 'b-lme', 'b-loc', 'b-beam',
];

const txt = (id) => document.getElementById(id).textContent;

function jsonResponse(body, ok = true) {
  return Promise.resolve({ ok, status: ok ? 200 : 500, json: () => Promise.resolve(body) });
}

let J;
let routes;

function load(extra) {
  document.body.innerHTML =
    IDS.map((i) => `<div id="${i}"></div>`).join('') +
    '<div id="loading"></div><div id="vitals-toggle" style="display:none"></div>' +
    '<div id="benchmark-summary" style="display:none"></div>';
  J = Object.assign(
    {
      API_URL: '/api/graph',
      buildGraph: vi.fn(),
      addBatchToGraph: vi.fn(),
    },
    extra
  );
  globalThis.JUG = window.JUG = J;
  loadScript('ui/unified/js/state.js');
  loadScript('ui/unified/js/polling.js');
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  routes = {};
  globalThis.fetch = vi.fn((url) => {
    const key = Object.keys(routes).find((k) => String(url).startsWith(k));
    return key ? routes[key](url) : Promise.reject(new Error('no route ' + url));
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete globalThis.fetch;
});

describe('progress poll', () => {
  it('stays on standby unless the graph view is active', () => {
    load();
    J.state.activeView = 'board';
    J._pollTest.fetchGraph();
    expect(txt('status-text')).toBe('Online — graph standby');
    expect(document.getElementById('loading').classList.contains('done')).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1200);
    expect(document.getElementById('loading')).toBeNull();
  });

  it('reports a building phase, keeps polling, then Ready', async () => {
    load();
    await vi.advanceTimersByTimeAsync(600); // let the boot timers pass (standby view)
    J.state.activeView = 'graph';
    let call = 0;
    routes['/api/graph/progress'] = () => {
      call++;
      if (call === 1) return jsonResponse({ phase: 'embed', pct: 0.42, node_count: 7, edge_count: 3 });
      if (call === 2) return jsonResponse({ baseline_ready: true, pct: 0.5, indeterminate: false });
      if (call === 3) return jsonResponse({ phase: 'drl', indeterminate: true, phase_elapsed: 180 });
      return jsonResponse({ full_ready: true, node_count: 9, edge_count: 4 });
    };
    await vi.advanceTimersByTimeAsync(60);
    expect(txt('status-text')).toBe('Building · embed (42%)');
    expect(txt('s-nodes')).toBe('7');
    expect(J.__progress.phase).toBe('embed');
    await vi.advanceTimersByTimeAsync(2000);
    expect(txt('status-text')).toBe('Baseline ready · loading L6 (50%)');
    await vi.advanceTimersByTimeAsync(2000);
    expect(txt('status-text')).toBe('Building · drl (3 min elapsed)');
    await vi.advanceTimersByTimeAsync(2000);
    expect(txt('status-text')).toBe('Ready · 9 nodes');
    expect(call).toBe(4);
    await vi.advanceTimersByTimeAsync(5000);
    expect(call).toBe(4); // stopped polling after full_ready
  });

  it('uses the local node count once the phase loader has data', async () => {
    load();
    J.state.activeView = 'graph';
    J.state.lastData = { nodes: [{ id: 'a', type: 'memory' }], edges: [] };
    routes['/api/graph/progress'] = () => jsonResponse({ full_ready: true, node_count: 99 });
    J._pollTest.fetchGraph();
    await vi.advanceTimersByTimeAsync(0);
    expect(txt('status-text')).toBe('Ready · 1 nodes');
  });

  it('treats an HTTP error as a failure and retries', async () => {
    load();
    await vi.advanceTimersByTimeAsync(600);
    J.state.activeView = 'graph';
    routes['/api/graph/progress'] = () => jsonResponse({}, false);
    await vi.advanceTimersByTimeAsync(60);
    expect(J._pollTest.failures()).toBe(1);
    expect(console.warn).toHaveBeenCalledWith('[cortex] progress poll error:', 'HTTP 500');
    await vi.advanceTimersByTimeAsync(4000);
    expect(J._pollTest.failures()).toBe(2);
  });

  it('boot schedule polls graph and stats; switching to graph re-triggers', async () => {
    load();
    routes['/api/stats'] = () => jsonResponse({ memory_count: 5 });
    routes['/api/graph/progress'] = () => jsonResponse({ full_ready: true });
    await vi.advanceTimersByTimeAsync(600);
    expect(fetch.mock.calls.some((c) => c[0] === '/api/stats')).toBe(true);
    // standby on the default (trace) view -> no progress fetch
    expect(fetch.mock.calls.some((c) => c[0] === '/api/graph/progress')).toBe(false);
    J.state.activeView = 'graph';
    await vi.advanceTimersByTimeAsync(100);
    expect(fetch.mock.calls.some((c) => c[0] === '/api/graph/progress')).toBe(true);
    J.state.activeView = 'board'; // non-graph change is ignored
  });

  it('defers the boot fetches to DOMContentLoaded while loading', async () => {
    const spy = vi.spyOn(document, 'readyState', 'get').mockReturnValue('loading');
    load();
    routes['/api/stats'] = () => jsonResponse({});
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetch).not.toHaveBeenCalled();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    await vi.advanceTimersByTimeAsync(400);
    expect(fetch).toHaveBeenCalledWith('/api/stats');
    spy.mockRestore();
  });
});

describe('stats HUD', () => {
  async function stats(body) {
    routes['/api/stats'] = () => jsonResponse(body);
    await vi.advanceTimersByTimeAsync(300);
  }

  it('shows server totals before anything renders', async () => {
    load();
    await stats({
      domain_count: 2, memory_count: 30, entity_count: 4, discussion_count: 5,
      node_count: 12345, edge_count: 99, memory_count_raw: 31,
    });
    expect(txt('s-dom')).toBe('2');
    expect(txt('s-mem')).toBe('30');
    expect(txt('s-mem-raw')).toBe('31');
    expect(txt('sb-nodes')).toBe('0/12 345');
    expect(J.__storeMeta.node_count).toBe(12345);
  });

  it('counts the rendered graph and shows the store total as a tooltip', async () => {
    load();
    J.state.lastData = {
      nodes: [
        { id: '1', kind: 'domain' }, { id: '2', type: 'memory' }, { id: '3', type: 'discussion' },
        { id: '4', type: 'file' }, { id: '5' },
      ],
      links: [{}, {}],
    };
    await stats({ memory_count: 500, node_count: 1000, edge_count: 10 });
    expect(txt('s-dom')).toBe('1');
    expect(txt('s-mem')).toBe('1');
    expect(document.getElementById('s-mem').title).toBe('500 in store (1 shown)');
    expect(txt('s-ent')).toBe('2');
    expect(txt('s-nodes')).toBe('5');
    expect(txt('s-edge')).toBe('2');
    expect(txt('sb-nodes')).toBe('5/1 000');
  });

  it('prefers the renderer tally on graph and trace views', async () => {
    load();
    J.state.activeView = 'graph';
    J.__wfgRendered = { nodes: 10, edges: 5, domain: 1, memory: 2, discussion: 3 };
    await stats({ memory_count: 50, node_count: 100, edge_count: 20 });
    expect(txt('s-ent')).toBe('4');
    expect(document.getElementById('s-mem').title).toBe('50 in store (2 shown)');
    expect(txt('sb-nodes')).toBe('10/100');
    expect(txt('sb-edges')).toBe('5/20');
    J.state.activeView = 'trace';
    J.emit('state:lastData', {});
    expect(txt('sb-nodes')).toBe('10/10');
  });

  it('renders system vitals in full', async () => {
    load();
    await stats({
      system_vitals: {
        mean_heat: 0.12345,
        consolidation_pipeline: { labile: 1, early_ltp: 2, late_ltp: 3, consolidated: 4, reconsolidating: 5 },
        procedural_skills: 7, habitual_skills: 2,
        provenance: { perceived: 1, told: 1, inferred: 2, unknown: 0 },
        crystallized_confabulations: 3, habituated_repeats: 4, extinguished: 5,
        conflicting_claim_pairs: 6,
        familiarity_resolvable: { sampled: 10, share: 0.5, resolvable: 5 },
        sleep_phase_outputs: { nrem: 8, rem: 9 },
        targeted_reactivation: { cue: 'auth', cued_replayed: 3 },
        stress_modulation: { gain: 0.5, stress: 0.9, is_impairing: true },
        active_goal: { active: true, label: 'ship', triggers: 2 },
        forward_model: { sampled: 4, mean_error: 0.5 },
        attentional_salience: { sampled: 20, focus_share: 0.25 },
      },
      benchmarks: {
        LongMemEval: { recall_10: 91.4, mrr: 0.8123 },
        LoCoMo: { recall_10: 80 },
        BEAM: {},
      },
    });
    expect(document.getElementById('vitals-toggle').style.display).toBe('');
    expect(txt('sv-heat')).toBe('0.123');
    expect(txt('sv-recon')).toBe('5');
    expect(txt('sv-skills')).toBe('7 (2)');
    expect(txt('sv-inferred')).toBe('2 / 4');
    expect(txt('sv-familiarity')).toBe('50% (5/10)');
    expect(txt('sv-sleepphase')).toBe('8 / 9');
    expect(txt('sv-tmr')).toBe('auth (3)');
    expect(txt('sv-stress')).toBe('\u21930.50x (0.90)');
    expect(txt('sv-goal')).toBe('ship (2)');
    expect(txt('sv-prederr')).toBe('0.5000 (4)');
    expect(txt('sv-attn')).toBe('25% (20)');
    expect(document.getElementById('benchmark-summary').style.display).toBe('block');
    expect(txt('b-lme')).toBe('R@10 91% | MRR .812');
    expect(txt('b-loc')).toBe('R@10 80%');
    expect(txt('b-beam')).toBe('--');
  });

  it('renders vitals defaults when optional pieces are absent', async () => {
    load();
    await stats({ system_vitals: { inferred_memories: 4 } });
    expect(txt('sv-heat')).toBe('--');
    expect(txt('sv-skills')).toBe('0');
    expect(txt('sv-inferred')).toBe('4');
    expect(txt('sv-familiarity')).toBe('--');
    expect(txt('sv-tmr')).toBe('--');
    expect(txt('sv-stress')).toBe('1.00x (0.00)');
    expect(txt('sv-goal')).toBe('--');
    expect(txt('sv-prederr')).toBe('--');
    expect(txt('sv-attn')).toBe('--');
  });

  it('a stats failure only warns; db:false skips the poll', async () => {
    load();
    routes['/api/stats'] = () => jsonResponse({}, false);
    await vi.advanceTimersByTimeAsync(300);
    expect(console.warn).toHaveBeenCalledWith('[cortex] stats poll error:', 'HTTP 500');
    load({ capabilities: { db: false } });
    fetch.mockClear();
    await vi.advanceTimersByTimeAsync(31000);
    expect(fetch.mock.calls.some((c) => c[0] === '/api/stats')).toBe(false);
  });

  it('emits coverage:refresh after stats and reuses meta on lastData', async () => {
    load();
    const seen = vi.fn();
    J.on('coverage:refresh', seen);
    await stats({ domain_count: 3 });
    expect(seen).toHaveBeenCalled();
    J.state.lastData = { nodes: [{ id: 'd', kind: 'domain' }], edges: [] };
    expect(txt('s-dom')).toBe('1');
  });
});

describe('clock and offline sample', () => {
  it('ticks the status clock every second', async () => {
    load();
    vi.setSystemTime(new Date(2026, 0, 1, 3, 4, 5));
    await vi.advanceTimersByTimeAsync(1000);
    expect(txt('status-time')).toMatch(/^\d\d:\d\d:\d\d$/);
  });

  it('useFallback installs the sample graph', () => {
    load();
    J._pollTest.useFallback();
    expect(J.buildGraph).toHaveBeenCalled();
    expect(J.state.lastData.nodes.length).toBe(2);
    expect(txt('status-text')).toBe('Offline (sample)');
  });
});
