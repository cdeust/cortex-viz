// Cortex Brain View — Fruchterman-Reingold associative relaxation.
//
// Memory-node positions EMERGE from the memory<->memory 'associates_with'
// graph. This is a full force-directed placement, not a bucket layout: the
// associative structure — not the domain/kind the memory happens to belong to
// — decides where a memory sits, which is what makes distinct clusters legible
// instead of "partitioned by domain".
//
//   source: Fruchterman, T.M.J. & Reingold, E.M. (1991), "Graph Drawing by
//   Force-Directed Placement", Software: Practice & Experience 21(11):
//   1129-1164. Three forces below trace directly to that paper:
//     - attraction along each edge:   f_a(d) = w * d^2 / k
//     - repulsion between near pairs: f_r(d) = k^2 / d
//     - natural spacing:              k = C * (Volume / N)^(1/3)   [3-D analogue
//       of the paper's k = C*sqrt(Area/N); C = 1, the paper's own convention]
//   At d = k attraction and repulsion have EQUAL magnitude (k), so the model is
//   self-balancing — no invented relative gain between them. Repulsion is
//   truncated to a 2k cutoff via a linked-cell grid: the paper's own "grid
//   variant" (§ Speeding Up), which drops the all-pairs O(N^2) term to O(N) for
//   a roughly uniform point set.
//
// The anatomical placement from layout.js (the hippocampus->neocortex heat
// gradient) is retained ONLY as a WEAK prior: a soft spring back to the seed
// that keeps a memory near its consolidation depth and stops degree-0 memories
// (no associations) from drifting off under pure repulsion. It is deliberately
// too weak to reimpose the bucket structure — associations win.
//
// Only MEMORY nodes move; every other kind keeps its anatomical position.

window.BRAIN = window.BRAIN || {};

