// Micro-bench + equivalence harness for ui/brain/js/edges.js BRAIN.buildEdges.
//
// Issue #23: buildEdges' hot-path helpers (controlPoint, edgeAlpha, writeEdge,
// pointAt, pushVert) carried 9-13 positional parameters (§4.4 PARAM_COUNT,
// max 4). The fix replaces them with per-call context/scratch objects built
// ONCE per buildEdges invocation and mutated per-edge/per-segment — same
// pattern Introduce Parameter Object already applied one level up by #22
// (resolveEdgeRouting/fillEdgeBuffers taking `ctx`/`routing`).
//
// This harness is the "measure before merging" gate the backlog requires: it
// (1) times BRAIN.buildEdges over a synthetic graph at realistic scale
//     (median of several runs, per tasks/todo.md §2 methodology — see
//     tests/js/run_trigram_conformance.mjs for the sibling pattern), and
// (2) proves the two versions produce BYTE-IDENTICAL output buffers on the
//     exact same input (position/colour/segment arrays, edgeIndex, counts) —
//     the equivalence proof requested by the issue.
//
// Usage:
//   node scripts/bench_brain_edges.mjs <path-to-edges.js> [--iterations N]
//   node scripts/bench_brain_edges.mjs <before.js> <after.js>   # equivalence + compare
//
// Loaded the same way audit_brain_alignment.mjs loads dependency-free brain
// modules: eval() against a stubbed `window`/`BRAIN`/`THREE`, since edges.js
// is a plain browser script (IIFE + window.BRAIN.* exports), not ESM.

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..');

const N_NODES = 50000;   // representative node count (brain view runs 10^4-10^5)
const M_EDGES = 300000;  // matches the trigram bench's synthetic scale (same
                          // order of magnitude as the "358k edges" dense-cloud
                          // case documented in edges.js's own comments)
const DROP_FRAC = 0.004; // ~0.41% measured drop rate documented in edges.js
const CROSS_REGION_FRAC = 0.35; // fraction of edges routed as curved tracts
const REGION_COUNT = 12;
const ITERATIONS_DEFAULT = 9;

// Deterministic PRNG (mulberry32) — same synthetic graph across "before" and
// "after" runs is what makes the equivalence check meaningful.
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildSyntheticGraph() {
  const rand = mulberry32(20260714);
  const R = 80;

  const positions = new Float32Array(N_NODES * 3);
  const nodeColors = new Float32Array(N_NODES * 3);
  const regionKey = new Int32Array(N_NODES);
  const hemi = new Int8Array(N_NODES);
  const indexOfId = new Map();

  for (let i = 0; i < N_NODES; i++) {
    positions[i * 3] = (rand() * 2 - 1) * R;
    positions[i * 3 + 1] = (rand() * 2 - 1) * R;
    positions[i * 3 + 2] = (rand() * 2 - 1) * R;
    nodeColors[i * 3] = rand();
    nodeColors[i * 3 + 1] = rand();
    nodeColors[i * 3 + 2] = rand();
    regionKey[i] = Math.floor(rand() * REGION_COUNT);
    hemi[i] = rand() < 0.5 ? 0 : 1;
    indexOfId.set('n' + i, i);
  }

  const edges = new Array(M_EDGES);
  for (let i = 0; i < M_EDGES; i++) {
    const si = Math.floor(rand() * N_NODES);
    let ti = Math.floor(rand() * N_NODES);
    // Bias a slice of edges cross-region so the curved-tract branch (the one
    // controlPoint/pointAt actually exercise with a control point) gets real
    // coverage, not just the straight-lerp branch.
    if (rand() < CROSS_REGION_FRAC) {
      ti = Math.floor(rand() * N_NODES);
    }
    const sourceId = rand() < DROP_FRAC ? 'ghost-src-' + i : 'n' + si;
    const targetId = rand() < DROP_FRAC ? 'ghost-dst-' + i : 'n' + ti;
    edges[i] = { source: sourceId, target: targetId };
  }

  const atlas = {
    tractBow(regA, hemiA, regB, hemiB) {
      if (regA === regB && hemiA === hemiB) return null;
      return { bow: (regA * 31 + regB * 7 + hemiA * 3 + hemiB) % 11, midline: (regA + regB) % 5 === 0 };
    },
    bowToWorld(bow) {
      const a = bow * 0.37;
      return { x: Math.sin(a), y: Math.cos(a), z: Math.sin(a * 1.3) };
    },
  };

  return { positions, nodeColors, regionKey, hemi, indexOfId, edges, atlas };
}

// Stub THREE + window/BRAIN just enough for BRAIN.buildEdges to run without
// touching a real GPU/DOM. Values are never read back — only the geometry
// attribute arrays and BRAIN.edgeIndex/edgeCount fields are, both populated
// before any THREE object is touched by buildEdgeMesh.
function makeSandbox() {
  class BufferAttribute { constructor(array, itemSize) { this.array = array; this.itemSize = itemSize; } }
  class BufferGeometry {
    constructor() { this._attrs = {}; }
    setAttribute(name, attr) { this._attrs[name] = attr; }
    getAttribute(name) { return this._attrs[name]; }
  }
  class Color { constructor(hex) { this.hex = hex; } set(hex) { this.hex = hex; return this; } }
  class ShaderMaterial { constructor(opts) { Object.assign(this, opts); } }
  class LineSegments { constructor(geometry, material) { this.geometry = geometry; this.material = material; } }
  class Vector2 { constructor() { this.x = 0; this.y = 0; } copy() { return this; } }

  const THREE = { BufferAttribute, BufferGeometry, Color, ShaderMaterial, LineSegments,
    Vector2, NormalBlending: 'normal' };

  const window = {
    addEventListener() {},
    CortexPalette: null,
  };
  const BRAIN = {
    TARGET_RADIUS: 80,
    world: { add() {} },
  };
  return { THREE, window, BRAIN };
}

