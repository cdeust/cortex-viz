// Behaviour tests for workflow_graph_tokens.js (colour/radius/label
// resolution, surface-change repaint) and workflow_graph_topology.js
// (domain anchors, domain inheritance, hubs, degree, force helpers).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

function fresh(jugExtra) {
  window.JUG = Object.assign({ state: { activeView: 'graph' } }, jugExtra);
  globalThis.JUG = window.JUG;
  loadScript('ui/unified/js/workflow_graph_const.js');
}

afterEach(() => {
  delete window.CortexPalette;
  delete window.CortexSurface;
  document.documentElement.removeAttribute('data-surface');
});

describe('tokens: radius and label', () => {
  beforeEach(() => {
    fresh();
    loadScript('ui/unified/js/workflow_graph_tokens.js');
  });

  it('nodeRadius uses kind base plus clamped size/weight bump', () => {
    const W = window.JUG._wfg;
    const base = W.nodeRadius({ kind: 'domain' });
    expect(W.nodeRadius({ kind: 'nonexistent' })).toBe(6);
    expect(W.nodeRadius({ kind: 'domain', size: base + 100 })).toBe(base + 6);
    expect(W.nodeRadius({ kind: 'domain', size: base - 100 })).toBe(base - 2);
    expect(W.nodeRadius({ kind: 'domain', size: base + 1 })).toBe(base + 1);
    expect(W.nodeRadius({ kind: 'domain', weight: 1 })).toBe(base + 2);
    expect(W.nodeRadius({ kind: 'domain', weight: 10 })).toBe(base + 4);
  });

  it('labelOf picks the first available descriptor', () => {
    const L = window.JUG._wfg.labelOf;
    expect(L({ label: 'a', name: 'b' })).toBe('a');
    expect(L({ name: 'b' })).toBe('b');
    expect(L({ title: 't' })).toBe('t');
    expect(L({ path: 'p' })).toBe('p');
    expect(L({ id: 'i' })).toBe('i');
    expect(L({})).toBe('');
  });
});

describe('tokens: colours via CortexPalette', () => {
  beforeEach(() => {
    fresh();
    window.CortexSurface = { EVENT: 'cortex:surface-change' };
    window.CortexPalette = { hex: vi.fn((t) => '#tok:' + t) };
    loadScript('ui/unified/js/workflow_graph_tokens.js');
  });

  it('server colour wins except for tool hubs; fallbacks are live tokens', () => {
    const W = window.JUG._wfg;
    expect(W.nodeColor({ kind: 'file', color: '#abc' })).toBe('#abc');
    expect(W.nodeColor({ kind: 'file' })).toBe('#tok:--tool-read');
    expect(W.nodeColor({ kind: 'unmapped' })).toBe('#tok:--field-point');
    expect(W.nodeColor({ kind: 'tool_hub', id: 'tool_hub:d:Bash', color: '#abc' })).toBe('#tok:--tool-bash');
    // hub with an empty id tail falls back to colour / kind token
    expect(W.nodeColor({ kind: 'tool_hub', id: '', color: '#def' })).toBe('#def');
    expect(W.nodeColor({ kind: 'tool_hub', id: 'x:', color: '#def' })).toBe('#def');
  });

  it('KIND_COLOR re-resolves on every read and falls back when empty', () => {
    const K = window.JUG._wfg.KIND_COLOR;
    expect(K.domain).toBe('#tok:--warn-ink');
    window.CortexPalette.hex = vi.fn((t) => (t === '--field-point' ? '#fp' : ''));
    expect(K.skill).toBe('#fp');
    expect(Object.keys(K)).toContain('symbol');
  });

  it('surface change repaints the active renderer and tolerates redraw errors', () => {
    const W = window.JUG._wfg;
    const r = { redraw: vi.fn() };
    W.setActiveRenderer(r);
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(r.redraw).toHaveBeenCalledTimes(1);
    r.redraw = vi.fn(() => {
      throw new Error('boom');
    });
    expect(() => window.dispatchEvent(new CustomEvent('cortex:surface-change'))).not.toThrow();
    // clearing a different renderer is a no-op; clearing the active one detaches it
    W.clearActiveRenderer({});
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(r.redraw).toHaveBeenCalledTimes(2);
    W.clearActiveRenderer(r);
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(r.redraw).toHaveBeenCalledTimes(2);
    W.setActiveRenderer({});
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
  });
});

