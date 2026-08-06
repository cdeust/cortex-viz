import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

function chainableForce() {
  const force = {};
  ['id', 'distance', 'strength', 'distanceMax', 'radius', 'iterations'].forEach((name) => {
    force[name] = () => force;
  });
  force.links = vi.fn(() => force);
  return force;
}

function d3Harness() {
  const simulations = [];
  const d3 = {
    forceSimulation(seed) {
      let alpha = 1;
      let alphaDecay = 0.022;
      let alphaMin = 0.001;
      let nodes = seed;
      const forces = {};
      const events = {};
      const sim = {
        restart: vi.fn(() => sim),
        stop: vi.fn(() => sim),
        alpha(value) {
          if (value === undefined) return alpha;
          alpha = value;
          return sim;
        },
        alphaDecay(value) {
          if (value === undefined) return alphaDecay;
          alphaDecay = value;
          return sim;
        },
        alphaMin(value) {
          if (value === undefined) return alphaMin;
          alphaMin = value;
          return sim;
        },
        velocityDecay: () => sim,
        nodes(value) {
          if (value === undefined) return nodes;
          nodes = value;
          return sim;
        },
        force(name, value) {
          if (value === undefined) return forces[name];
          forces[name] = value;
          return sim;
        },
        on(name, fn) {
          if (fn == null) delete events[name];
          else events[name] = fn;
          return sim;
        },
        emit(name) {
          Object.keys(events).filter((key) => key.split('.')[0] === name)
            .forEach((key) => events[key]());
        },
      };
      simulations.push(sim);
      return sim;
    },
    forceLink: () => chainableForce(),
    forceCollide: () => chainableForce(),
    forceManyBody: () => chainableForce(),
  };
  return { d3, simulations };
}

function mountTraceHarness(initialNodes, initialEdges) {
  const jug = makeJUG({ state: { activeView: 'trace' } });
  globalThis.JUG = jug;
  window.JUG = jug;
  const harness = d3Harness();
  window.d3 = harness.d3;
  globalThis.d3 = harness.d3;

  loadScript('ui/unified/js/workflow_graph_const.js');
  loadScript('ui/unified/js/workflow_graph_lod.js');
  loadScript('ui/unified/js/workflow_graph_slots.js');
  loadScript('ui/unified/js/workflow_graph_topology.js');
  loadScript('ui/unified/js/workflow_graph_trace_layout.js');

  const renders = [];
  const animationFrames = [];
  let animationTime = 0;
  window.requestAnimationFrame = vi.fn((callback) => {
    animationFrames.push(callback);
    return animationFrames.length;
  });
  function stepAnimation(onFrame) {
    if (!animationFrames.length) return false;
    animationTime += 1000 / 60;
    animationFrames.shift()(animationTime);
    if (onFrame) onFrame();
    return true;
  }
  function jumpAnimation(elapsed) {
    if (!animationFrames.length) return false;
    animationTime += elapsed;
    animationFrames.shift()(animationTime);
    return true;
  }
  function flushAnimation(onFrame) {
    while (stepAnimation(onFrame)) {}
  }
  const redrawNow = vi.fn();
  const redraw = vi.fn();
  Object.assign(window.JUG._wfg, {
    KIND_COLOR: {},
    setActiveRenderer() {},
    clearActiveRenderer() {},
    mountCanvas(container, ctx) {
      renders.push(ctx);
      return {
        destroy() {},
        resize() {},
        selectId() {},
        redraw,
        redrawNow,
      };
    },
  });
  loadScript('ui/unified/js/workflow_graph.js');

  const container = document.createElement('div');
  Object.defineProperty(container, 'clientWidth', { value: 900 });
  Object.defineProperty(container, 'clientHeight', { value: 700 });
  document.body.appendChild(container);
  const data = {
    meta: { schema: 'trace.v1' },
    nodes: initialNodes || [{ id: 'domain:dev', kind: 'domain', label: 'dev' }],
    edges: initialEdges || [],
  };
  jug.state.lastData = data;
  const handle = jug.renderWorkflowGraph(container, data);
  return { handle, ctx: renders[0], redraw, redrawNow,
    stepAnimation, jumpAnimation, flushAnimation,
    pendingAnimationFrames: () => animationFrames.length,
    sim: harness.simulations[0] };
}

function traceRadius(ctx, node) {
  const base = ctx.KIND_RADIUS[node.kind] == null ? 6 : ctx.KIND_RADIUS[node.kind];
  return base + Math.min(8, Math.sqrt(ctx.degree[node.id] || 0));
}

function expectNoOverlaps(ctx, nodes) {
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i];
      const b = nodes[j];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      expect(distance + Number.EPSILON).toBeGreaterThanOrEqual(traceRadius(ctx, a) + traceRadius(ctx, b));
    }
  }
}

