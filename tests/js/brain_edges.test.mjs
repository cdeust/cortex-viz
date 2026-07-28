// Brain synapse web — GPU-instanced curve geometry (ui/brain/js/edges.js).
//
// The property under test is the one the corpus depends on: EVERY edge whose
// endpoints are both in the node set is drawn, at any size, and the CPU-side
// geometry does NOT grow per edge. The previous implementation expanded each
// edge into K_CURVE-1 line segments on the main thread (a Float32Array of
// totalSeg*6 floats — 64 MB at 531k edges, hundreds of MB at the 5.5M-edge
// corpus). These tests pin the instanced layout: one constant-size template
// plus O(E) per-edge attribute records, no sampling and no cap.
import { describe, it, expect, beforeEach } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

// Minimal THREE stand-in: edges.js only needs constructors + attribute
// bookkeeping, never a GL context. Geometry records what was set so the tests
// can read instanceCount and the per-instance buffers back.
function installFakeThree() {
  class Vector2 {
    set(x, y) { this.x = x; this.y = y; return this; }
    copy(v) { this.x = v.x; this.y = v.y; return this; }
  }
  class Color {
    constructor(v) { this.value = v; }
    set(v) { this.value = v; return this; }
  }
  // Mirrors three's real semantics: `needsUpdate` is a SETTER that bumps
  // `version` (there is no getter — reading it back yields nothing useful), and
  // `version` is what actually drives the GPU re-upload. Asserting on version
  // is therefore the only assertion that can fail when a repaint forgets to
  // mark the buffer. Verified against three r137's BufferAttribute.
  class BufferAttribute {
    constructor(array, itemSize) {
      this.array = array;
      this.itemSize = itemSize;
      this.version = 0;
    }
    set needsUpdate(value) { if (value === true) this.version += 1; }
    get needsUpdate() { return false; }
  }
  class InstancedBufferAttribute extends BufferAttribute {}
  class BufferGeometry {
    constructor() { this.attributes = {}; this.disposed = false; }
    setAttribute(name, attr) { this.attributes[name] = attr; }
    getAttribute(name) { return this.attributes[name]; }
    dispose() { this.disposed = true; }
  }
  class InstancedBufferGeometry extends BufferGeometry {}
  class LineSegmentsGeometry extends BufferGeometry {
    setPositions(flat) { this.positions = flat; }
  }
  const THREE = {
    Vector2, Color, BufferAttribute, InstancedBufferAttribute,
    BufferGeometry, InstancedBufferGeometry, LineSegmentsGeometry,
    NormalBlending: 'normal',
    ShaderMaterial: class { constructor(o) { Object.assign(this, o); } dispose() {} },
    LineMaterial: class {
      constructor(o) { Object.assign(this, o); this.resolution = new Vector2(); }
      dispose() {}
    },
    LineSegments: class { constructor(g, m) { this.geometry = g; this.material = m; } },
    LineSegments2: class { constructor(g, m) { this.geometry = g; this.material = m; } },
  };
  globalThis.THREE = THREE;
  return THREE;
}

// A world/renderer pair recording what the module adds to the scene.
function installBrainHost() {
  const added = [];
  globalThis.BRAIN = {
    world: { add: (o) => added.push(o), remove: (o) => {
      const i = added.indexOf(o); if (i >= 0) added.splice(i, 1);
    } },
    renderer: { getSize: (v) => v.set(800, 600) },
    TARGET_RADIUS: 80,
  };
  return added;
}

// An atlas that bows edges only between the two regions named in `bowPairs`;
// everything else stays straight (tractBow -> null).
function makeAtlas(bowPairs = []) {
  const key = (a, b) => `${a}|${b}`;
  const set = new Set(bowPairs.map(([a, b]) => key(a, b)));
  return {
    tractBow: (regA, hemiA, regB, hemiB) =>
      set.has(key(regA, regB)) ? { bow: 'slf', midline: false } : null,
    bowToWorld: () => ({ x: 0, y: 10, z: 0 }),
  };
}

// Build `n` nodes on a line and `edges` between the given index pairs.
function scene(n, pairs, { regions = null } = {}) {
  const positions = new Float32Array(n * 3);
  const nodeColors = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    positions[i * 3] = i * 2;
    positions[i * 3 + 1] = i;
    positions[i * 3 + 2] = 0;
    nodeColors[i * 3] = i / Math.max(n - 1, 1);
  }
  const indexOfId = new Map();
  for (let i = 0; i < n; i++) indexOfId.set(`n${i}`, i);
  const edges = pairs.map(([s, t]) => ({ source: `n${s}`, target: `n${t}` }));
  const regionKey = new Int32Array(n);
  const hemi = new Int32Array(n);
  if (regions) regions.forEach((r, i) => { regionKey[i] = r; });
  return { positions, nodeColors, indexOfId, edges, regionKey, hemi };
}

