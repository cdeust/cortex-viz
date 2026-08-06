import { beforeAll, describe, expect, it } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let prepareTopology;

beforeAll(() => {
  window.JUG = { state: { activeView: 'trace', lastData: { meta: { schema: 'trace.v1' } } }, _wfg: {} };
  globalThis.JUG = window.JUG;
  loadScript('ui/unified/js/workflow_graph_const.js');
  loadScript('ui/unified/js/workflow_graph_slots.js');
  loadScript('ui/unified/js/workflow_graph_topology.js');
  prepareTopology = window.JUG._wfg.prepareTopology;
});

describe('Trace topology for streamed observed activity', () => {
  it('places an MCP call and target inside the expanded session disk', () => {
    const nodes = [
      { id: 'domain:repo', kind: 'domain' },
      { id: 'session:s1', kind: 'session', session_id: 's1' },
      { id: 'act:s1:7', kind: 'action', session_id: 's1', seq: 7 },
      { id: 'mcp:postgres', kind: 'mcp' },
    ];
    const edges = [
      { source: 'domain:repo', target: 'session:s1', kind: 'has_session' },
      { source: 'session:s1', target: 'act:s1:7', kind: 'did' },
      { source: 'act:s1:7', target: 'mcp:postgres', kind: 'call' },
    ];

    const topology = prepareTopology(nodes, edges, 1200, 800);

    expect(topology.domainOf['act:s1:7']).toBe('domain:repo');
    expect(topology.domainOf['mcp:postgres']).toBe('domain:repo');
    expect(topology.adj['session:s1']['act:s1:7']).toBe(true);
    expect(topology.adj['act:s1:7']['mcp:postgres']).toBe(true);
    expect(topology.slotOf['act:s1:7']).toBeTruthy();
    expect(topology.slotOf['mcp:postgres']).toBeTruthy();
    const session = topology.slotOf['session:s1'];
    const target = topology.slotOf['mcp:postgres'];
    const domain = topology.anchors['domain:repo'];
    expect(Math.hypot(target.x - session.x, target.y - session.y))
      .toBeLessThan(Math.hypot(target.x - domain.x, target.y - domain.y));
  });

  it('does not turn branch attachments into radial spine progression', () => {
    const nodes = [
      { id: 'domain:repo', kind: 'domain' },
      { id: 'session:s1', kind: 'session', session_id: 's1' },
      { id: 's1:p2', kind: 'prompt', session_id: 's1', seq: 2 },
      { id: 'file:shared', kind: 'file' },
      { id: 's1:a3', kind: 'action', session_id: 's1', seq: 3 },
      { id: 's1:p0', kind: 'prompt', session_id: 's1', seq: 0 },
      { id: 's1:a1', kind: 'action', session_id: 's1', seq: 1 },
    ];
    const edges = [
      { source: 's1:a1', target: 's1:p2', kind: 'next' },
      { source: 'domain:repo', target: 'session:s1', kind: 'has_session' },
      { source: 's1:p2', target: 's1:a3', kind: 'next' },
      { source: 's1:a1', target: 'file:shared', kind: 'read' },
      { source: 'session:s1', target: 's1:p0', kind: 'step' },
      { source: 's1:p0', target: 's1:a1', kind: 'next' },
    ];
    const topology = prepareTopology(nodes, edges, 1200, 800);
    const hub = topology.slotOf['session:s1'];
    const progress = (id) => {
      const slot = topology.slotOf[id];
      return Math.hypot(slot.x - hub.x, slot.y - hub.y);
    };
    // A read target is a branch from the action, not another temporal step.
    // The rejected planner forced it beyond every action that ever touched it,
    // exploding frequently reused files toward the edge of the viewport.
    expect(progress('file:shared')).toBeLessThan(progress('s1:a1'));

    const permuted = prepareTopology([...nodes].reverse(), [...edges].reverse(), 1200, 800);
    Object.keys(topology.slotOf).forEach((id) => {
      expect(permuted.slotOf[id]).toEqual(topology.slotOf[id]);
    });
  });

  it('propagates a domain through long shuffled chains and terminates on cycles', () => {
    const chain = Array.from({ length: 9 }, (_, seq) => ({
      id: `s1:e${seq}`, kind: 'action', seq,
    }));
    const nodes = [
      { id: 'domain:repo', kind: 'domain' },
      { id: 'session:s1', kind: 'session', session_id: 's1' },
      ...chain.reverse(),
    ];
    const edges = [
      { source: 'domain:repo', target: 'session:s1', kind: 'has_session' },
      { source: 'session:s1', target: 's1:e0', kind: 'step' },
      ...Array.from({ length: 8 }, (_, seq) => ({
        source: `s1:e${seq}`, target: `s1:e${seq + 1}`, kind: 'next',
      })).reverse(),
      { source: 's1:e8', target: 's1:e7', kind: 'next' },
    ];
    const topology = prepareTopology(nodes, edges, 1200, 800);

    chain.forEach((node) => {
      expect(topology.domainOf[node.id]).toBe('domain:repo');
      expect(topology.slotOf[node.id]).toBeTruthy();
    });
    expect(Object.keys(window.JUG._wfg.traceCausalContext(nodes, edges).rootOf))
      .toHaveLength(10);
  });

  it('places a cross-session shared target independently of edge order', () => {
    const nodes = [
      { id: 'domain:b', kind: 'domain' },
      { id: 'domain:a', kind: 'domain' },
      { id: 'session:b', kind: 'session', session_id: 'b' },
      { id: 'session:a', kind: 'session', session_id: 'a' },
      { id: 'act:b', kind: 'action', session_id: 'b', seq: 1 },
      { id: 'act:a', kind: 'action', session_id: 'a', seq: 1 },
      { id: 'file:shared', kind: 'file' },
    ];
    const structural = [
      { source: 'domain:a', target: 'session:a', kind: 'has_session' },
      { source: 'session:a', target: 'act:a', kind: 'did' },
      { source: 'domain:b', target: 'session:b', kind: 'has_session' },
      { source: 'session:b', target: 'act:b', kind: 'did' },
    ];
    const shared = [
      { source: 'act:a', target: 'file:shared', kind: 'read' },
      { source: 'act:b', target: 'file:shared', kind: 'edit' },
    ];
    const left = prepareTopology(nodes, [...structural, ...shared], 1200, 800);
    const right = prepareTopology(
      [...nodes].reverse(), [...structural].reverse().concat([...shared].reverse()), 1200, 800
    );

    expect(left.domainOf['file:shared']).toBe('domain:a');
    expect(right.domainOf['file:shared']).toBe('domain:a');
    expect(right.slotOf['file:shared']).toEqual(left.slotOf['file:shared']);
    const causal = window.JUG._wfg.traceCausalContext(nodes, [...structural, ...shared]);
    expect(causal.shared['file:shared']).toBe(true);
    expect(causal.predecessors['file:shared'].sort()).toEqual(['act:a', 'act:b']);
  });
});
