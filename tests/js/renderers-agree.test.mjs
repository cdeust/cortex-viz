// SVG vs canvas renderers agree on the same model (issue #35 AC#4: workflow
// graph SVG vs canvas renderers agreeing on the same model).
//
// Both ui/unified/js/workflow_graph_render_svg.js and _render_canvas.js derive a
// node's radius / fill / label from the single shared source
// JUG._wfg.{nodeRadius,nodeColor,labelOf}. This test mounts BOTH against the
// same model with lightweight d3 + 2D-context stubs and captures what each
// renderer actually assigns per node — independently: the SVG side from its
// d3 attribute writes, the canvas side by observing the shared accessors. If a
// future edit made one renderer compute geometry/colour/label on its own, the
// two capture maps would diverge and this test fails.
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

const WIDTH = 800;
const HEIGHT = 800;

// Model shared by both renderers. `color` is set so nodeColor resolves without
// touching CSS (kind !== tool_hub returns n.color); positions sit inside the
// canvas viewport so nothing is culled.
const nodes = [
  { id: 'd', kind: 'domain', label: 'Alpha', color: '#111111', x: 100, y: 100 },
  { id: 'f', kind: 'file', label: 'a.py', color: '#222222', x: 200, y: 150 },
  { id: 'm', kind: 'memory', label: 'note', color: '#333333', x: 300, y: 200 },
];
const ctx = {
  nodes,
  edges: [],
  adj: {},
  byId: { d: nodes[0], f: nodes[1], m: nodes[2] },
  anchors: {},
  domains: [],
  shells: [],
  sideShells: [],
  cx: 400,
  cy: 400,
  baseR: 400,
};

// ── d3 stub ────────────────────────────────────────────────────────────────
// A chainable selection that records datum-bound attr('r'|'fill') and text()
// results into the SVG capture map. Everything else is a no-op that keeps the
// chain alive.
function makeSelection(data, capture) {
  const sel = {
    _data: data,
    append() {
      return makeSelection(sel._data, capture);
    },
    selectAll() {
      return makeSelection([], capture);
    },
    data(arr) {
      sel._data = arr;
      return sel;
    },
    enter() {
      return makeSelection(sel._data, capture);
    },
    filter(fn) {
      return makeSelection((sel._data || []).filter((d, i) => fn(d, i)), capture);
    },
    attr(name, val) {
      if (typeof val === 'function' && sel._data) {
        sel._data.forEach((d, i) => {
          const v = val(d, i);
          if (name === 'r') capture(d).r = v;
          else if (name === 'fill') capture(d).fill = v;
        });
      }
      return sel;
    },
    style() {
      return sel;
    },
    classed() {
      return sel;
    },
    text(fn) {
      if (typeof fn === 'function' && sel._data) {
        sel._data.forEach((d, i) => {
          capture(d).label = fn(d, i);
        });
      }
      return sel;
    },
    call(fn, ...args) {
      if (typeof fn === 'function') fn(sel, ...args);
      return sel;
    },
    on() {
      return sel;
    },
    node() {
      return { clientWidth: WIDTH, clientHeight: HEIGHT };
    },
    remove() {},
  };
  return sel;
}

function makeBehavior() {
  const b = function () {};
  b.scaleExtent = () => b;
  b.subject = () => b;
  b.on = () => b;
  b.transform = () => {};
  return b;
}

function makeD3(capture) {
  return {
    select: () => makeSelection(null, capture),
    zoom: () => makeBehavior(),
    drag: () => makeBehavior(),
    zoomIdentity: {
      k: 1,
      x: 0,
      y: 0,
      invert: (p) => p,
      translate() {
        return this;
      },
      scale() {
        return this;
      },
    },
  };
}

// ── 2D context stub ──────────────────────────────────────────────────────────
function makeCtx2D() {
  return {
    save() {},
    restore() {},
    clearRect() {},
    translate() {},
    scale() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    arc() {},
    fill() {},
    stroke() {},
    fillText() {},
    setLineDash() {},
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    globalAlpha: 1,
    font: '',
    textAlign: '',
    textBaseline: '',
  };
}

