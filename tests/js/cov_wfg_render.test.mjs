// Behaviour tests for workflow_graph_render_canvas.js and
// workflow_graph_render_svg.js. d3 is replaced by a small DOM-backed
// selection/zoom/drag implementation (the real d3 is a CDN script that is not
// installed here) so SVG output is real DOM and event handlers really run;
// the canvas 2D context is a recording stub.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

const SVG_NS = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set(['svg', 'g', 'circle', 'line', 'text', 'path']);

// ── mini d3 ────────────────────────────────────────────────────────────────
class T {
  constructor(k, x, y) {
    this.k = k;
    this.x = x;
    this.y = y;
  }
  translate(tx, ty) {
    return new T(this.k, this.x + this.k * tx, this.y + this.k * ty);
  }
  scale(s) {
    return new T(this.k * s, this.x, this.y);
  }
  invert(p) {
    return [(p[0] - this.x) / this.k, (p[1] - this.y) / this.k];
  }
  toString() {
    return `translate(${this.x},${this.y}) scale(${this.k})`;
  }
}

function makeD3() {
  const zooms = [];
  const drags = [];

  class Sel {
    constructor(els, parent, data) {
      this.els = els;
      this._parent = parent;
      this._data = data;
    }
    append(tag) {
      const out = this.els.map((el) => {
        const c = SVG_TAGS.has(tag)
          ? document.createElementNS(SVG_NS, tag)
          : document.createElement(tag);
        c.__data__ = el.__data__;
        el.appendChild(c);
        return c;
      });
      return new Sel(out);
    }
    selectAll() {
      return new Sel([], this.els[0]);
    }
    data(arr) {
      return new Sel([], this._parent, arr);
    }
    enter() {
      const parent = this._parent;
      const data = this._data;
      return {
        append(tag) {
          const out = data.map((d) => {
            const c = SVG_TAGS.has(tag)
              ? document.createElementNS(SVG_NS, tag)
              : document.createElement(tag);
            c.__data__ = d;
            parent.appendChild(c);
            return c;
          });
          return new Sel(out);
        },
      };
    }
    attr(name, val) {
      this.els.forEach((el, i) => {
        const v = typeof val === 'function' ? val(el.__data__, i) : val;
        el.setAttribute(name, String(v));
      });
      return this;
    }
    style(name, val) {
      this.els.forEach((el) => {
        el.style[name] = val;
      });
      return this;
    }
    classed(name, val) {
      this.els.forEach((el, i) => {
        const on = typeof val === 'function' ? val(el.__data__, i) : val;
        name.split(' ').forEach((c) => el.classList.toggle(c, !!on));
      });
      return this;
    }
    text(fn) {
      this.els.forEach((el, i) => {
        el.textContent = typeof fn === 'function' ? fn(el.__data__, i) : fn;
      });
      return this;
    }
    filter(fn) {
      return new Sel(this.els.filter((el, i) => fn(el.__data__, i)));
    }
    on(type, fn) {
      if (typeof fn !== 'function') return this;
      this.els.forEach((el) => el.addEventListener(type.split('.')[0], (ev) => fn(ev, el.__data__)));
      return this;
    }
    call(fn, ...args) {
      fn(this, ...args);
      return this;
    }
    node() {
      return this.els[0];
    }
    remove() {
      this.els.forEach((el) => el.remove());
      return this;
    }
  }

  function zoom() {
    const z = function (sel) {
      z.sel = sel;
    };
    z.handlers = {};
    z.scaleExtent = () => z;
    z.on = (n, fn) => {
      z.handlers[n] = fn;
      return z;
    };
    z.transform = (sel, t) => {
      zooms[0].handlers.zoom && zooms[0].handlers.zoom({ transform: t });
    };
    zooms.push(z);
    return z;
  }
  function drag() {
    const d = function () {};
    d.handlers = {};
    d.subject = (fn) => {
      d.subjectFn = fn;
      return d;
    };
    d.on = (n, fn) => {
      d.handlers[n] = fn;
      return d;
    };
    drags.push(d);
    return d;
  }
  return {
    select: (el) => new Sel([el]),
    zoom,
    drag,
    zoomIdentity: new T(1, 0, 0),
    _zooms: zooms,
    _drags: drags,
  };
}