(function () {
  // WEAK anatomical prior: soft spring from each memory back to its layout.js
  // seed. This is the ONE hand-set gain in the model; kept small so associative
  // structure dominates while degree-0 nodes still stay put. A memory pushed a
  // distance d from its seed by repulsion settles where kAnchor*d ~ k^2/d, i.e.
  // ~k/sqrt(kAnchor) from the seed. Visual calibration (single knob).
  var K_ANCHOR = 0.20;
  var C_SPACING = 1.0;           // Fruchterman-Reingold's k = C*(V/N)^(1/3); C=1 per the paper
  var CUTOFF_K = 2.0;            // repulsion truncation radius in units of k (grid variant)
  var ITERATIONS = 160;          // FR annealing steps; temperature decays k -> ~0 across these
  var EPS_HEAT = 0.05;           // floor for heat in the reinforcement coupling
  var DEFAULT_RADIUS = 80;       // fallback when BRAIN.TARGET_RADIUS isn't set yet (matches scene.js)

  function endId(v) { return (typeof v === 'object' && v) ? v.id : v; }

  function isMemoryNode(node) {
    return (node.kind || node.type) === 'memory';
  }

  // heat_i, heat_j -> sqrt(max(heat_i,eps) * max(heat_j,eps)). Hot pairs
  // (frequently accessed / recently consolidated memories) bind tighter — the
  // "heat/access -> attraction" reinforcement coupling; scales the edge weight.
  function reinforcement(heatA, heatB, epsHeat) {
    var a = Math.max(typeof heatA === 'number' ? heatA : 0, epsHeat);
    var b = Math.max(typeof heatB === 'number' ? heatB : 0, epsHeat);
    return Math.sqrt(a * b);
  }

  // Collect 'associates_with' edges whose BOTH endpoints resolve to memory
  // nodes, each with a DIMENSIONLESS weight w = w_norm_ij * reinforce_ij in
  // [0,1] (constant across iterations). w scales the FR attraction f_a = w*d^2/k
  // — there is no separate spring gain, so attraction and repulsion stay
  // balanced at the natural spacing k.
  function buildAssociationSprings(edges, indexOfId, memMask, nodes, epsHeat) {
    var rows = [];
    var minW = Infinity, maxW = -Infinity;
    for (var e = 0; e < edges.length; e++) {
      var edge = edges[e];
      if ((edge.kind || edge.type) !== 'associates_with') continue;
      var si = indexOfId.get(endId(edge.source));
      var ti = indexOfId.get(endId(edge.target));
      if (si == null || ti == null || !memMask[si] || !memMask[ti]) continue;
      var w = typeof edge.weight === 'number' ? edge.weight : 0;
      if (w < minW) minW = w;
      if (w > maxW) maxW = w;
      rows.push({ i: si, j: ti, w: w, r: reinforcement(nodes[si].heat, nodes[ti].heat, epsHeat) });
    }
    var span = maxW - minW;
    var springs = new Array(rows.length);
    for (var k = 0; k < rows.length; k++) {
      var row = rows[k];
      var wNorm = span > 1e-9 ? (row.w - minW) / span : 1;
      springs[k] = { i: row.i, j: row.j, w: wNorm * row.r };
    }
    return springs;
  }

  // Fruchterman-Reingold natural spacing for a 3-D layout: the edge length at
  // which attraction and repulsion balance. k = C*(V/N)^(1/3), the direct 3-D
  // analogue of the paper's k = C*sqrt(Area/N). V is the brain's bounding ball.
  function naturalSpacing(nMem, radius) {
    if (nMem < 1) return radius;
    var volume = (4 / 3) * Math.PI * radius * radius * radius;
    return C_SPACING * Math.cbrt(volume / nMem);
  }

  // Accumulate FR repulsion (f_r = k^2 / d) into the force buffers, truncated to
  // a CUTOFF_K*k radius via a linked-cell hash grid (cell size = cutoff), so
  // each node only tests the 27 neighbouring cells — the paper's grid variant.
  function accumulateRepulsion(memIdx, positions, k, dx, dy, dz) {
    var cell = CUTOFF_K * k, inv = 1 / cell, k2 = k * k, cut2 = cell * cell;
    var grid = new Map();
    for (var m = 0; m < memIdx.length; m++) {
      var o = memIdx[m] * 3;
      var key = Math.floor(positions[o] * inv) + ',' +
                Math.floor(positions[o + 1] * inv) + ',' +
                Math.floor(positions[o + 2] * inv);
      var bucket = grid.get(key);
      if (!bucket) { bucket = []; grid.set(key, bucket); }
      bucket.push(memIdx[m]);
    }
    for (var m2 = 0; m2 < memIdx.length; m2++) {
      var i = memIdx[m2], oi = i * 3;
      var xi = positions[oi], yi = positions[oi + 1], zi = positions[oi + 2];
      var bx = Math.floor(xi * inv), by = Math.floor(yi * inv), bz = Math.floor(zi * inv);
      for (var gx = -1; gx <= 1; gx++) {
        for (var gy = -1; gy <= 1; gy++) {
          for (var gz = -1; gz <= 1; gz++) {
            var b = grid.get((bx + gx) + ',' + (by + gy) + ',' + (bz + gz));
            if (!b) continue;
            for (var t = 0; t < b.length; t++) {
              var j = b[t]; if (j === i) continue;
              var oj = j * 3;
              var ex = xi - positions[oj], ey = yi - positions[oj + 1], ez = zi - positions[oj + 2];
              var d2 = ex * ex + ey * ey + ez * ez;
              if (d2 > cut2 || d2 < 1e-9) continue;
              var f = k2 / d2;   // (k^2 / d) / d — magnitude/distance, i.e. unit-vector scale
              dx[oi] += ex * f; dy[oi] += ey * f; dz[oi] += ez * f;
            }
          }
        }
      }
    }
  }

  // One FR displacement pass: weak anchor prior + edge attraction + near-pair
  // repulsion, integrated with a temperature-capped step and clamped inside the
  // cortical surface. `temp` is the max displacement this iteration (cools k->0).
  function applyIteration(ctx, temp) {
    var memIdx = ctx.memIdx, positions = ctx.positions, anchors = ctx.anchors;
    var dx = ctx.dx, dy = ctx.dy, dz = ctx.dz, k = ctx.k, surface = ctx.surface;
    for (var m = 0; m < memIdx.length; m++) { var z = memIdx[m] * 3; dx[z] = dy[z] = dz[z] = 0; }
    // weak anatomical prior
    for (var m2 = 0; m2 < memIdx.length; m2++) {
      var o = memIdx[m2] * 3;
      dx[o] += K_ANCHOR * (anchors[o] - positions[o]);
      dy[o] += K_ANCHOR * (anchors[o + 1] - positions[o + 1]);
      dz[o] += K_ANCHOR * (anchors[o + 2] - positions[o + 2]);
    }
    // attraction along association edges: f_a = w * d^2 / k
    var springs = ctx.springs;
    for (var s = 0; s < springs.length; s++) {
      var sp = springs[s], oi = sp.i * 3, oj = sp.j * 3;
      var ex = positions[oj] - positions[oi], ey = positions[oj + 1] - positions[oi + 1], ez = positions[oj + 2] - positions[oi + 2];
      var d2 = ex * ex + ey * ey + ez * ez;
      if (d2 < 1e-9) continue;
      var d = Math.sqrt(d2);
      var f = sp.w * d / k;   // (w*d^2/k) / d — magnitude/distance
      dx[oi] += ex * f; dy[oi] += ey * f; dz[oi] += ez * f;
      dx[oj] -= ex * f; dy[oj] -= ey * f; dz[oj] -= ez * f;
    }
    // repulsion (grid-truncated)
    accumulateRepulsion(memIdx, positions, k, dx, dy, dz);
    // integrate: displacement = (F/|F|) * min(|F|, temp), then clamp inside hull
    for (var m3 = 0; m3 < memIdx.length; m3++) {
      var p = memIdx[m3] * 3;
      var fx = dx[p], fy = dy[p], fz = dz[p];
      var mag = Math.sqrt(fx * fx + fy * fy + fz * fz);
      if (mag < 1e-9) continue;
      var step = Math.min(mag, temp) / mag;
      var c = BRAIN.clampToSurface(positions[p] + fx * step, positions[p + 1] + fy * step, positions[p + 2] + fz * step, surface);
      positions[p] = c.x; positions[p + 1] = c.y; positions[p + 2] = c.z;
    }
  }

  // nodes: graph nodes (same order as `positions`); positions: Float32Array
  // (3N), MUTATED in place for memory-node rows only; edges: graph edges (any
  // kind — filtered to 'associates_with' here); indexOfId: id -> row Map;
  // surface: from BRAIN.buildSurface (needs radiusInDir, via clampToSurface).
  // opts overrides module constants (used by the numeric test harness).
  // Returns { moved, iterations, meanShift, edgeCount, k }.
  BRAIN.relaxAssociative = function (nodes, positions, edges, indexOfId, surface, opts) {
    opts = opts || {};
    var iterations = typeof opts.iterations === 'number' ? opts.iterations : ITERATIONS;
    var epsHeat = typeof opts.epsHeat === 'number' ? opts.epsHeat : EPS_HEAT;
    var radius = (typeof opts.targetRadius === 'number') ? opts.targetRadius : (BRAIN.TARGET_RADIUS || DEFAULT_RADIUS);

    var n = nodes.length;
    var memMask = new Uint8Array(n);
    var memIdx = [];
    for (var i = 0; i < n; i++) {
      if (isMemoryNode(nodes[i])) { memMask[i] = 1; memIdx.push(i); }
    }

    var springs = buildAssociationSprings(edges, indexOfId, memMask, nodes, epsHeat);
    if (memIdx.length === 0) {
      return { moved: 0, iterations: 0, meanShift: 0, edgeCount: springs.length, k: 0 };
    }

    var k = typeof opts.k === 'number' ? opts.k : naturalSpacing(memIdx.length, radius);
    var anchors = positions.slice();   // anatomical placement, frozen as the weak-prior target
    var ctx = {
      memIdx: memIdx, positions: positions, anchors: anchors, springs: springs,
      k: k, surface: surface,
      dx: new Float64Array(n * 3), dy: new Float64Array(n * 3), dz: new Float64Array(n * 3),
    };

    // Linear cooling: max step k -> ~0 across the run (FR annealing schedule).
    for (var it = 0; it < iterations; it++) {
      applyIteration(ctx, k * (1 - it / iterations));
    }

    var totalShift = 0;
    for (var m = 0; m < memIdx.length; m++) {
      var o = memIdx[m] * 3;
      var ax = positions[o] - anchors[o], ay = positions[o + 1] - anchors[o + 1], az = positions[o + 2] - anchors[o + 2];
      totalShift += Math.sqrt(ax * ax + ay * ay + az * az);
    }
    return {
      moved: memIdx.length, iterations: iterations,
      meanShift: totalShift / memIdx.length, edgeCount: springs.length, k: k,
    };
  };
})();
