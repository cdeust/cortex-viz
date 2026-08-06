// Live Trace contract: an expanded transcript tails from its server cursor,
// and host activity is projected only into the expanded session it belongs
// to. The full SSE batches remain buffered for the Galaxy view.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

function makeBus() {
  const handlers = {};
  return {
    state: { activeView: 'trace', lastData: null },
    on(event, fn) {
      (handlers[event] || (handlers[event] = [])).push(fn);
    },
    emit(event, data) {
      (handlers[event] || []).forEach((fn) => fn(data));
    },
    appendGraphDelta: vi.fn(),
    setGraphData: vi.fn(),
  };
}

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.instances.push(this);
  }

  addEventListener(event, fn) {
    (this.listeners[event] || (this.listeners[event] = [])).push(fn);
  }

  send(event, payload) {
    (this.listeners[event] || []).forEach((fn) => fn({ data: JSON.stringify(payload) }));
  }

  close() {
    this.closed = true;
  }
}

function ok(payload) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function loadTrace() {
  loadScript('ui/unified/js/trace.js');
  // jsdom can still report "loading" when the IIFE is evaluated.
  document.dispatchEvent(new window.Event('DOMContentLoaded'));
}

function sessionFragment(sid, seq) {
  const root = `session:${sid}`;
  const action = `act:${sid}:${seq}`;
  const target = `tool:tool-${sid}`;
  return {
    nodes: [
      { id: root, kind: 'session', type: 'session' },
      { id: action, kind: 'action', type: 'action', tool: `tool-${sid}` },
      { id: target, kind: 'tool_hub', type: 'tool_hub' },
    ],
    edges: [
      { id: `${root}->${action}`, source: root, target: action, kind: 'did' },
      { id: `${action}->${target}`, source: action, target, kind: 'use' },
    ],
  };
}

function historicalChain(sid = 'alpha') {
  const root = `session:${sid}`;
  const e0 = `${sid}:e0`;
  const e1 = `${sid}:e1`;
  const e2 = `${sid}:e2`;
  const file = 'file:shared';
  return {
    // Deliberately not in sequence order: seq, not arrival order, is the
    // evidence for the causal reveal order.
    nodes: [
      { id: e2, kind: 'prompt', type: 'prompt', seq: 2, session_id: sid },
      { id: file, kind: 'file', type: 'file' },
      { id: e0, kind: 'prompt', type: 'prompt', seq: 0, session_id: sid },
      { id: e1, kind: 'action', type: 'action', seq: 1, session_id: sid, tool: 'Read' },
    ],
    edges: [
      { id: `${e1}->${e2}`, source: e1, target: e2, kind: 'next' },
      { id: `${root}->${e0}`, source: root, target: e0, kind: 'step' },
      { id: `${e1}->${file}`, source: e1, target: file, kind: 'read' },
      { id: `${e0}->${e1}`, source: e0, target: e1, kind: 'next' },
    ],
    meta: { event_count: 3 },
    next_since: 3,
  };
}