function loadEdgesModule(filePath) {
  const source = readFileSync(filePath, 'utf8');
  const sandbox = makeSandbox();
  const fn = new Function('THREE', 'window', 'BRAIN', 'console', source + '\nreturn BRAIN;');
  const BRAIN = fn(sandbox.THREE, sandbox.window, sandbox.BRAIN, { log() {}, warn() {} });
  return BRAIN;
}

function callBuildEdges(BRAIN, graph) {
  return BRAIN.buildEdges(graph.edges, graph.positions, graph.indexOfId, graph.nodeColors,
    graph.regionKey, graph.hemi, graph.atlas);
}

function snapshotOutput(BRAIN) {
  const lines = BRAIN.edgeLines;
  const idx = BRAIN.edgeIndex;
  return {
    edgeCount: BRAIN.edgeCount,
    curvedEdgeCount: BRAIN.curvedEdgeCount,
    droppedEdgeCount: BRAIN.droppedEdgeCount,
    position: Array.from(lines.geometry.getAttribute('position').array),
    ealpha: Array.from(lines.geometry.getAttribute('ealpha').array),
    ecolor: Array.from(lines.geometry.getAttribute('ecolor').array),
    srcRow: Array.from(idx.srcRow),
    dstRow: Array.from(idx.dstRow),
    vStart: Array.from(idx.vStart),
    vCount: Array.from(idx.vCount),
    baseAlpha: Array.from(idx.baseAlpha),
  };
}

function arraysEqual(a, b) {
  if (a.length !== b.length) return { equal: false, reason: `length ${a.length} != ${b.length}` };
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return { equal: false, reason: `index ${i}: ${a[i]} != ${b[i]}` };
  }
  return { equal: true };
}

function compareSnapshots(before, after) {
  const scalarFields = ['edgeCount', 'curvedEdgeCount', 'droppedEdgeCount'];
  for (const f of scalarFields) {
    if (before[f] !== after[f]) return { equal: false, reason: `${f}: ${before[f]} != ${after[f]}` };
  }
  const arrayFields = ['position', 'ealpha', 'ecolor', 'srcRow', 'dstRow', 'vStart', 'vCount', 'baseAlpha'];
  for (const f of arrayFields) {
    const r = arraysEqual(before[f], after[f]);
    if (!r.equal) return { equal: false, reason: `${f} — ${r.reason}` };
  }
  return { equal: true };
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function benchOne(filePath, iterations) {
  const graph = buildSyntheticGraph();
  const times = [];
  let snapshot = null;
  for (let i = 0; i < iterations; i++) {
    const BRAIN = loadEdgesModule(filePath); // fresh module instance per run —
    // buildEdges is deterministic in its inputs, but reusing one BRAIN across
    // calls would let mutable BRAIN.edgeIndex leak state between iterations.
    const t0 = performance.now();
    callBuildEdges(BRAIN, graph);
    times.push(performance.now() - t0);
    if (i === iterations - 1) snapshot = snapshotOutput(BRAIN);
  }
  return {
    file: path.relative(repoRoot, filePath),
    n_nodes: N_NODES, m_edges: M_EDGES,
    iterations,
    times_ms: times,
    median_ms: median(times),
    min_ms: Math.min(...times),
    max_ms: Math.max(...times),
    snapshot,
  };
}

function main() {
  const args = process.argv.slice(2);
  const iterFlagIdx = args.indexOf('--iterations');
  const iterations = iterFlagIdx >= 0 ? parseInt(args[iterFlagIdx + 1], 10) : ITERATIONS_DEFAULT;
  const files = args.filter((a) => a !== '--iterations' && a !== args[iterFlagIdx + 1]);

  if (files.length === 0) {
    console.error('usage: node scripts/bench_brain_edges.mjs <edges.js> [<edges2.js>] [--iterations N]');
    process.exit(2);
  }

  const results = files.map((f) => benchOne(path.resolve(f), iterations));
  for (const r of results) {
    console.log(`[bench] ${r.file}: median=${r.median_ms.toFixed(2)}ms min=${r.min_ms.toFixed(2)}ms ` +
      `max=${r.max_ms.toFixed(2)}ms (${r.iterations} runs, N=${r.n_nodes} nodes, M=${r.m_edges} edges)`);
  }

  if (results.length === 2) {
    const cmp = compareSnapshots(results[0].snapshot, results[1].snapshot);
    if (cmp.equal) {
      console.log(`[equivalence] PASS — ${results[0].file} and ${results[1].file} produce byte-identical output buffers.`);
    } else {
      console.error(`[equivalence] FAIL — ${cmp.reason}`);
      process.exit(1);
    }
    const delta = results[1].median_ms - results[0].median_ms;
    const pct = (delta / results[0].median_ms) * 100;
    console.log(`[delta] ${results[1].file} vs ${results[0].file}: ${delta >= 0 ? '+' : ''}${delta.toFixed(2)}ms ` +
      `(${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)`);
  }
}

main();
