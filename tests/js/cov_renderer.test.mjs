// Behaviour tests for ui/unified/js/renderer.js: force-graph wiring, link
// styling, hover/click selection, brushing/linking and view switching.
// ForceGraph itself is the only stub (a recording chainable fake).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

// Real event bus + state: load the production state.js onto a bare JUG.
function makeJUG(extra) {
  const j = Object.assign({}, extra);
  delete j.state;
  globalThis.JUG = window.JUG = j;
  loadScript('ui/unified/js/state.js');
  return j;
}

let cfg;
let G;
let J;

function makeForceGraph() {
  cfg = {};
  let data = { nodes: [], links: [] };
  const forces = {};
  const chainable = () => {
    const o = {};
    ['strength', 'distanceMax', 'distance', 'iterations'].forEach((m) => {
      o[m] = (fn) => {
        o['_' + m] = fn;
        return o;
      };
    });
    return o;
  };
  G = new Proxy(
    {
      graphData(d) {
        if (d === undefined) return data;
        data = d;
        return G;
      },
      d3Force(name, f) {
        if (f !== undefined) {
          forces[name] = f;
          return G;
        }
        forces[name] = forces[name] || chainable();
        return forces[name];
      },
      linkColor(fn) {
        if (fn === undefined) return cfg.linkColor;
        cfg.linkColor = fn;
        return G;
      },
      linkWidth(fn) {
        if (fn === undefined) return cfg.linkWidth;
        cfg.linkWidth = fn;
        return G;
      },
      _forces: forces,
      _calls: [],
    },
    {
      get(t, k) {
        if (k in t) return t[k];
        return (...a) => {
          cfg[k] = a[0];
          t._calls.push([k, ...a]);
          return G;
        };
      },
    }
  );
  return G;
}

function load() {
  document.body.innerHTML = `
    <div id="graph-container"></div><div id="info-panel"></div>
    <div id="legend"></div><div id="status-bar"></div>`;
  J = makeJUG({
    state: { selectedId: null },
    _currentEdges: [],
    _draw: {
      node: vi.fn(),
      nodeRadius: (n) => (n.r === undefined ? 5 : n.r),
      hitArea() {},
      colorAlpha: (h, a) => h + '@' + a,
    },
    _tooltip: { show: vi.fn(), hide: vi.fn() },
  });
  globalThis.JUG = window.JUG = J;
  globalThis.ForceGraph = () => () => makeForceGraph();
  globalThis.requestAnimationFrame = (fn) => fn();
  loadScript('ui/unified/js/renderer.js');
}

beforeEach(() => {
  vi.useFakeTimers();
  globalThis.d3 = {
    forceCollide: (fn) => {
      const o = { fn, iterations: () => o, strength: () => o };
      return o;
    },
  };
});
afterEach(() => {
  vi.useRealTimers();
  delete globalThis.d3;
  delete window.CortexPalette;
  delete window.CortexSurface;
});

describe('renderer init and forces', () => {
  it('wires ForceGraph options and force tuning', () => {
    load();
    expect(J.getGraph()).toBe(G);
    expect(cfg.nodeId).toBe('id');
    expect(cfg.cooldownTicks).toBe(400);
    expect(cfg.linkCurvature({ type: 'bridge' })).toBe(0.15);
    expect(cfg.linkCurvature({ type: 'calls' })).toBe(0);
    expect(cfg.nodeCanvasObjectMode()).toBe('replace');
    expect(cfg.linkDirectionalParticleColor()).toBe('#A53E00');

    const collide = G._forces.collide;
    expect(collide.fn({ type: 'domain', r: 5 })).toBe(13);
    expect(collide.fn({ type: 'topic', r: 5 })).toBe(10);
    expect(collide.fn({ type: 'memory', r: 5 })).toBe(7);

    const charge = G._forces.charge._strength;
    expect(charge({ type: 'root' })).toBe(-800);
    expect(charge({ type: 'zzz' })).toBe(-18);
    const link = G._forces.link;
    expect(link._distance({ type: 'has-category' })).toBe(180);
    expect(link._distance({})).toBe(30);
    expect(link._strength({ type: 'has-category' })).toBe(0.6);
    expect(link._strength({ type: 'other' })).toBe(0.3);
  });

  it('skips forces without d3, and without a container', () => {
    delete globalThis.d3;
    load();
    expect(G._forces.collide).toBeUndefined();
    document.body.innerHTML = '';
    G = null;
    J = makeJUG({ state: {} });
    globalThis.JUG = window.JUG = J;
    loadScript('ui/unified/js/renderer.js');
    expect(J.getGraph()).toBeNull();
    expect(J.setGraphData([], [])).toBeUndefined();
    expect(J.selectNodeById('x')).toBe(false);
    J.resetCamera();
    J.state.activeView = 'board';
  });

  it('resize resizes the graph to the container', () => {
    load();
    window.dispatchEvent(new Event('resize'));
    expect(G._calls.some((c) => c[0] === 'width')).toBe(true);
  });

  it('boots on DOMContentLoaded when still loading', () => {
    const spy = vi.spyOn(document, 'readyState', 'get').mockReturnValue('loading');
    load();
    expect(J.getGraph()).toBeNull();
    spy.mockRestore();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    expect(J.getGraph()).toBe(G);
  });
});