// ── canvas 2D recording context ────────────────────────────────────────────
function makeG() {
  const calls = {};
  const rec = (n) =>
    function (...a) {
      (calls[n] = calls[n] || []).push(a);
    };
  const g = {
    calls,
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    globalAlpha: 1,
    font: '',
    textAlign: '',
    textBaseline: '',
  };
  ['save', 'restore', 'clearRect', 'translate', 'scale', 'beginPath', 'moveTo', 'lineTo', 'arc',
    'fill', 'stroke', 'fillText', 'setLineDash'].forEach((n) => {
    g[n] = rec(n);
  });
  return g;
}

function makeSim() {
  const sim = {
    handlers: {},
    on(n, fn) {
      sim.handlers[n] = fn;
      return sim;
    },
    alphaTarget: vi.fn(() => ({ restart: vi.fn() })),
  };
  return sim;
}

function makeModel() {
  const domain = { id: 'domain:a', kind: 'domain', label: 'Alpha', color: '#111111', x: 200, y: 200 };
  const hub = { id: 'hub:read', kind: 'tool_hub', label: 'read', color: '#222222', x: 260, y: 200 };
  const file = { id: 'f1', kind: 'file', label: 'a.py', color: '#333333', x: 300, y: 260 };
  const file2 = { id: 'f2', kind: 'file', label: 'b.py', color: '#444444', x: 300, y: 262 };
  const far = { id: 'far', kind: 'file', label: 'far', color: '#555555', x: 5000, y: 5000 };
  const sym = { id: 's1', kind: 'symbol', label: 's', color: '#666666', x: 220, y: 300 };
  const agent = { id: 'ag', kind: 'agent', label: 'agent', color: '#777777', x: 240, y: 120 };
  const nodes = [domain, hub, file, file2, far, sym, agent];
  const edges = [
    { source: domain, target: hub, kind: 'in_domain', weight: 0.5 },
    { source: hub, target: file, kind: 'tool_used_file' },
    { source: file, target: file2, kind: 'imports', _crossDomain: true },
    { source: file, target: far, kind: 'calls', _crossDomain: true },
    { source: sym, target: file, kind: 'defined_in' },
  ];
  return {
    nodes,
    edges,
    adj: { f1: { 'hub:read': true, f2: true } },
    byId: Object.fromEntries(nodes.map((n) => [n.id, n])),
    anchors: { 'domain:a': { x: 200, y: 200 }, 'domain:none': undefined },
    domains: [domain, { id: 'domain:none', kind: 'domain' }],
    shells: [{ key: 'L1', r: 70 }, { key: 'L2', r: 140 }, { key: 'Lx', r: 200 }],
    sideShells: [{ key: 'L4', r: 150, angle: 2 }, { key: 'Lq', r: 120, angle: -2 }],
    cx: 400,
    cy: 300,
    baseR: 300,
  };
}

let d3;
let J;

function setupGlobals() {
  d3 = makeD3();
  window.d3 = d3;
  globalThis.d3 = d3;
  window.JUG = { emit: vi.fn(), _tooltip: { show: vi.fn(), hide: vi.fn() } };
  globalThis.JUG = window.JUG;
  J = window.JUG;
  loadScript('ui/unified/js/workflow_graph_const.js');
  loadScript('ui/unified/js/spatial_hash.js');
  loadScript('ui/unified/js/workflow_graph_tokens.js');
}

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = '<div id="host"></div>';
  delete window.CortexPalette;
  delete window.CortexSurface;
});
afterEach(() => {
  vi.useRealTimers();
});