describe('tokens: defensive CSS reader without CortexPalette', () => {
  it('reads the custom property, caches per surface, and invalidates on change', () => {
    fresh();
    window.CortexSurface = { EVENT: 'cortex:surface-change' };
    loadScript('ui/unified/js/workflow_graph_tokens.js');
    const W = window.JUG._wfg;
    document.documentElement.style.setProperty('--tool-read', ' #112233 ');
    expect(W.nodeColor({ kind: 'file' })).toBe('#112233');
    document.documentElement.style.setProperty('--tool-read', '#445566');
    expect(W.nodeColor({ kind: 'file' })).toBe('#112233'); // cached
    document.documentElement.setAttribute('data-surface', 'ink');
    expect(W.nodeColor({ kind: 'file' })).toBe('#445566'); // new surface key
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    document.documentElement.style.setProperty('--tool-read', '#778899');
    expect(W.nodeColor({ kind: 'file' })).toBe('#778899');
    // nothing resolves -> null fallback
    document.documentElement.style.removeProperty('--field-point');
    expect(W.nodeColor({ kind: 'unmapped' })).toBeNull();
  });
});

describe('topology', () => {
  let W;
  beforeEach(() => {
    fresh();
    loadScript('ui/unified/js/workflow_graph_slots.js');
    loadScript('ui/unified/js/workflow_graph_topology.js');
    W = window.JUG._wfg;
  });

  function galaxy() {
    const nodes = [
      { id: 'domain:a', kind: 'domain' },
      { id: 'domain:b', kind: 'domain' },
      { id: 'hub:read', kind: 'tool_hub' },
      { id: 'hub:bash', kind: 'tool_hub' },
      { id: 'f1', kind: 'file', path: '/x/f1.py', domain: 'domain:a' },
      { id: 'f2', kind: 'file', path: '/x/f2.py', domain_id: 'domain:b' },
      { id: 's1', kind: 'symbol', path: '/x/f1.py' },
      { id: 's2', kind: 'symbol', domain: 'a' },
      { id: 's3', kind: 'symbol' },
      { id: 's4', kind: 'symbol' },
      { id: 'm1', kind: 'memory' },
    ];
    const edges = [
      { source: 'domain:a', target: 'hub:read', kind: 'in_domain' },
      { source: 'f2', target: 'domain:b', kind: 'in_domain' },
      { source: 'hub:read', target: 'f1', kind: 'tool_used_file', weight: 0.5 },
      { source: 'f1', target: 'hub:read', kind: 'tool_used_file', weight: 2 },
      { source: 'hub:bash', target: 'f1', kind: 'tool_used_file' },
      { source: 'hub:bash', target: 'ghost', kind: 'tool_used_file' },
      { source: 's2', target: 'f1', kind: 'defined_in' },
      { source: 'f2', target: 's4', kind: 'defined_in' },
      { source: 's3', target: 'ghost', kind: 'defined_in' },
      { source: 's3', target: 's1', kind: 'calls' },
      { source: 'f1', target: 'f2', kind: 'imports' },
      { source: 'f1', target: 'm1', kind: 'other' },
    ];
    return { nodes, edges };
  }

  it('lays out galaxy anchors, domain membership, hubs, degrees and shells', () => {
    const { nodes, edges } = galaxy();
    const t = W.prepareTopology(nodes, edges, 1000, 800);
    expect(t.isTrace).toBe(false);
    expect(t.shells.length).toBeGreaterThan(0);
    expect(t.sideShells.map((s) => s.key)).toEqual(['L4', 'L5']);
    expect(Object.keys(t.anchors).sort()).toEqual(['domain:a', 'domain:b']);
    expect(nodes[0].fx).toBe(nodes[0].x);
    expect(t.domainOf['domain:a']).toBe('domain:a');
    expect(t.domainOf['hub:read']).toBe('domain:a');
    expect(t.domainOf.f1).toBe('domain:a');
    expect(t.domainOf.f2).toBe('domain:b');
    // symbol inherits parent file domain (via path and via edge)
    expect(t.parentFile.s1).toBe('f1');
    expect(t.parentFile.s2).toBe('f1');
    expect(t.parentFile.s4).toBe('f2');
    expect(t.domainOf.s1).toBe('domain:a');
    expect(t.domainOf.s4).toBe('domain:b');
    expect(t.domainOf.s3).toBe('domain:__global__');
    // heaviest same-domain hub wins; cross-domain hub ignored
    expect(t.primaryHub.f1).toBe('hub:read');
    expect(t.degree.f1).toBeGreaterThan(3);
    expect(t.adj.f1.f2).toBe(true);
    const cross = edges.find((e) => e.kind === 'imports');
    expect(cross._crossDomain).toBe(true);
    expect(edges[0]._crossDomain).toBe(false);
    expect(t.slotOf.f1).toBeTruthy();
    expect(t.cx).toBe(500);
  });

  it('detects trace graphs by view, schema or node kinds and sorts domains', () => {
    const nodes = [{ id: 'domain:z', kind: 'domain' }, { id: 'domain:a', kind: 'domain' }];
    window.JUG.state.activeView = 'graph';
    expect(W.prepareTopology(nodes, [], 800, 600).isTrace).toBe(false);
    window.JUG.state.activeView = 'trace';
    const t = W.prepareTopology(nodes, [], 800, 600);
    expect(t.isTrace).toBe(true);
    expect(t.shells).toEqual([]);
    expect(t.sideShells).toEqual([]);
    expect(t.domains.map((d) => d.id)).toEqual(['domain:a', 'domain:z']);
    window.JUG.state.activeView = 'graph';
    window.JUG.state.lastData = { meta: { schema: 'trace.v1' } };
    expect(W.prepareTopology(nodes, [], 800, 600).isTrace).toBe(true);
    window.JUG.state.lastData = null;
    expect(W.prepareTopology([...nodes, { id: 'p', kind: 'prompt' }], [], 800, 600).isTrace).toBe(true);
    expect(W.prepareTopology([{ id: 'p', type: 'action' }], [], 800, 600).isTrace).toBe(true);
  });

  it('propagates domain down trace edges, picking the smallest membership', () => {
    const nodes = [
      { id: 'domain:b', kind: 'domain' },
      { id: 'domain:a', kind: 'domain' },
      { id: 'session:1', kind: 'session' },
      { id: 'act:1', kind: 'action' },
      { id: 'file:1', kind: 'file' },
    ];
    const edges = [
      { source: 'domain:b', target: 'session:1', kind: 'has_session' },
      { source: 'domain:a', target: { id: 'session:1' }, kind: 'has_session' },
      { source: { id: 'session:1' }, target: 'act:1', kind: 'did' },
      { source: 'act:1', target: 'file:1', kind: 'edit' },
      { source: 'orphan', target: 'file:1', kind: 'edit' },
    ];
    const t = W.prepareTopology(nodes, edges, 800, 600);
    expect(t.domainOf['act:1']).toBe('domain:a');
    expect(t.domainOf['file:1']).toBe('domain:a');
    expect(t.domainOf.orphan).toBeUndefined();
  });

  it('handles no domains at all', () => {
    const t = W.prepareTopology([{ id: 'x', kind: 'file' }], [], 400, 300);
    expect(t.domains).toEqual([]);
    expect(t.baseR).toBeGreaterThan(0);
  });

  describe('force helpers', () => {
    const F = () => W.forces;

    it('link distance and strength honour cross-domain and weights', () => {
      expect(F().linkDistance({ _crossDomain: true })).toBe(window.JUG._wfgConst.CROSS_DOMAIN_DISTANCE);
      expect(F().linkDistance({ kind: 'zzz' })).toBe(30);
      expect(F().linkStrength({ _crossDomain: true })).toBe(window.JUG._wfgConst.CROSS_DOMAIN_STRENGTH);
      const noW = F().linkStrength({ kind: 'zzz' });
      expect(noW).toBe(0.4);
      expect(F().linkStrength({ kind: 'zzz', weight: 0 })).toBeCloseTo(0.4 * 0.3);
      expect(F().linkStrength({ kind: 'zzz', weight: 5 })).toBeCloseTo(0.4);
    });

    it('charge strength per kind', () => {
      expect(F().chargeStrength({ kind: 'domain' })).toBe(-620);
      expect(F().chargeStrength({ kind: 'tool_hub' })).toBe(-140);
      expect(F().chargeStrength({ kind: 'agent' })).toBe(-80);
      expect(F().chargeStrength({ kind: 'skill' })).toBe(-80);
      expect(F().chargeStrength({ kind: 'symbol' })).toBe(-22);
      expect(F().chargeStrength({ kind: 'file' })).toBe(-28);
    });

    it('collisionRadius grows with sqrt(degree), capped', () => {
      const base = F().collisionRadius({ kind: 'file', id: 'a' }, { degree: {} });
      expect(F().collisionRadius({ kind: 'file', id: 'a' }, { degree: { a: 9 } })).toBe(base + 3);
      expect(F().collisionRadius({ kind: 'file', id: 'a' }, { degree: { a: 10000 } })).toBe(base + 8);
      expect(F().collisionRadius({ kind: 'weird', id: 'a' }, { degree: {} })).toBe(6);
    });

    it('slotForce pulls non-domain nodes toward their slot', () => {
      const nodes = [
        { id: 'd', kind: 'domain', x: 0, y: 0, vx: 0, vy: 0 },
        { id: 'a', kind: 'file', x: 0, y: 0, vx: 0, vy: 0 },
        { id: 'b', kind: 'file', x: 5, y: 5, vx: 0, vy: 0 },
      ];
      const ctx = { nodes, slotOf: { d: { x: 9, y: 9 }, a: { x: 10, y: 20 } } };
      F().slotForce(ctx, 0.5)(1);
      expect(nodes[0].vx).toBe(0);
      expect(nodes[1].vx).toBe(5);
      expect(nodes[1].vy).toBe(10);
      expect(nodes[2].vx).toBe(0);
    });

    it('symbolMultiCenterForce attracts symbols to every domain they touch', () => {
      const nodes = [
        { id: 'sym', kind: 'symbol', x: 0, y: 0, vx: 0, vy: 0 },
        { id: 'lone', kind: 'symbol', x: 0, y: 0, vx: 0, vy: 0 },
        { id: 'f', kind: 'file', x: 0, y: 0, vx: 0, vy: 0 },
        { id: 'g', kind: 'file', x: 0, y: 0, vx: 0, vy: 0 },
      ];
      const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
      const ctx = {
        nodes,
        byId,
        domainOf: { sym: 'A', f: 'B', g: 'A', lone: 'Z' },
        anchors: { A: { x: 100, y: 0 }, B: { x: 0, y: 100 } },
        edges: [
          { source: 'sym', target: 'f', kind: 'calls' },
          { source: { id: 'g' }, target: { id: 'sym' }, kind: 'member_of' },
          { source: 'sym', target: 'missing', kind: 'calls' },
          { source: 'sym', target: 'f', kind: 'other' },
        ],
      };
      const force = F().symbolMultiCenterForce(ctx);
      expect(Object.keys(ctx._symDomains.sym).sort()).toEqual(['A', 'B']);
      expect(Object.keys(ctx._symDomains.lone)).toEqual([]);
      force(1);
      expect(nodes[0].vx).toBeCloseTo((0.06 / 2) * 100);
      expect(nodes[0].vy).toBeCloseTo((0.06 / 2) * 100);
      expect(nodes[1].vx).toBe(0);
      expect(nodes[2].vx).toBe(0);
    });

    it('interDomainRepelForce pushes domains apart symmetrically', () => {
      const a = { x: 0, y: 0, vx: 0, vy: 0 };
      const b = { x: 100, y: 0, vx: 0, vy: 0 };
      F().interDomainRepelForce({ domains: [a, b] }, 1)(1);
      expect(a.vx).toBeLessThan(0);
      expect(b.vx).toBeGreaterThan(0);
      expect(a.vx).toBeCloseTo(-b.vx);
      expect(a.vy).toBe(0);
    });
  });
});