describe('palette refresh', () => {
  it('reads CortexPalette at load and on surface change', () => {
    window.CortexSurface = { EVENT: 'cortex:surface-change' };
    window.CortexPalette = { hex: vi.fn((n) => (n === '--canvas' ? '#fafafa' : '#112233')) };
    load();
    expect(cfg.backgroundColor).toBe('#fafafa');
    expect(cfg.linkDirectionalParticleColor()).toBe('#112233');
    window.CortexPalette.hex = () => '#000001';
    window.dispatchEvent(new CustomEvent('cortex:surface-change'));
    expect(G._calls.filter((c) => c[0] === 'backgroundColor').pop()[1]).toBe('#000001');
  });

  it('falls back when the palette returns empties', () => {
    window.CortexPalette = { hex: () => '' };
    load();
    expect(cfg.backgroundColor).toBe('#08080f');
    expect(cfg.linkDirectionalParticleColor()).toBe('#A53E00');
  });

  it('works without a colorAlpha helper', () => {
    window.CortexPalette = { hex: () => '#abcdef' };
    document.body.innerHTML = '<div id="graph-container"></div>';
    J = makeJUG({ state: {}, _draw: { hitArea() {}, nodeRadius: () => 1 } });
    delete J._draw.colorAlpha;
    globalThis.JUG = window.JUG = J;
    globalThis.ForceGraph = () => () => makeForceGraph();
    globalThis.requestAnimationFrame = (fn) => fn();
    loadScript('ui/unified/js/renderer.js');
    expect(cfg.linkColor({ source: 'a', target: 'b' })).toBe('#abcdef');
  });
});