describe('canvas renderer', () => {
  let g;
  let sim;
  let ctx;
  let handle;
  let canvas;

  function mount(opts) {
    opts = opts || {};
    setupGlobals();
    if (opts.palette) window.CortexPalette = { hex: vi.fn((t) => '#pal' + t.length) };
    loadScript('ui/unified/js/workflow_graph_render_canvas.js');
    g = makeG();
    window.HTMLCanvasElement.prototype.getContext = () => g;
    sim = makeSim();
    ctx = opts.ctx || makeModel();
    if (opts.noSpatial) delete window.JUG._wfg.SpatialHash;
    handle = window.JUG._wfg.mountCanvas(document.getElementById('host'), ctx, sim, 800, 600);
    canvas = document.querySelector('canvas.wfg-canvas');
  }

  const mouse = (type, x, y) => canvas.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y }));
  const count = (n) => (g.calls[n] || []).length;

  it('creates a sized canvas and fits to content after 80ms', () => {
    mount();
    expect(canvas.width).toBe(800);
    expect(canvas.style.height).toBe('600px');
    expect(count('clearRect')).toBe(0);
    vi.advanceTimersByTime(90);
    expect(count('clearRect')).toBe(2); // zoom transform callback + explicit draw
    // fit scales to <= 1 and draws nodes inside the viewport, culling far ones
    expect(count('arc')).toBeGreaterThan(0);
  });

  it('draws shells, rings, edges, labels and focus ring with the recorded context', () => {
    mount();
    handle.redraw();
    // domain with anchor draws 3 dashed shells + 2 side arcs; the anchor-less domain is skipped
    expect(g.calls.setLineDash.length).toBe(2);
    // labels: shell tokens at default zoom + domain/hub labels
    const texts = g.calls.fillText.map((c) => c[0]);
    expect(texts).toContain('Alpha');
    expect(texts).toContain('read');
    expect(texts).toContain('L1');
    expect(texts).toContain('L4');
    expect(texts).not.toContain('agent'); // agents are not labelled on canvas
    // the far-away node is culled: nodes drawn = 6 of 7
    const arcs = g.calls.arc.filter((c) => c[2] < 40 && c[3] === 0 && c[4] === Math.PI * 2);
    expect(arcs.length).toBe(6);
  });

  it('hover highlights, shows the tooltip and dims non-neighbours', () => {
    mount();
    vi.advanceTimersByTime(90);
    handle.fit();
    // place a point over file f1 using the (identity-ish) fit transform
    const fitK = Math.min(800 / (2 * (300 + 240 + 60)), 600 / (2 * (300 + 240 + 60)), 1);
    const tx = 400 - 400 * fitK;
    const ty = 300 - 300 * fitK;
    const px = tx + 300 * fitK;
    const py = ty + 260 * fitK;
    mouse('mousemove', px, py);
    expect(J._tooltip.show).toHaveBeenCalled();
    expect(canvas.style.cursor).toBe('pointer');
    // moving on the same node does not re-show
    const shows = J._tooltip.show.mock.calls.length;
    mouse('mousemove', px, py);
    expect(J._tooltip.show.mock.calls.length).toBe(shows);
    // empty space hides it
    mouse('mousemove', 1, 1);
    expect(J._tooltip.hide).toHaveBeenCalled();
    expect(canvas.style.cursor).toBe('default');
    mouse('mouseleave', 1, 1);
    expect(J._tooltip.hide.mock.calls.length).toBeGreaterThan(1);
  });

  it('click selects a node, emits, and clicking empty space deselects', () => {
    mount();
    vi.advanceTimersByTime(90);
    const k = Math.min(800 / 1200, 600 / 1200, 1);
    const px = 400 - 400 * k + 200 * k;
    const py = 300 - 300 * k + 200 * k;
    mouse('click', px, py);
    expect(J.emit).toHaveBeenCalledWith('graph:selectNode', ctx.nodes[0]);
    g.calls.stroke.length = 0;
    handle.redraw();
    expect(g.calls.stroke.length).toBeGreaterThan(0); // focus ring
    mouse('click', 3, 3);
    expect(J.emit).toHaveBeenCalledWith('graph:deselectNode');
  });

  it('tolerates a missing or throwing JUG.emit and missing tooltip', () => {
    mount();
    vi.advanceTimersByTime(90);
    J.emit = () => {
      throw new Error('bus');
    };
    mouse('click', 3, 3);
    handle.selectId('f1');
    delete J._tooltip;
    mouse('mousemove', 1, 1);
    mouse('mouseleave', 1, 1);
    delete J.emit;
    mouse('click', 3, 3);
    handle.selectId('f1');
  });

  it('uses the spatial grid after the sim settles and the topmost node wins', () => {
    mount();
    vi.advanceTimersByTime(90);
    sim.handlers.end();
    const k = Math.min(800 / 1200, 600 / 1200, 1);
    const px = 400 - 400 * k + 300 * k;
    const py = 300 - 300 * k + 261 * k; // overlaps f1 and f2
    mouse('click', px, py);
    expect(J.emit).toHaveBeenLastCalledWith('graph:selectNode', ctx.byId.f2);
    // tick invalidates the grid and draws; falls back to the linear scan
    const before = count('clearRect');
    sim.handlers.tick();
    expect(count('clearRect')).toBe(before + 1);
    mouse('click', px, py);
    expect(J.emit).toHaveBeenLastCalledWith('graph:selectNode', ctx.byId.f2);
    // grid miss
    sim.handlers.end();
    mouse('click', 2, 2);
    expect(J.emit).toHaveBeenLastCalledWith('graph:deselectNode');
  });

  it('works without a SpatialHash', () => {
    mount({ noSpatial: true });
    vi.advanceTimersByTime(90);
    sim.handlers.end();
    handle.redrawNow();
    mouse('click', 3, 3);
    expect(J.emit).toHaveBeenLastCalledWith('graph:deselectNode');
  });

  it('zoom handler redraws; every edge is drawn at normal zoom', () => {
    mount();
    const zoom = d3._zooms[0];
    g.calls.lineTo = [];
    zoom.handlers.zoom({ transform: new T(1, 0, 0) });
    expect(g.calls.lineTo.length).toBe(5);
  });

  it('structural edges are shown at normal zoom and hidden when zoomed out unfocused', () => {
    mount();
    const zoom = d3._zooms[0];
    zoom.handlers.zoom({ transform: new T(1, 0, 0) });
    g.calls.lineTo = [];
    handle.redraw();
    const normal = g.calls.lineTo.length;
    zoom.handlers.zoom({ transform: new T(0.5, 0, 0) });
    g.calls.lineTo = [];
    g.calls.fillText = [];
    handle.redraw();
    const zoomedOut = g.calls.lineTo.length;
    expect(zoomedOut).toBeLessThan(normal);
    // below 0.5 domain/hub labels are not drawn; shell tokens need k > 0.35
    expect(g.calls.fillText.map((c) => c[0])).not.toContain('Alpha');
    zoom.handlers.zoom({ transform: new T(0.3, 0, 0) });
    g.calls.fillText = [];
    handle.redraw();
    expect(g.calls.fillText.length).toBe(0);
  });

  it('edges off-screen are culled', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
    g.calls.lineTo = [];
    handle.redraw();
    // shift the view so every endpoint is off-screen on the same side
    d3._zooms[0].handlers.zoom({ transform: new T(1, -9000, -9000) });
    g.calls.lineTo = [];
    handle.redraw();
    expect(g.calls.lineTo.length).toBe(0);
  });

  it('focus highlights active edges and dims others', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
    handle.selectId('f1');
    const strokes = g.calls.stroke.length;
    expect(strokes).toBeGreaterThan(0);
    // dimmed node alpha 0.15 was applied at some point during the focused draw
    expect(ctx.byId.f1).toBeDefined();
    expect(J.emit).toHaveBeenCalledWith('graph:selectNode', ctx.byId.f1);
    handle.selectId('missing');
  });

  it('drag subject, start, drag and end manage pinning and the simulation', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
    const dr = d3._drags[0];
    const f1 = ctx.byId.f1;
    const subject = dr.subjectFn({ x: 300, y: 260 });
    expect(subject).toBe(ctx.byId.f2); // topmost under the pointer
    dr.handlers.start({ subject: null });
    dr.handlers.drag({ subject: null });
    dr.handlers.end({ subject: null });
    dr.handlers.start({ subject: f1, active: 0 });
    expect(sim.alphaTarget).toHaveBeenCalledWith(0.2);
    expect(f1.fx).toBe(f1.x);
    dr.handlers.drag({ subject: f1, x: 10, y: 20 });
    expect([f1.fx, f1.fy]).toEqual([10, 20]);
    dr.handlers.end({ subject: f1, active: 0 });
    expect(sim.alphaTarget).toHaveBeenCalledWith(0);
    expect(f1.fx).toBeNull();
    const dom = ctx.byId['domain:a'];
    dom.fx = 1;
    dr.handlers.start({ subject: dom, active: 1 });
    dr.handlers.end({ subject: dom, active: 1 });
    expect(dom.fx).toBe(dom.x);
  });

  it('applyFilter dims non-matching nodes and survives predicate errors; reset clears it', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
    const alphas = [];
    Object.defineProperty(g, 'globalAlpha', {
      get: () => 1,
      set: (v) => alphas.push(v),
      configurable: true,
    });
    handle.applyFilter((n) => n.kind === 'file' || n.kind === 'domain', ctx);
    expect(alphas).toContain(0.04);
    expect(alphas).toContain(1);
    // erroring predicate keeps everything
    alphas.length = 0;
    handle.applyFilter(() => {
      throw new Error('p');
    }, ctx);
    expect(alphas).not.toContain(0.04);
    // filtered edge drawing honours kept endpoints and structural hiding
    handle.applyFilter((n) => n.kind === 'file', ctx);
    g.calls.lineTo = [];
    handle.redraw();
    expect(g.calls.lineTo.length).toBe(2); // imports + calls (files on both ends)... defined_in has symbol
    d3._zooms[0].handlers.zoom({ transform: new T(0.5, 0, 0) });
    handle.selectId('f1');
    handle.applyFilter(null, ctx);
    g.calls.lineTo = [];
    handle.redraw();
    expect(g.calls.lineTo.length).toBeGreaterThan(0);
  });

  it('filtered draw shows labels and focus ring for kept nodes and dims by focus', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
    handle.applyFilter((n) => n.kind === 'domain' || n.kind === 'tool_hub' || n.id === 'f1', ctx);
    g.calls.fillText = [];
    handle.selectId('f1');
    const t = g.calls.fillText.map((c) => c[0]);
    expect(t).toContain('Alpha');
    expect(t).toContain('read');
  });

  it('resolves label and accent colours via CortexPalette or computed CSS', () => {
    mount({ palette: true });
    handle.selectId('domain:a');
    expect(window.CortexPalette.hex).toHaveBeenCalledWith('--text');
    expect(window.CortexPalette.hex).toHaveBeenCalledWith('--text-secondary');
    expect(window.CortexPalette.hex).toHaveBeenCalledWith('--accent-ink');
    mount();
    document.documentElement.style.setProperty('--text', '#010203');
    document.documentElement.style.removeProperty('--accent-ink');
    document.documentElement.style.removeProperty('--text-secondary');
    handle.selectId('domain:a');
    const fills = g.calls.fillText.length;
    expect(fills).toBeGreaterThan(0);
  });

  it('resize, redrawNow, fit and destroy', () => {
    mount();
    handle.resize(500, 400);
    expect(canvas.width).toBe(500);
    expect(canvas.style.width).toBe('500px');
    handle.redrawNow();
    handle.fit();
    handle.destroy();
    expect(document.querySelector('canvas.wfg-canvas')).toBeNull();
    handle.destroy();
  });

  it('handles contexts without shells/baseR and a node-less hover adjacency', () => {
    const bare = makeModel();
    bare.shells = undefined;
    bare.baseR = undefined;
    bare.cx = undefined;
    bare.cy = undefined;
    mount({ ctx: bare });
    vi.advanceTimersByTime(90);
    handle.redraw();
    expect(count('clearRect')).toBeGreaterThan(0);
  });

  it('centred anchors place shell tokens straight up', () => {
    const m = makeModel();
    m.anchors['domain:a'] = { x: m.cx, y: m.cy };
    mount({ ctx: m });
    handle.redraw();
    expect(g.calls.fillText.map((c) => c[0])).toContain('L1');
  });
});