function build(s, atlas) {
  return globalThis.BRAIN.buildEdges(
    s.edges, s.positions, s.indexOfId, s.nodeColors, s.regionKey, s.hemi, atlas
  );
}

describe('brain edges — instanced curve geometry', () => {
  beforeEach(() => {
    installFakeThree();
    installBrainHost();
    globalThis.CortexPalette = { hex: () => '#8a4420' };
    loadScript('ui/brain/js/edges.js');
  });

  it('draws EVERY edge — no cap, no sampling', () => {
    const n = 400;
    const pairs = [];
    for (let i = 0; i < n - 1; i++) pairs.push([i, i + 1]);
    for (let i = 0; i < n - 2; i++) pairs.push([i, i + 2]);  // 797 edges
    const s = scene(n, pairs);
    const lines = build(s, makeAtlas());

    expect(lines.geometry.instanceCount).toBe(pairs.length);
    expect(BRAIN.edgeCount).toBe(pairs.length);
    expect(BRAIN.droppedEdgeCount).toBe(0);
    expect(lines.geometry.getAttribute('iAlpha').array.length).toBe(pairs.length);
  });

  it('CPU geometry does not grow with the edge count', () => {
    // The regression this whole change exists to prevent: the shared template
    // is constant-size, so 10x the edges costs 10x a few floats of instance
    // record — never 10x an expanded segment buffer.
    const small = scene(20, Array.from({ length: 19 }, (_, i) => [i, i + 1]));
    const smallTemplate = build(small, makeAtlas()).geometry
      .getAttribute('position').array.length;

    installBrainHost();
    loadScript('ui/brain/js/edges.js');
    const big = scene(400, Array.from({ length: 399 }, (_, i) => [i, i + 1]));
    const bigGeom = build(big, makeAtlas()).geometry;

    expect(bigGeom.getAttribute('position').array.length).toBe(smallTemplate);
    expect(bigGeom.instanceCount).toBe(399);
  });

  it('template is K_CURVE-1 segments as vertex pairs, t rising 0 -> 1', () => {
    const { curveTemplate, K_CURVE } = BRAIN._edgeInternals;
    const pos = curveTemplate();
    const steps = K_CURVE - 1;
    expect(pos.length).toBe(steps * 2 * 3);
    // Float32Array storage, so compare at float32 precision (~7 decimal
    // digits), not double precision.
    for (let k = 0; k < steps; k++) {
      expect(pos[k * 6]).toBeCloseTo(k / steps, 6);
      expect(pos[k * 6 + 3]).toBeCloseTo((k + 1) / steps, 6);
    }
    expect(pos[0]).toBe(0);
    expect(pos[(steps - 1) * 6 + 3]).toBeCloseTo(1, 6);
  });

  it('a midpoint control point collapses the Bezier to an EXACT straight line', () => {
    // This identity is why straight edges need no special case and no second
    // draw call: u²A + 2ut·mid(A,B) + t²B == (1-t)A + tB.
    const { bezierPoint } = BRAIN._edgeInternals;
    const A = [3, -7, 11], B = [-5, 2, 40];
    const inst = {
      iA: Float32Array.from(A),
      iB: Float32Array.from(B),
      iC: Float32Array.from([(A[0] + B[0]) / 2, (A[1] + B[1]) / 2, (A[2] + B[2]) / 2]),
    };
    const out = [0, 0, 0];
    for (const t of [0, 0.2, 0.5, 0.75, 1]) {
      bezierPoint(inst, 0, t, out);
      for (let d = 0; d < 3; d++) {
        expect(out[d]).toBeCloseTo(A[d] + (B[d] - A[d]) * t, 5);
      }
    }
  });

  it('straight edges store the midpoint; bowed edges store the tract control', () => {
    // regions 0<->1 bow; 0<->0 stays straight.
    const s = scene(3, [[0, 1], [1, 2]], { regions: [0, 0, 1] });
    build(s, makeAtlas([[0, 1]]));
    const inst = BRAIN.edgeInstances;

    // edge 0: n0(reg0) -> n1(reg0) => straight => control == midpoint
    expect(inst.iC[0]).toBeCloseTo((inst.iA[0] + inst.iB[0]) / 2, 5);
    expect(inst.iC[1]).toBeCloseTo((inst.iA[1] + inst.iB[1]) / 2, 5);
    // edge 1: n1(reg0) -> n2(reg1) => bowed => lifted off the midpoint in y
    const midY = (inst.iA[4] + inst.iB[4]) / 2;
    expect(inst.iC[4]).toBeGreaterThan(midY);
    expect(BRAIN.curvedEdgeCount).toBe(1);
  });

  it('counts edges whose endpoint left the node set, and keeps the rest packed', () => {
    const s = scene(3, [[0, 1], [1, 2]]);
    s.edges.push({ source: 'n0', target: 'missing' });
    build(s, makeAtlas());

    expect(BRAIN.droppedEdgeCount).toBe(1);
    expect(BRAIN.edgeCount).toBe(2);
    expect(BRAIN.edgeLines.geometry.instanceCount).toBe(2);
    // The surviving instances stay contiguous — a dropped edge must not leave
    // a zeroed hole that would draw a degenerate line at the origin.
    expect(BRAIN.edgeInstances.srcRow[1]).toBe(1);
    expect(BRAIN.edgeInstances.dstRow[1]).toBe(2);
  });

  it('repaint writes ONE alpha per edge and honours the kind filter', () => {
    const s = scene(3, [[0, 1], [1, 2]]);
    build(s, makeAtlas());
    const inst = BRAIN.edgeInstances;
    const base0 = inst.baseAlpha[0], base1 = inst.baseAlpha[1];

    BRAIN.nodeKindByRow = ['memory', 'entity', 'entity'];
    BRAIN.filterKind = 'memory';
    BRAIN.repaintEdgeFilter();

    // edge 0 touches the memory node -> full; edge 1 does not -> dimmed.
    expect(inst.iAlpha[0]).toBeCloseTo(base0, 6);
    expect(inst.iAlpha[1]).toBeLessThan(base1);
    // version bump == the buffer was marked for GPU re-upload (three's
    // needsUpdate is setter-only). Without this the repaint would be invisible.
    expect(BRAIN.edgeLines.geometry.getAttribute('iAlpha').version).toBe(1);
    expect(BRAIN.edgeLines.geometry.getAttribute('iHl').version).toBe(1);

    BRAIN.filterKind = null;
    BRAIN.repaintEdgeFilter();
    expect(inst.iAlpha[1]).toBeCloseTo(base1, 6);
  });

  it('highlight lights incident edges, dims the rest, and traces them bold', () => {
    const added = installBrainHost();
    loadScript('ui/brain/js/edges.js');
    const s = scene(4, [[0, 1], [1, 2], [2, 3]]);
    build(s, makeAtlas());
    const inst = BRAIN.edgeInstances;
    const seen = [];
    BRAIN.highlightPoints = (set) => seen.push(set);

    BRAIN.highlightNode(1);   // incident: edge 0 (0-1) and edge 1 (1-2)

    expect(inst.iHl[0]).toBe(1);
    expect(inst.iHl[1]).toBe(1);
    expect(inst.iHl[2]).toBe(0);
    expect(inst.iAlpha[0]).toBeGreaterThan(inst.iAlpha[2]);
    // Neighbours handed to the point cloud: the node itself plus both ends.
    expect([...seen[0]].sort()).toEqual([0, 1, 2]);
    // A fat-line overlay was added for exactly the incident edges: 2 edges x
    // (K_CURVE-1) segments x 2 vertices x 3 floats.
    const steps = BRAIN._edgeInternals.K_CURVE - 1;
    const ov = added[added.length - 1];
    expect(ov.geometry.positions.length).toBe(2 * steps * 2 * 3);
  });

  it('deselecting disposes the overlay and restores the plain state', () => {
    const added = installBrainHost();
    loadScript('ui/brain/js/edges.js');
    const s = scene(3, [[0, 1], [1, 2]]);
    const lines = build(s, makeAtlas());
    BRAIN.highlightPoints = () => {};

    BRAIN.highlightNode(1);
    const withOverlay = added.length;
    BRAIN.highlightNode(null);

    expect(added.length).toBe(withOverlay - 1);   // overlay removed from world
    expect(added).toContain(lines);               // the web itself stays
    expect(BRAIN.edgeInstances.iHl[0]).toBe(0);
  });
});