describe('interaction', () => {
  const edge = (s, t) => ({ source: s, target: t, weight: 0.5 });

  beforeEach(() => {
    load();
    G.graphData({
      nodes: [
        { id: 'a', x: 1, y: 2 },
        { id: 'b' },
      ],
      links: [],
    });
    J._currentEdges = [edge('a', 'b'), edge({ id: 'b' }, 'c')];
  });

  it('hover updates cursor, tooltip, and refreshes link styles', () => {
    const n = { id: 'a' };
    cfg.onNodeHover(n);
    expect(document.body.style.cursor).toBe('pointer');
    expect(J._tooltip.show).toHaveBeenCalledWith(n);
    // focus on hovered node tiers the links
    expect(cfg.linkColor(edge('a', 'b'))).toMatch(/rgba|@/);
    expect(cfg.linkWidth(edge('a', 'b'))).toBe(1.5);
    expect(cfg.linkWidth(edge('x', 'y'))).toBe(0.15);
    cfg.onNodeHover(null);
    expect(document.body.style.cursor).toBe('default');
    expect(J._tooltip.hide).toHaveBeenCalled();
    expect(cfg.linkWidth({ source: 'x', target: 'y', weight: 1 })).toBeCloseTo(1.6);
    expect(cfg.linkWidth({ source: 'x', target: 'y' })).toBeCloseTo(0.76);
    const dflt = cfg.linkColor(edge('x', 'y'));
    expect(dflt).toBe(cfg.linkColor(edge('q', 'r')));
  });

  it('click selects, emits, tiers edges and particles; click again deselects', () => {
    const events = [];
    J.on('graph:selectNode', (v) => events.push(['sel', v.id]));
    J.on('graph:deselectNode', () => events.push(['desel']));
    cfg.onNodeClick(null);
    cfg.onNodeClick({ id: 'a' });
    expect(J.state.selectedId).toBe('a');
    expect(events).toEqual([['sel', 'a']]);
    expect(cfg.linkDirectionalParticles(edge('a', 'b'))).toBe(3);
    expect(cfg.linkDirectionalParticles(edge({ id: 'z' }, { id: 'a' }))).toBe(3);
    expect(cfg.linkDirectionalParticles(edge('x', 'y'))).toBe(0);
    expect(cfg.linkColor(edge('x', 'y'))).not.toBe(cfg.linkColor(edge('a', 'b')));
    // drawNode passes neighbour set to the draw module
    cfg.nodeCanvasObject({ id: 'b' }, {}, 2);
    const args = J._draw.node.mock.calls.pop();
    expect(args[4]).toBe('a');
    expect(args[5]).toEqual({ a: true, b: true });
    cfg.onNodeClick({ id: 'a' });
    expect(J.state.selectedId).toBeNull();
    expect(events[1]).toEqual(['desel']);
    expect(cfg.linkDirectionalParticles(edge('a', 'b'))).toBe(0);
  });

  it('background click deselects only when something is selected', () => {
    const spy = vi.fn();
    J.on('graph:deselectNode', spy);
    cfg.onBackgroundClick();
    expect(spy).not.toHaveBeenCalled();
    cfg.onNodeClick({ id: 'a' });
    cfg.onBackgroundClick();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('setGraphData feeds the graph and zooms to fit later', () => {
    J.setGraphData([{ id: 'n' }], [edge('n', 'n')]);
    expect(J._currentEdges.length).toBe(1);
    expect(G.graphData().nodes.length).toBe(1);
    vi.advanceTimersByTime(1600);
    expect(G._calls.some((c) => c[0] === 'zoomToFit' && c[1] === 600)).toBe(true);
  });

  it('selectNodeById centres on the node and resetCamera deselects + fits', () => {
    expect(J.selectNodeById('a')).toBe(true);
    expect(J.state.selectedId).toBe('a');
    expect(G._calls.some((c) => c[0] === 'centerAt' && c[1] === 1)).toBe(true);
    expect(J.selectNodeById('nope')).toBe(false);
    J.resetCamera();
    expect(J.state.selectedId).toBeNull();
    expect(G._calls.some((c) => c[0] === 'zoomToFit' && c[1] === 400)).toBe(true);
    J.deselectNode();
  });

  it('syncs externally emitted selection and deselection', () => {
    J.emit('graph:selectNode', { id: 'b' });
    expect(J.state.selectedId).toBe('b');
    expect(cfg.linkDirectionalParticles(edge('b', 'c'))).toBe(3);
    J.emit('graph:selectNode', { id: 'b' }); // same node: no-op
    J.emit('graph:selectNode', { id: 'missing' });
    expect(J.state.selectedId).toBe('missing');
    J.emit('graph:selectNode', null);
    J.emit('graph:deselectNode');
    expect(J.state.selectedId).toBeNull();
    J.emit('graph:deselectNode'); // nothing selected: no-op
  });

  it('view switching toggles containers, animation and restores selection', () => {
    J.state.activeView = 'board';
    expect(document.getElementById('graph-container').style.display).toBe('none');
    expect(document.getElementById('info-panel').style.display).toBe('none');
    expect(G._calls.some((c) => c[0] === 'pauseAnimation')).toBe(true);
    J.state.selectedId = 'a';
    J.state.activeView = 'graph';
    expect(document.getElementById('graph-container').style.display).toBe('block');
    expect(document.getElementById('legend').style.display).toBe('');
    expect(G._calls.some((c) => c[0] === 'resumeAnimation')).toBe(true);
    expect(J.state.selectedId).toBe('a');
    J.state.activeView = 'trace';
    expect(document.getElementById('legend').style.display).toBe('none');
    expect(document.getElementById('status-bar').style.display).toBe('');
  });
});