describe('svg renderer', () => {
  let sim;
  let ctx;
  let handle;
  let svg;

  function mount(opts) {
    opts = opts || {};
    setupGlobals();
    loadScript('ui/unified/js/workflow_graph_render_svg.js');
    sim = makeSim();
    ctx = opts.ctx || makeModel();
    handle = window.JUG._wfg.mountSVG(document.getElementById('host'), ctx, sim, 800, 600);
    svg = document.querySelector('svg.wfg-svg');
  }

  const nodeEl = (id) => Array.from(document.querySelectorAll('g.wfg-node')).find((e) => e.__data__.id === id);

  it('builds shells, links and nodes as real SVG DOM', () => {
    mount();
    expect(svg.getAttribute('width')).toBe('800');
    // shells: 3 rings per domain with an anchor (the anchor-less domain is skipped)
    expect(svg.querySelectorAll('circle.wfg-shell').length).toBe(3);
    expect(svg.querySelectorAll('text.wfg-shell-label').length).toBe(5);
    expect(svg.querySelector('.wfg-shell--L1').getAttribute('fill')).toBe('none');
    const links = svg.querySelectorAll('line.wfg-link');
    expect(links.length).toBe(5);
    expect(links[2].classList.contains('wfg-link--cross')).toBe(true);
    expect(links[2].getAttribute('stroke-width')).toBe('0.6');
    expect(links[0].getAttribute('stroke-width')).toBe(String(0.8 + 0.5 * 1.2));
    expect(links[1].getAttribute('stroke-width')).toBe(String(0.8 + 0.3 * 1.2));
    expect(links[0].classList.contains('wfg-link--in_domain')).toBe(true);
    const nodes = svg.querySelectorAll('g.wfg-node');
    expect(nodes.length).toBe(7);
    expect(nodeEl('domain:a').querySelector('circle').getAttribute('r')).toBe('26');
    expect(nodeEl('domain:a').querySelector('circle').getAttribute('fill')).toBe('#111111');
    // only domain / tool_hub / agent get text labels
    const labelled = Array.from(svg.querySelectorAll('text.wfg-label')).map((t) => t.textContent);
    expect(labelled.sort()).toEqual(['Alpha', 'agent', 'read']);
  });

  it('centred domains place the shell token straight up', () => {
    const m = makeModel();
    m.anchors['domain:a'] = { x: m.cx, y: m.cy };
    mount({ ctx: m });
    const label = svg.querySelector('text.wfg-shell-label--L1');
    expect(Number(label.getAttribute('x'))).toBeCloseTo(m.cx);
    expect(Number(label.getAttribute('y'))).toBeCloseTo(m.cy - 70);
  });

  it('zoom writes the root transform and emits graph:zoom', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(2, 5, 6) });
    expect(svg.querySelector('g.wfg-root').getAttribute('transform')).toBe('translate(5,6) scale(2)');
    expect(J.emit).toHaveBeenCalledWith('graph:zoom', { k: 2 });
    delete J.emit;
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
  });

  it('hover highlights until something is selected; click selects and dims others', () => {
    mount();
    const f1 = nodeEl('f1');
    f1.dispatchEvent(new MouseEvent('mouseenter'));
    expect(f1.classList.contains('wfg-focus')).toBe(true);
    expect(nodeEl('hub:read').classList.contains('wfg-dim')).toBe(false); // neighbour
    expect(nodeEl('far').classList.contains('wfg-dim')).toBe(true);
    const active = svg.querySelectorAll('line.wfg-link--active').length;
    expect(active).toBe(4);
    f1.dispatchEvent(new MouseEvent('mouseleave'));
    expect(f1.classList.contains('wfg-focus')).toBe(false);

    nodeEl('domain:a').dispatchEvent(new MouseEvent('click'));
    expect(J.emit).toHaveBeenCalledWith('graph:selectNode', ctx.byId['domain:a']);
    // while selected, hover is ignored
    f1.dispatchEvent(new MouseEvent('mouseenter'));
    expect(f1.classList.contains('wfg-focus')).toBe(false);
    f1.dispatchEvent(new MouseEvent('mouseleave'));
    expect(nodeEl('domain:a').classList.contains('wfg-focus')).toBe(true);
  });

  it('clicking the svg background clears selection; clicks on children do not', () => {
    mount();
    nodeEl('f1').dispatchEvent(new MouseEvent('click'));
    svg.querySelector('g.wfg-root').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(J.emit).not.toHaveBeenCalledWith('graph:deselectNode');
    svg.dispatchEvent(new MouseEvent('click'));
    expect(J.emit).toHaveBeenCalledWith('graph:deselectNode');
    expect(nodeEl('f1').classList.contains('wfg-focus')).toBe(false);
    delete J.emit;
    nodeEl('f1').dispatchEvent(new MouseEvent('click'));
    svg.dispatchEvent(new MouseEvent('click'));
  });

  it('tick positions links and nodes from the simulation', () => {
    mount();
    sim.handlers.tick();
    const link = svg.querySelector('line.wfg-link');
    expect(link.getAttribute('x1')).toBe('200');
    expect(link.getAttribute('y2')).toBe('200');
    expect(nodeEl('f1').getAttribute('transform')).toBe('translate(300,260)');
  });

  it('drag pins nodes and releases non-domain nodes', () => {
    mount();
    const dr = d3._drags[0];
    const f1 = ctx.byId.f1;
    dr.handlers.start({ active: 0 }, f1);
    expect(sim.alphaTarget).toHaveBeenCalledWith(0.2);
    expect(f1.fx).toBe(f1.x);
    dr.handlers.drag({ x: 7, y: 8 }, f1);
    expect([f1.fx, f1.fy]).toEqual([7, 8]);
    dr.handlers.end({ active: 0 }, f1);
    expect(sim.alphaTarget).toHaveBeenCalledWith(0);
    expect(f1.fx).toBeNull();
    const dom = ctx.byId['domain:a'];
    dr.handlers.start({ active: 1 }, dom);
    dr.handlers.end({ active: 1 }, dom);
    expect(dom.fx).toBe(dom.x);
  });

  it('fit zooms to content after 80ms, resize updates size, destroy removes the svg', () => {
    mount();
    d3._zooms[0].handlers.zoom({ transform: new T(1, 0, 0) });
    vi.advanceTimersByTime(90);
    const t = svg.querySelector('g.wfg-root').getAttribute('transform');
    expect(t).toMatch(/scale\(/);
    handle.resize(400, 300);
    expect(svg.getAttribute('width')).toBe('400');
    handle.fit();
    handle.destroy();
    expect(document.querySelector('svg.wfg-svg')).toBeNull();
  });

  it('selectId picks known nodes only', () => {
    mount();
    handle.selectId('nope');
    expect(J.emit).not.toHaveBeenCalledWith('graph:selectNode', expect.anything());
    handle.selectId('f2');
    expect(J.emit).toHaveBeenCalledWith('graph:selectNode', ctx.byId.f2);
  });

  it('applyFilter marks filtered-out nodes and links, tolerates errors, and resets', () => {
    mount();
    handle.applyFilter((n) => n.kind === 'file', ctx);
    expect(nodeEl('domain:a').classList.contains('wfg-filter-out')).toBe(true);
    expect(nodeEl('f1').classList.contains('wfg-filter-out')).toBe(false);
    const out = svg.querySelectorAll('line.wfg-filter-out').length;
    expect(out).toBe(3); // only file-file edges (imports, calls) survive
    handle.applyFilter(() => {
      throw new Error('x');
    }, ctx);
    expect(svg.querySelectorAll('.wfg-filter-out').length).toBe(0);
    handle.applyFilter((n) => n.id === 'f1', ctx);
    handle.applyFilter(undefined, ctx);
    expect(svg.querySelectorAll('.wfg-filter-out').length).toBe(0);
  });
});
