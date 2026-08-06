// Offline degradation contract for ui/unified/js/polling.js.
//
// `useFallback` — the named "Offline (sample)" mode — was defined and never
// called (CodeQL js/unused-local-variable #163): an unreachable server left
// the loading overlay up forever, since hideLoading() only ran on the
// success path. These tests pin the degradation and, just as importantly,
// the cases where it must NOT fire.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

let poll;

// Fail the progress poll `n` more times, then keep failing.
function installFailingFetch() {
  const calls = { n: 0 };
  globalThis.fetch = vi.fn(() => {
    calls.n++;
    return Promise.reject(new Error('ECONNREFUSED'));
  });
  return calls;
}

// The poll's catch runs on the microtask queue; give it a turn.
async function pollOnce() {
  poll.fetchGraph();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function statusText() {
  return document.getElementById('status-text').textContent;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  document.body.innerHTML =
    '<div id="status-text"></div><div id="loading"></div><div id="s-mem"></div>';
  globalThis.JUG = makeJUG({
    state: { activeView: 'graph', lastData: null },
    buildGraph: vi.fn(function (d) { globalThis.JUG.state.lastData = d; }),
    addBatchToGraph() {},
    API_URL: '/api/graph',
  });
  window.JUG = globalThis.JUG;
  loadScript('ui/unified/js/polling.js');
  poll = window.JUG._pollTest;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete globalThis.fetch;
});

describe('progress poll — offline degradation', () => {
  it('renders the current mixed-kind Trace snapshot into the legend and status strip', () => {
    document.body.insertAdjacentHTML('beforeend', [
      's-dom', 's-mem', 's-ent', 's-disc', 's-nodes', 's-edge', 'sb-nodes', 'sb-edges',
    ].map((id) => `<div id="${id}"></div>`).join(''));
    window.JUG.state.activeView = 'trace';
    window.JUG.__wfgRendered = {
      nodes: 4, edges: 3, domain: 1, memory: 1, discussion: 1,
    };

    window.JUG.emit('state:lastData', {});

    expect(document.getElementById('s-dom').textContent).toBe('1');
    expect(document.getElementById('s-mem').textContent).toBe('1');
    expect(document.getElementById('s-ent').textContent).toBe('1');
    expect(document.getElementById('s-disc').textContent).toBe('1');
    expect(document.getElementById('s-nodes').textContent).toBe('4');
    expect(document.getElementById('s-edge').textContent).toBe('3');
    expect(document.getElementById('sb-nodes').textContent).toBe('4/4');
    expect(document.getElementById('sb-edges').textContent).toBe('3/3');
  });

  it('does not degrade before the failure threshold', async () => {
    installFailingFetch();
    for (let i = 0; i < poll.offlineAfter - 1; i++) await pollOnce();
    expect(poll.failures()).toBe(poll.offlineAfter - 1);
    expect(poll.offlineApplied()).toBe(false);
    expect(window.JUG.buildGraph).not.toHaveBeenCalled();
  });

  it('enters the named offline mode on the Nth straight failure', async () => {
    installFailingFetch();
    for (let i = 0; i < poll.offlineAfter; i++) await pollOnce();
    expect(poll.offlineApplied()).toBe(true);
    expect(statusText()).toBe('Offline (sample)');
  });

  it('clears the loading overlay it used to leave up forever', async () => {
    installFailingFetch();
    expect(document.getElementById('loading').classList.contains('done')).toBe(false);
    for (let i = 0; i < poll.offlineAfter; i++) await pollOnce();
    expect(document.getElementById('loading').classList.contains('done')).toBe(true);
  });

  it('applies the fallback once, not on every later failure', async () => {
    installFailingFetch();
    for (let i = 0; i < poll.offlineAfter + 4; i++) await pollOnce();
    expect(window.JUG.buildGraph).toHaveBeenCalledTimes(1);
  });

  it('treats a rendered-but-empty node list as an empty canvas', async () => {
    installFailingFetch();
    window.JUG.state.lastData = { nodes: [], edges: [] };
    for (let i = 0; i < poll.offlineAfter; i++) await pollOnce();
    expect(poll.offlineApplied()).toBe(true);
  });

  it('never replaces a canvas that already rendered real nodes', async () => {
    installFailingFetch();
    window.JUG.state.lastData = { nodes: [{ id: 'real' }], edges: [] };
    for (let i = 0; i < poll.offlineAfter + 2; i++) await pollOnce();
    expect(poll.offlineApplied()).toBe(false);
    expect(window.JUG.buildGraph).not.toHaveBeenCalled();
    expect(window.JUG.state.lastData.nodes[0].id).toBe('real');
  });

  it('resets the failure count when a poll succeeds, so blips never accumulate', async () => {
    installFailingFetch();
    await pollOnce();
    await pollOnce();
    expect(poll.failures()).toBe(2);

    globalThis.fetch = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ full_ready: true, node_count: 5, edge_count: 2 }),
      }));
    await pollOnce();
    expect(poll.failures()).toBe(0);

    installFailingFetch();
    await pollOnce();
    expect(poll.offlineApplied()).toBe(false);
  });

  it('keeps retrying after degrading — the mode is not a dead end', async () => {
    installFailingFetch();
    for (let i = 0; i < poll.offlineAfter; i++) await pollOnce();
    expect(vi.getTimerCount()).toBeGreaterThan(0);
  });

  it('warns on every failure so the cause is debuggable', async () => {
    installFailingFetch();
    await pollOnce();
    expect(console.warn).toHaveBeenCalledWith(
      '[cortex] progress poll error:', 'ECONNREFUSED');
  });
});