function crossOverlapCount(ctx, left, right) {
  let count = 0;
  for (const a of left) {
    for (const b of right) {
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (distance + Number.EPSILON < traceRadius(ctx, a) + traceRadius(ctx, b)) count++;
    }
  }
  return count;
}

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
  delete globalThis.JUG;
  delete globalThis.d3;
  delete window.requestAnimationFrame;
});

describe('Trace topology-aware append', () => {
  it('keeps the rendered legend snapshot current for mixed Trace node kinds', () => {
    vi.useFakeTimers();
    const { handle } = mountTraceHarness();
    expect(window.JUG.__wfgRendered).toMatchObject({
      nodes: 1, edges: 0, domain: 1, memory: 0, discussion: 0,
    });
    const nodes = [
      { id: 'session:legend', kind: 'session', session_id: 'legend' },
      { id: 'memory:legend', kind: 'memory', session_id: 'legend' },
      { id: 'discussion:legend', kind: 'discussion', session_id: 'legend' },
    ];
    handle.append(nodes, [
      { source: 'domain:dev', target: 'session:legend', kind: 'has_session' },
      { source: 'session:legend', target: 'memory:legend', kind: 'remembers' },
      { source: 'session:legend', target: 'discussion:legend', kind: 'discusses' },
    ], { topologyAware: true });

    expect(window.JUG.__wfgRendered).toMatchObject({
      nodes: 4, edges: 3, domain: 1, memory: 1, discussion: 1,
    });
  });

  it('merges a richer duplicate into the live renderer without topology motion', () => {
    vi.useFakeTimers();
    const file = { id: 'file:deadbeef00', kind: 'file', label: 'x.py' };
    const { handle, ctx, pendingAnimationFrames } = mountTraceHarness([
      { id: 'domain:dev', kind: 'domain', label: 'dev' }, file,
    ], [{ source: 'domain:dev', target: file.id, kind: 'in_domain' }]);
    const updated = [];
    window.JUG.on('graph:nodeUpdated', (_event, node) => updated.push(node));

    const result = handle.append([
      { id: file.id, kind: 'file', path: '/repo/x.py' },
    ], [], { topologyAware: true });

    expect(result).toMatchObject({ addedNodes: 0, addedEdges: 0 });
    expect(ctx.byId[file.id].path).toBe('/repo/x.py');
    expect(updated).toEqual([ctx.byId[file.id]]);
    expect(pendingAnimationFrames()).toBe(0);
  });

  it('targets only new nodes and existing nodes whose canonical slot changed', () => {
    const { ctx } = mountTraceHarness();
    const nodes = [
      { id: 'domain:dev', kind: 'domain' },
      { id: 'session:stable', kind: 'session', session_id: 'stable' },
      { id: 'session:moved', kind: 'session', session_id: 'moved' },
      { id: 'stable:event', kind: 'action', session_id: 'stable' },
      { id: 'moved:event', kind: 'action', session_id: 'moved' },
      { id: 'moved:new', kind: 'action', session_id: 'moved' },
    ];
    const local = window.JUG._wfg.packTraceExpansion({
      ...ctx,
      nodes,
      byId: Object.fromEntries(nodes.map((node) => [node.id, node])),
      domainOf: Object.fromEntries(nodes.map((node) => [node.id, 'domain:dev'])),
      slotOf: {
        'session:stable': { x: 10, y: 10 },
        'session:moved': { x: 20, y: 20 },
        'stable:event': { x: 11, y: 11 },
        'moved:event': { x: 24, y: 24 },
        'moved:new': { x: 26, y: 26 },
      },
    }, [], ['moved:new'], [], {
      'session:stable': { x: 10, y: 10 },
      'session:moved': { x: 20, y: 20 },
      'stable:event': { x: 11, y: 11 },
      'moved:event': { x: 22, y: 22 },
    });

    expect(Object.keys(local.targets).sort()).toEqual(['moved:event', 'moved:new']);
    expect(local.affectedDomains).toEqual({ 'domain:dev': true });
  });

  function packWith(nodes, edges, addedIds, changedEdges) {
    const { ctx } = mountTraceHarness();
    const slotOf = Object.fromEntries(
      nodes.map((node, index) => [node.id, { x: index * 10, y: index * 10 }])
    );
    return window.JUG._wfg.packTraceExpansion({
      ...ctx,
      nodes,
      byId: Object.fromEntries(nodes.map((node) => [node.id, node])),
      domainOf: Object.fromEntries(nodes.map((node) => [node.id, 'domain:dev'])),
      slotOf,
    }, edges, addedIds, changedEdges, {});
  }

  it('prefers an observed cause that knows its position in the session', () => {
    // Two candidate parents for one effect: the action carries a `seq` (it is
    // a point on the session's spine), the file does not. The anchor must be
    // the ordered one, or a revealed effect emerges from whichever candidate
    // the edge list happened to mention first.
    const nodes = [
      { id: 'domain:dev', kind: 'domain' },
      { id: 'session:s', kind: 'session', session_id: 's' },
      { id: 'file:helper', kind: 'file', session_id: 's' },
      { id: 's:a4', kind: 'action', session_id: 's', seq: 4 },
      { id: 's:new', kind: 'action', session_id: 's', seq: 5 },
    ];
    const edges = [
      { source: 'file:helper', target: 's:new', kind: 'read' },
      { source: 's:a4', target: 's:new', kind: 'next' },
    ];

    expect(packWith(nodes, edges, ['s:new'], []).anchorOf['s:new']).toBe('s:a4');
  });

  it('breaks a tie between two unordered causes the same way in either order', () => {
    const nodes = [
      { id: 'domain:dev', kind: 'domain' },
      { id: 'session:s', kind: 'session', session_id: 's' },
      { id: 'file:b', kind: 'file', session_id: 's' },
      { id: 'file:a', kind: 'file', session_id: 's' },
      { id: 's:new', kind: 'action', session_id: 's' },
    ];
    const edges = [
      { source: 'file:b', target: 's:new', kind: 'read' },
      { source: 'file:a', target: 's:new', kind: 'read' },
    ];
    const forward = packWith(nodes, edges, ['s:new'], []);
    const reversed = packWith([...nodes].reverse(), [...edges].reverse(), ['s:new'], []);

    expect(forward.anchorOf['s:new']).toBe('file:a');
    expect(reversed.anchorOf['s:new']).toBe(forward.anchorOf['s:new']);
  });

  it('falls back to the session root when nothing caused the new node', () => {
    const nodes = [
      { id: 'domain:dev', kind: 'domain' },
      { id: 'session:s', kind: 'session', session_id: 's' },
      { id: 's:orphan', kind: 'action', session_id: 's' },
    ];

    expect(packWith(nodes, [], ['s:orphan'], []).anchorOf['s:orphan'])
      .toBe('session:s');
  });

  it('accepts an append that reports no changed edges at all', () => {
    // The Galaxy-side caller omits the argument entirely; a missing list must
    // not throw and must not lose the added node's target.
    const nodes = [
      { id: 'domain:dev', kind: 'domain' },
      { id: 'session:s', kind: 'session', session_id: 's' },
      { id: 's:new', kind: 'action', session_id: 's', seq: 1 },
    ];
    const local = packWith(nodes, [], ['s:new'], undefined);

    expect(Object.keys(local.targets)).toEqual(['s:new']);
    expect(local.affectedDomains).toEqual({ 'domain:dev': true });
  });

  it('is a no-op with nothing to move and no animation in flight', () => {
    const { ctx, sim, redraw, redrawNow, pendingAnimationFrames } = mountTraceHarness();
    const requestFrame = window.requestAnimationFrame.bind(window);

    const duration = window.JUG._wfg.animateTraceExpansion(
      ctx, { targets: {} }, sim, { redraw, redrawNow }, requestFrame
    );

    expect(duration).toBe(0);
    expect(pendingAnimationFrames()).toBe(0);
    expect(sim._traceAnimationState).toBeFalsy();
  });

  it('reveals each new effect beside its immediate causal parent', () => {
    vi.useFakeTimers();
    const { handle, ctx, flushAnimation, pendingAnimationFrames } = mountTraceHarness([
      { id: 'domain:dev', kind: 'domain' },
      { id: 'session:flow', kind: 'session', session_id: 'flow' },
    ], [{ source: 'domain:dev', target: 'session:flow', kind: 'has_session' }]);
    handle.append([
      { id: 'flow:p0', kind: 'prompt', session_id: 'flow', seq: 0 },
    ], [{ source: 'session:flow', target: 'flow:p0', kind: 'step' }], {
      topologyAware: true,
    });
    const hub = ctx.byId['session:flow'];
    const prompt = ctx.byId['flow:p0'];
    expect(Math.hypot(prompt.x - hub.x, prompt.y - hub.y)).toBeCloseTo(
      traceRadius(ctx, prompt) + traceRadius(ctx, hub), 12
    );
    flushAnimation();

    handle.append([
      { id: 'flow:a1', kind: 'action', session_id: 'flow', seq: 1 },
      { id: 'file:flow', kind: 'file' },
    ], [
      { source: 'flow:p0', target: 'flow:a1', kind: 'next' },
      { source: 'flow:a1', target: 'file:flow', kind: 'read' },
    ], { topologyAware: true });
    const action = ctx.byId['flow:a1'];
    const file = ctx.byId['file:flow'];
    expect(Math.hypot(action.x - prompt.x, action.y - prompt.y)).toBeCloseTo(
      traceRadius(ctx, action) + traceRadius(ctx, prompt), 12
    );
    expect(Math.hypot(file.x - action.x, file.y - action.y)).toBeCloseTo(
      traceRadius(ctx, file) + traceRadius(ctx, action), 12
    );
    expect(pendingAnimationFrames()).toBe(1);
    flushAnimation();
    expectNoOverlaps(ctx, ctx.nodes);
  });

  it('coalesces sustained retargeting into one bounded animation loop', () => {
    vi.useFakeTimers();
    const { ctx, sim, redraw, redrawNow, stepAnimation, flushAnimation,
      pendingAnimationFrames } = mountTraceHarness([
      { id: 'domain:dev', kind: 'domain', label: 'dev' },
      { id: 'session:stream', kind: 'session', session_id: 'stream', domain_id: 'domain:dev' },
    ], [{ source: 'domain:dev', target: 'session:stream', kind: 'has_session' }]);
    const node = ctx.byId['session:stream'];
    const animate = window.JUG._wfg.animateTraceExpansion;
    const renderer = { redraw, redrawNow };
    const requestFrame = window.requestAnimationFrame.bind(window);
    let latest = { x: node.x + 80, y: node.y + 40 };
    animate(ctx, { targets: { [node.id]: latest } }, sim, renderer, requestFrame);
    let firstCycleCompleted = false;
    let previousToken = sim._traceAnimationToken;

    for (let frame = 0; frame < 24; frame++) {
      latest = { x: latest.x + 1, y: latest.y - 1 };
      const wasIdle = !sim._traceAnimationState;
      animate(ctx, { targets: { [node.id]: latest } }, sim, renderer, requestFrame);
      if (wasIdle) expect(sim._traceAnimationToken).toBeGreaterThan(previousToken);
      else expect(sim._traceAnimationToken).toBe(previousToken);
      previousToken = sim._traceAnimationToken;
      expect(pendingAnimationFrames()).toBe(1);
      stepAnimation();
      if (!sim._traceAnimationState) firstCycleCompleted = true;
    }

    expect(firstCycleCompleted).toBe(true);
    flushAnimation();
    expect(pendingAnimationFrames()).toBe(0);
    expect(sim._traceAnimationState).toBeNull();
    expect({ x: node.x, y: node.y }).toEqual(latest);
  });

  it('gives a node arriving late its own transition without spawning another loop', () => {
    vi.useFakeTimers();
    const { ctx, sim, redraw, redrawNow, stepAnimation, flushAnimation,
      pendingAnimationFrames } = mountTraceHarness([
      { id: 'domain:dev', kind: 'domain', label: 'dev' },
      { id: 'session:a', kind: 'session', session_id: 'a', domain_id: 'domain:dev' },
      { id: 'session:b', kind: 'session', session_id: 'b', domain_id: 'domain:dev' },
    ], [
      { source: 'domain:dev', target: 'session:a', kind: 'has_session' },
      { source: 'domain:dev', target: 'session:b', kind: 'has_session' },
    ]);
    const animate = window.JUG._wfg.animateTraceExpansion;
    const requestFrame = window.requestAnimationFrame.bind(window);
    const renderer = { redraw, redrawNow };
    const a = ctx.byId['session:a'];
    const b = ctx.byId['session:b'];
    const targetA = { x: a.x + 60, y: a.y + 30 };
    const targetB = { x: b.x - 70, y: b.y - 20 };
    animate(ctx, { targets: { [a.id]: targetA } }, sim, renderer, requestFrame);
    for (let frame = 0; frame < 17; frame++) stepAnimation();

    animate(ctx, { targets: { [b.id]: targetB } }, sim, renderer, requestFrame);
    expect(pendingAnimationFrames()).toBe(1);
    for (let frame = 0; frame < 3; frame++) stepAnimation();

    expect({ x: a.x, y: a.y }).toEqual(targetA);
    expect({ x: b.x, y: b.y }).not.toEqual(targetB);
    expect(pendingAnimationFrames()).toBe(1);
    flushAnimation();
    expect({ x: b.x, y: b.y }).toEqual(targetB);
  });

  it('starts a late node on its first painted frame after requestAnimationFrame throttling', () => {
    vi.useFakeTimers();
    const { ctx, sim, redraw, redrawNow, stepAnimation, jumpAnimation,
      flushAnimation } = mountTraceHarness([
      { id: 'domain:dev', kind: 'domain', label: 'dev' },
      { id: 'session:a', kind: 'session', session_id: 'a', domain_id: 'domain:dev' },
      { id: 'session:b', kind: 'session', session_id: 'b', domain_id: 'domain:dev' },
    ], [
      { source: 'domain:dev', target: 'session:a', kind: 'has_session' },
      { source: 'domain:dev', target: 'session:b', kind: 'has_session' },
    ]);
    const animate = window.JUG._wfg.animateTraceExpansion;
    const requestFrame = window.requestAnimationFrame.bind(window);
    const renderer = { redraw, redrawNow };
    const a = ctx.byId['session:a'];
    const b = ctx.byId['session:b'];
    const targetA = { x: a.x + 50, y: a.y };
    const targetB = { x: b.x, y: b.y + 50 };
    animate(ctx, { targets: { [a.id]: targetA } }, sim, renderer, requestFrame);
    stepAnimation();
    animate(ctx, { targets: { [b.id]: targetB } }, sim, renderer, requestFrame);

    jumpAnimation(1000);

    expect({ x: a.x, y: a.y }).toEqual(targetA);
    expect({ x: b.x, y: b.y }).not.toEqual(targetB);
    flushAnimation();
    expect({ x: b.x, y: b.y }).toEqual(targetB);
  });

  it('animates 168 children into a collision-free local expansion', () => {
    vi.useFakeTimers();
    const { handle, ctx, redrawNow, flushAnimation, sim } = mountTraceHarness([
      { id: 'domain:dev', kind: 'domain', label: 'dev' },
      { id: 'domain:other', kind: 'domain', label: 'other' },
    ]);
    const domainBefore = { x: ctx.byId['domain:dev'].x, y: ctx.byId['domain:dev'].y };
    const otherBefore = { x: ctx.byId['domain:other'].x, y: ctx.byId['domain:other'].y };
    const children = Array.from({ length: 168 }, (_, index) => ({
      id: `session:s${index}`, kind: 'session', session_id: `s${index}`, domain_id: 'domain:dev',
    }));
    const childEdges = children.map((node) => ({
      id: `domain:dev->${node.id}`, source: 'domain:dev', target: node.id, kind: 'has_session',
    }));
    const result = handle.append(children, childEdges, { topologyAware: true });
    const animatedStart = { x: ctx.byId['session:s0'].x, y: ctx.byId['session:s0'].y };
    const target = ctx.slotOf['session:s0'];

    expect(result).toMatchObject({ addedNodes: 168, addedEdges: 168, totalNodes: 170 });
    expect(animatedStart).not.toEqual({ x: target.x, y: target.y });
    expect(sim.restart).not.toHaveBeenCalled();
    expect(sim.stop).toHaveBeenCalled();
    flushAnimation();

    const localNodes = [ctx.byId['domain:dev'], ...children.map((node) => ctx.byId[node.id])];
    expectNoOverlaps(ctx, localNodes);
    expect({ x: ctx.byId['domain:dev'].x, y: ctx.byId['domain:dev'].y }).toEqual(domainBefore);
    expect({ x: ctx.byId['domain:other'].x, y: ctx.byId['domain:other'].y }).toEqual(otherBefore);
    expect({ x: ctx.byId['session:s0'].x, y: ctx.byId['session:s0'].y }).toEqual(target);
    expect(redrawNow).toHaveBeenCalledTimes(2);
    expect(ctx.adj['domain:dev']['session:s0']).toBe(true);
  });

  it('eliminates the measured 206-new by 149-existing cross-overlap case', () => {
    vi.useFakeTimers();
    const domains = [
      { id: 'domain:dev', kind: 'domain', label: 'dev' },
      { id: 'domain:other', kind: 'domain', label: 'other' },
    ];
    const otherSession = {
      id: 'session:other', kind: 'session', session_id: 'other', domain_id: 'domain:other',
    };
    const otherEdge = { source: 'domain:other', target: otherSession.id, kind: 'has_session' };
    const { handle, ctx, flushAnimation } = mountTraceHarness(
      [...domains, otherSession], [otherEdge]
    );
    const otherDomainBefore = {
      x: ctx.byId['domain:other'].x, y: ctx.byId['domain:other'].y,
    };
    const otherSessionBefore = {
      x: ctx.byId[otherSession.id].x, y: ctx.byId[otherSession.id].y,
    };
    const sessions = Array.from({ length: 148 }, (_, index) => ({
      id: `session:s${index}`, kind: 'session', session_id: `s${index}`, domain_id: 'domain:dev',
    }));
    const sessionEdges = sessions.map((node) => ({
      source: 'domain:dev', target: node.id, kind: 'has_session',
    }));
    handle.append(sessions, sessionEdges, { topologyAware: true });
    flushAnimation();
    const existing = [ctx.byId['domain:dev'], ...sessions.map((node) => ctx.byId[node.id])];
    const siblingBefore = new Map(existing.slice(2).map((node) => [
      node.id, { x: node.x, y: node.y },
    ]));
    const actions = Array.from({ length: 206 }, (_, seq) => ({
      id: `s0:e${seq}`, kind: 'action', session_id: 's0', domain_id: 'session:s0', seq,
    }));
    const edges = actions.map((node, index) => ({
      source: index === 0 ? 'session:s0' : actions[index - 1].id,
      target: node.id,
      kind: index === 0 ? 'step' : 'next',
    }));
    handle.append(actions, edges, { topologyAware: true });

    expect(existing).toHaveLength(149);
    expect(ctx.byId['session:s0'].fx).toBeNull();
    expect({ x: ctx.byId['s0:e0'].x, y: ctx.byId['s0:e0'].y })
      .not.toEqual(ctx.slotOf['s0:e0']);
    flushAnimation();

    const appended = actions.map((node) => ctx.byId[node.id]);
    expect(crossOverlapCount(ctx, appended, existing)).toBe(0);
    expectNoOverlaps(ctx, ctx.nodes);
    const movedSiblings = existing.slice(2).filter((node) => {
      const before = siblingBefore.get(node.id);
      return node.x !== before.x || node.y !== before.y;
    });
    expect(movedSiblings).toHaveLength(147);
    [...existing.slice(1), ...appended].forEach((node) => {
      expect({ x: node.x, y: node.y }).toEqual(ctx.slotOf[node.id]);
    });
    expect({ x: ctx.byId['domain:other'].x, y: ctx.byId['domain:other'].y })
      .toEqual(otherDomainBefore);
    expect({ x: ctx.byId[otherSession.id].x, y: ctx.byId[otherSession.id].y })
      .not.toEqual(otherSessionBefore);
    expect(ctx.adj['session:s0']['s0:e0']).toBe(true);
    expect(ctx.adj['s0:e204']['s0:e205']).toBe(true);
    const incremental = new Map(ctx.nodes.filter((node) => node.kind !== 'domain').map((node) => [
      node.id, { x: node.x, y: node.y },
    ]));
    const remount = mountTraceHarness(
      [...domains, otherSession, ...sessions.slice().reverse(), ...actions.slice().reverse()],
      [otherEdge, ...sessionEdges, ...edges]
    );
    incremental.forEach((position, id) => {
      expect({ x: remount.ctx.byId[id].x, y: remount.ctx.byId[id].y }).toEqual(position);
    });
  });

  it('has no delayed movement after the interpolation first becomes stable', () => {
    vi.useFakeTimers();
    const { handle, ctx, flushAnimation, sim } = mountTraceHarness();
    const children = Array.from({ length: 8 }, (_, index) => ({
      id: `session:late-${index}`, kind: 'session', session_id: `late-${index}`,
      domain_id: 'domain:dev',
    }));
    handle.append(children, children.map((node) => ({
      source: 'domain:dev', target: node.id, kind: 'has_session',
    })), { topologyAware: true });
    const positions = [];
    flushAnimation(() => {
      const node = ctx.byId['session:late-0'];
      positions.push({ x: node.x, y: node.y });
    });
    const stable = positions.at(-1);
    expect(positions.some((position) => position.x !== stable.x || position.y !== stable.y)).toBe(true);
    expect(stable).toEqual(ctx.slotOf['session:late-0']);
    const firstStable = positions.findIndex((position, index) => index > 0
      && position.x === positions[index - 1].x && position.y === positions[index - 1].y);
    if (firstStable >= 0) {
      positions.slice(firstStable).forEach((position) => expect(position).toEqual(stable));
    }

    // A d3 end event and the seed-pinning timer used to reveal the late snap.
    sim.emit('end');
    vi.advanceTimersByTime(5000);
    expect({ x: ctx.byId['session:late-0'].x, y: ctx.byId['session:late-0'].y }).toEqual(stable);
  });

  it('cancels an in-flight interpolation when its graph handle is destroyed', () => {
    vi.useFakeTimers();
    const { handle, ctx, redraw, redrawNow, stepAnimation, flushAnimation } = mountTraceHarness();
    const child = {
      id: 'session:destroyed', kind: 'session', session_id: 'destroyed', domain_id: 'domain:dev',
    };
    handle.append([child], [{
      source: 'domain:dev', target: child.id, kind: 'has_session',
    }], { topologyAware: true });
    expect(stepAnimation()).toBe(true);
    const beforeDestroy = { x: ctx.byId[child.id].x, y: ctx.byId[child.id].y };
    const redrawsBeforeDestroy = redraw.mock.calls.length + redrawNow.mock.calls.length;

    handle.destroy();
    flushAnimation();

    expect({ x: ctx.byId[child.id].x, y: ctx.byId[child.id].y }).toEqual(beforeDestroy);
    expect(redraw.mock.calls.length + redrawNow.mock.calls.length).toBe(redrawsBeforeDestroy);
  });

  it('composes interleaved domain appends without stranding the first animation', () => {
    vi.useFakeTimers();
    const nodes = [
      { id: 'domain:a', kind: 'domain' },
      { id: 'domain:b', kind: 'domain' },
      { id: 'session:a', kind: 'session', session_id: 'a', domain_id: 'domain:a' },
      { id: 'session:b', kind: 'session', session_id: 'b', domain_id: 'domain:b' },
    ];
    const edges = [
      { source: 'domain:a', target: 'session:a', kind: 'has_session' },
      { source: 'domain:b', target: 'session:b', kind: 'has_session' },
    ];
    const { handle, ctx, stepAnimation, flushAnimation } = mountTraceHarness(nodes, edges);
    const actionA = { id: 'a:1', kind: 'action', session_id: 'a', seq: 1 };
    const actionB = { id: 'b:1', kind: 'action', session_id: 'b', seq: 1 };

    handle.append([actionA], [{ source: 'session:a', target: actionA.id, kind: 'did' }], {
      topologyAware: true,
    });
    expect(stepAnimation()).toBe(true);
    expect({ x: ctx.byId[actionA.id].x, y: ctx.byId[actionA.id].y })
      .not.toEqual(ctx.slotOf[actionA.id]);
    handle.append([actionB], [{ source: 'session:b', target: actionB.id, kind: 'did' }], {
      topologyAware: true,
    });
    flushAnimation();

    ctx.nodes.filter((node) => node.kind !== 'domain').forEach((node) => {
      expect({ x: node.x, y: node.y }).toEqual(ctx.slotOf[node.id]);
    });
    expectNoOverlaps(ctx, ctx.nodes);
  });

  it('composes back-to-back appends before the first animation frame', () => {
    vi.useFakeTimers();
    const nodes = [
      { id: 'domain:a', kind: 'domain' },
      { id: 'domain:b', kind: 'domain' },
      { id: 'session:a', kind: 'session', session_id: 'a', domain_id: 'domain:a' },
      { id: 'session:b', kind: 'session', session_id: 'b', domain_id: 'domain:b' },
    ];
    const edges = [
      { source: 'domain:a', target: 'session:a', kind: 'has_session' },
      { source: 'domain:b', target: 'session:b', kind: 'has_session' },
    ];
    const { handle, ctx, flushAnimation } = mountTraceHarness(nodes, edges);
    const actionA = { id: 'a:queued', kind: 'action', session_id: 'a' };
    const actionB = { id: 'b:queued', kind: 'action', session_id: 'b' };
    handle.append([actionA], [{ source: 'session:a', target: actionA.id, kind: 'did' }], {
      topologyAware: true,
    });
    handle.append([actionB], [{ source: 'session:b', target: actionB.id, kind: 'did' }], {
      topologyAware: true,
    });
    flushAnimation();

    [actionA.id, actionB.id].forEach((id) => {
      expect({ x: ctx.byId[id].x, y: ctx.byId[id].y }).toEqual(ctx.slotOf[id]);
    });
  });

  it('reflows an edge-only affected domain without touching another domain', () => {
    vi.useFakeTimers();
    const nodes = [
      { id: 'domain:dev', kind: 'domain' },
      { id: 'domain:other', kind: 'domain' },
      { id: 'session:a', kind: 'session', session_id: 'a', domain_id: 'domain:dev' },
      { id: 'session:b', kind: 'session', session_id: 'b', domain_id: 'domain:dev' },
      { id: 'session:other', kind: 'session', session_id: 'other', domain_id: 'domain:other' },
    ];
    const baseEdges = [
      { source: 'domain:dev', target: 'session:a', kind: 'has_session' },
      { source: 'domain:dev', target: 'session:b', kind: 'has_session' },
      { source: 'domain:other', target: 'session:other', kind: 'has_session' },
    ];
    const { handle, ctx, flushAnimation } = mountTraceHarness(nodes, baseEdges);
    const outside = ctx.byId['session:other'];
    outside.x += 37; outside.y -= 19; outside.fx = outside.x; outside.fy = outside.y;
    const outsideBefore = { x: outside.x, y: outside.y };

    handle.append([], [{ source: 'session:a', target: 'session:b', kind: 'next' }], {
      topologyAware: true,
    });
    flushAnimation();

    expect({ x: outside.x, y: outside.y }).toEqual(outsideBefore);
    ['session:a', 'session:b'].forEach((id) => {
      expect({ x: ctx.byId[id].x, y: ctx.byId[id].y }).toEqual(ctx.slotOf[id]);
    });
  });

  it('keeps the Galaxy append on the existing streaming force path', () => {
    vi.useFakeTimers();
    const { handle, sim } = mountTraceHarness();
    handle.append(
      [{ id: 'memory:new', kind: 'memory', domain_id: 'domain:dev' }],
      [{ source: 'domain:dev', target: 'memory:new', kind: 'in_domain' }]
    );
    expect(sim.restart).toHaveBeenCalledTimes(1);
  });
});