function manualAnimationFrames() {
  let nextId = 1;
  const queued = [];
  const cancelled = new Set();
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((fn) => {
    const id = nextId++;
    queued.push({ id, fn });
    return id;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
    cancelled.add(id);
  });
  return {
    pending() {
      return queued.filter((item) => !cancelled.has(item.id)).length;
    },
    runNext() {
      while (queued.length) {
        const item = queued.shift();
        if (cancelled.has(item.id)) continue;
        item.fn(window.performance.now());
        return true;
      }
      return false;
    },
    runAll(limit = 100) {
      let count = 0;
      while (this.runNext()) {
        count++;
        if (count > limit) throw new Error('animation frame queue did not drain');
      }
      return count;
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  document.body.innerHTML = '<div id="graph-container"></div><div id="status-text"></div>';
  globalThis.JUG = makeBus();
  window.JUG = globalThis.JUG;
  globalThis.EventSource = FakeEventSource;
  window.EventSource = FakeEventSource;
});

afterEach(() => {
  if (window.TraceView) window.TraceView.setLive(false);
  if (window.JUG && window.JUG.stopActivityStream) window.JUG.stopActivityStream();
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete globalThis.fetch;
  delete globalThis.EventSource;
  delete window.EventSource;
  delete window.TraceView;
});

describe('Trace live activity', () => {
  it('reveals historical children in causal order across animation frames', async () => {
    const frames = manualAnimationFrames();
    const history = historicalChain();
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') return ok(history);
      throw new Error(`unexpected fetch ${url}`);
    });

    loadTrace();
    await settle();
    JUG.appendGraphDelta.mockClear();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();

    expect(JUG.appendGraphDelta).not.toHaveBeenCalled();
    expect(frames.pending()).toBe(1);

    frames.runNext();
    expect(JUG.appendGraphDelta.mock.calls[0][0].map((n) => n.id)).toEqual(['alpha:e0']);
    expect(frames.pending()).toBe(1);

    frames.runNext();
    expect(JUG.appendGraphDelta.mock.calls[1][0].map((n) => n.id)).toEqual(
      ['alpha:e1', 'file:shared']
    );
    expect(frames.pending()).toBe(1);

    frames.runNext();
    expect(JUG.appendGraphDelta.mock.calls[2][0].map((n) => n.id)).toEqual(['alpha:e2']);
    expect(frames.pending()).toBe(0);
    expect(JUG.appendGraphDelta.mock.calls.flatMap((call) => call[1]).map((e) => e.id))
      .toEqual([
        'session:alpha->alpha:e0',
        'alpha:e1->file:shared',
        'alpha:e0->alpha:e1',
        'alpha:e1->alpha:e2',
      ]);
  });

  it('keeps matching SSE deltas immediate while historical frames are queued', async () => {
    const frames = manualAnimationFrames();
    const history = historicalChain();
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') return ok(history);
      if (url === '/api/prd') return ok({ nodes: [], edges: [] });
      throw new Error(`unexpected fetch ${url}`);
    });

    loadTrace();
    await settle();
    JUG.appendGraphDelta.mockClear();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();

    loadScript('ui/unified/js/activity_stream.js');
    const stream = JUG.startActivityStream();
    const live = sessionFragment('alpha', 99);
    stream.send('batch', live);

    expect(JUG.appendGraphDelta).toHaveBeenCalledTimes(1);
    expect(JUG.appendGraphDelta.mock.calls[0][0].map((n) => n.id)).toEqual(
      live.nodes.map((n) => n.id)
    );
    expect(frames.pending()).toBe(1);

    expect(frames.runAll()).toBe(3);
    expect(JUG.appendGraphDelta).toHaveBeenCalledTimes(4);
    expect(JUG.appendGraphDelta.mock.calls.slice(1).flatMap((call) => call[0])
      .filter((n) => typeof n.seq === 'number').map((n) => n.seq)).toEqual([0, 1, 2]);
  });

  it('cancels pending reveal frames on reset and can reveal cleanly again', async () => {
    const frames = manualAnimationFrames();
    const history = historicalChain();
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') return ok(history);
      throw new Error(`unexpected fetch ${url}`);
    });

    loadTrace();
    await settle();
    JUG.appendGraphDelta.mockClear();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();
    frames.runNext();
    expect(frames.pending()).toBe(1);

    window.TraceView.reload();
    await settle();
    expect(window.cancelAnimationFrame).toHaveBeenCalledTimes(1);
    expect(frames.pending()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    frames.runAll();
    expect(JUG.appendGraphDelta.mock.calls.flatMap((call) => call[0]).map((n) => n.id))
      .not.toContain('alpha:e1');

    JUG.appendGraphDelta.mockClear();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();
    expect(frames.runAll()).toBe(3);
    expect(JUG.appendGraphDelta.mock.calls.flatMap((call) => call[0])
      .filter((n) => typeof n.seq === 'number').map((n) => n.seq)).toEqual([0, 1, 2]);
  });

  it('starts fallback polling at the next_since cursor returned by expansion', async () => {
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') {
        return ok({ nodes: [], edges: [], meta: { event_count: 7 }, next_since: 7 });
      }
      if (url === '/api/trace/chain?session=alpha&since=7') {
        return ok({ nodes: [], edges: [], next_since: 7 });
      }
      throw new Error(`unexpected fetch ${url}`);
    });

    loadTrace();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();

    await vi.advanceTimersByTimeAsync(4000);
    await settle();

    expect(fetch).toHaveBeenCalledWith('/api/trace/chain?session=alpha&since=7');
  });

  it('projects matching SSE activity, rejects unrelated sessions, and preserves Galaxy buffering', async () => {
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') {
        return ok({ nodes: [], edges: [], meta: { event_count: 0 }, next_since: 0 });
      }
      if (url === '/api/prd') return ok({ nodes: [], edges: [] });
      throw new Error(`unexpected fetch ${url}`);
    });

    loadTrace();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();
    JUG.appendGraphDelta.mockClear();

    loadScript('ui/unified/js/activity_stream.js');
    const stream = JUG.startActivityStream();
    expect(stream.url).toBe('/api/activity/stream?since=0');

    const alpha = sessionFragment('alpha', 8);
    const beta = sessionFragment('beta', 3);
    stream.send('batch', alpha);
    stream.send('batch', beta);

    expect(JUG.state.activeView).toBe('trace');
    expect(JUG.appendGraphDelta).toHaveBeenCalledTimes(1);
    expect(JUG.appendGraphDelta.mock.calls[0][0].map((n) => n.id)).toEqual(
      alpha.nodes.map((n) => n.id)
    );
    expect(JUG.appendGraphDelta.mock.calls[0][1]).toEqual(alpha.edges);

    // The Trace projection does not consume the activity stream. Galaxy gets
    // both complete batches when it becomes active, including the one Trace
    // already visualized through its own view-local graph payload.
    JUG.state.activeView = 'graph';
    JUG.state.lastData = { nodes: [], edges: [], meta: { schema: 'workflow_graph.v1' } };
    JUG.emit('state:lastData', { value: JUG.state.lastData });

    expect(JUG.appendGraphDelta).toHaveBeenCalledTimes(3);
    expect(JUG.appendGraphDelta.mock.calls[1][0]).toEqual(alpha.nodes);
    expect(JUG.appendGraphDelta.mock.calls[2][0]).toEqual(beta.nodes);
  });

  it('filters a mixed replay batch to the directed component of the expanded session', async () => {
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') {
        return ok({ nodes: [], edges: [], meta: { event_count: 0 }, next_since: 0 });
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    loadTrace();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();
    JUG.appendGraphDelta.mockClear();

    const alpha = sessionFragment('alpha', 9);
    const beta = sessionFragment('beta', 4);
    const accepted = window.TraceView.acceptActivityBatch(
      alpha.nodes.concat(beta.nodes),
      alpha.edges.concat(beta.edges)
    );

    expect(accepted).toBe(true);
    expect(JUG.appendGraphDelta).toHaveBeenCalledTimes(1);
    expect(JUG.appendGraphDelta.mock.calls[0][0].map((n) => n.id)).toEqual(
      alpha.nodes.map((n) => n.id)
    );
    expect(JUG.appendGraphDelta.mock.calls[0][1]).toEqual(alpha.edges);
  });

  it('does not project SSE activity while Trace live mode is paused', async () => {
    globalThis.fetch = vi.fn((url) => {
      if (url === '/api/trace/domains') return ok({ nodes: [], edges: [] });
      if (url === '/api/trace/chain?session=alpha') {
        return ok({ nodes: [], edges: [], meta: { event_count: 0 }, next_since: 0 });
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    loadTrace();
    JUG.emit('graph:selectNode', {
      id: 'session:alpha', kind: 'session', session_id: 'alpha',
    });
    await settle();
    JUG.appendGraphDelta.mockClear();

    window.TraceView.setLive(false);
    const alpha = sessionFragment('alpha', 10);

    expect(window.TraceView.acceptActivityBatch(alpha.nodes, alpha.edges)).toBe(false);
    expect(JUG.appendGraphDelta).not.toHaveBeenCalled();
  });
});