function makeSim() {
  const sim = {
    on() {
      return sim;
    },
    alphaTarget() {
      return { restart() {} };
    },
    alpha() {
      return sim;
    },
    restart() {},
  };
  return sim;
}

let capturedSVG;
let capturedCanvas;

beforeAll(() => {
  vi.useFakeTimers(); // hold the renderers' setTimeout(fitToContent, 80)
  capturedSVG = {};
  capturedCanvas = {};

  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  window.d3 = makeD3((d) => (capturedSVG[d.id] || (capturedSVG[d.id] = {})));
  globalThis.d3 = window.d3;
  window.HTMLCanvasElement.prototype.getContext = () => makeCtx2D();

  loadScript('ui/unified/js/workflow_graph.js'); // defines wfg.nodeRadius/Color/labelOf
  loadScript('ui/unified/js/workflow_graph_render_svg.js');
  loadScript('ui/unified/js/workflow_graph_render_canvas.js');

  const wfg = window.JUG._wfg;
  const container = document.createElement('div');
  document.body.appendChild(container);

  // SVG first — its attr writes call the REAL shared accessors; capture via d3.
  wfg.mountSVG(container, ctx, makeSim(), WIDTH, HEIGHT);

  // Then wrap the shared accessors to record what the canvas renderer asks for,
  // and force one synchronous draw via the handle's redraw().
  const realR = wfg.nodeRadius;
  const realC = wfg.nodeColor;
  const realL = wfg.labelOf;
  const canvasContainer = document.createElement('div');
  document.body.appendChild(canvasContainer);
  wfg.nodeRadius = (n) => {
    const v = realR(n);
    (capturedCanvas[n.id] || (capturedCanvas[n.id] = {})).r = v;
    return v;
  };
  wfg.nodeColor = (n) => {
    const v = realC(n);
    (capturedCanvas[n.id] || (capturedCanvas[n.id] = {})).fill = v;
    return v;
  };
  wfg.labelOf = (n) => {
    const v = realL(n);
    (capturedCanvas[n.id] || (capturedCanvas[n.id] = {})).label = v;
    return v;
  };
  const handle = wfg.mountCanvas(canvasContainer, ctx, makeSim(), WIDTH, HEIGHT);
  handle.redraw();

  // restore
  wfg.nodeRadius = realR;
  wfg.nodeColor = realC;
  wfg.labelOf = realL;
});

afterAll(() => {
  vi.useRealTimers();
});

describe('SVG vs canvas node model', () => {
  it('both renderers captured every node', () => {
    for (const n of nodes) {
      expect(capturedSVG[n.id], `SVG missing ${n.id}`).toBeDefined();
      expect(capturedCanvas[n.id], `canvas missing ${n.id}`).toBeDefined();
    }
  });

  it('agree on radius for every node', () => {
    for (const n of nodes) {
      expect(capturedCanvas[n.id].r).toBe(capturedSVG[n.id].r);
    }
  });

  it('agree on fill colour for every node', () => {
    for (const n of nodes) {
      expect(capturedCanvas[n.id].fill).toBe(capturedSVG[n.id].fill);
    }
  });

  it('agree on the label where both renderers draw one (domain hub)', () => {
    expect(capturedSVG.d.label).toBe('Alpha');
    expect(capturedCanvas.d.label).toBe(capturedSVG.d.label);
  });

  // Sanity: the captured values are the real shared-model values, not stub
  // artifacts — domain radius is KIND_RADIUS.domain (26) with no size bump.
  it('radii match the shared KIND_RADIUS table', () => {
    expect(capturedSVG.d.r).toBe(26); // domain
    expect(capturedSVG.f.r).toBe(5); // file
    expect(capturedSVG.m.r).toBe(7); // memory
  });
});