describe('canvas immediate redraw', () => {
  it('rebuilds hit-testing without applying a new zoom transform', () => {
    vi.useFakeTimers();
    const zoomTransforms = vi.fn();
    const behavior = () => {
      const fn = () => {};
      fn.scaleExtent = () => fn;
      fn.subject = () => fn;
      fn.on = () => fn;
      fn.transform = zoomTransforms;
      return fn;
    };
    const selection = {
      call(fn, ...args) { if (typeof fn === 'function') fn(selection, ...args); return selection; },
      on() { return selection; },
    };
    const jug = makeJUG();
    globalThis.JUG = jug;
    window.JUG = jug;
    window.d3 = {
      select: () => selection,
      zoom: behavior,
      drag: behavior,
      zoomIdentity: {
        k: 1, x: 0, y: 0,
        invert: (point) => point,
        translate() { return this; },
        scale() { return this; },
      },
    };
    globalThis.d3 = window.d3;
    const builds = vi.fn();
    class SpatialHash {
      build(nodes) { builds(nodes.length); }
      queryNeighborhood() { return []; }
    }
    jug._wfg = {
      SpatialHash,
      nodeRadius: () => 5,
      nodeColor: () => '#000',
      labelOf: (node) => node.id,
    };
    window.HTMLCanvasElement.prototype.getContext = () => ({
      save() {}, restore() {}, clearRect() {}, translate() {}, scale() {},
      beginPath() {}, moveTo() {}, lineTo() {}, arc() {}, fill() {}, stroke() {},
      fillText() {}, setLineDash() {},
    });
    loadScript('ui/unified/js/workflow_graph_render_canvas.js');
    const nodes = [{ id: 'session:s1', kind: 'session', x: 20, y: 20 }];
    const ctx = {
      nodes, edges: [], adj: {}, byId: { 'session:s1': nodes[0] },
      anchors: {}, domains: [], shells: [], sideShells: [], cx: 50, cy: 50, baseR: 20,
    };
    const sim = {
      on: () => sim,
      alphaTarget: () => sim,
      restart: () => sim,
    };
    const container = document.createElement('div');
    document.body.appendChild(container);
    const handle = jug._wfg.mountCanvas(container, ctx, sim, 100, 100);
    nodes.push({ id: 's1:e0', kind: 'action', x: 30, y: 30 });
    handle.redrawNow();

    expect(builds).toHaveBeenLastCalledWith(2);
    expect(zoomTransforms).not.toHaveBeenCalled();
  });
});

describe('workflow graph bridge append mode', () => {
  it('requests topology refresh for Trace deltas and not Galaxy deltas', () => {
    vi.useFakeTimers();
    const handlers = {};
    const append = vi.fn();
    const jug = makeJUG({
      state: {},
      on(name, fn) { (handlers[name] || (handlers[name] = [])).push(fn); },
      renderWorkflowGraph: vi.fn(() => ({ destroy() {}, append })),
    });
    globalThis.JUG = jug;
    window.JUG = jug;
    const host = document.createElement('div');
    host.id = 'graph-container';
    document.body.appendChild(host);
    loadScript('ui/unified/js/workflow_graph_bridge.js');
    document.dispatchEvent(new window.Event('DOMContentLoaded'));

    const emitState = (value, delta) => {
      (handlers['state:lastData'] || []).forEach((fn) => fn({ value, delta }));
    };
    const trace = { meta: { schema: 'trace.v1' }, nodes: [{ id: 'd', kind: 'domain' }], edges: [] };
    emitState(trace);
    vi.advanceTimersByTime(400);
    emitState(trace, { nodes: [{ id: 's', kind: 'session' }], edges: [] });
    expect(append.mock.calls.at(-1)[2]).toEqual({ topologyAware: true });

    const galaxy = { meta: { schema: 'workflow_graph.v1' }, nodes: [{ id: 'd', kind: 'domain' }], edges: [] };
    emitState(galaxy);
    vi.advanceTimersByTime(500);
    emitState(galaxy, { nodes: [{ id: 'm', kind: 'memory' }], edges: [] });
    expect(append.mock.calls.at(-1)[2]).toEqual({ topologyAware: false });
  });
});
