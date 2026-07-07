// Cortex Brain View — constrained associative force-relaxation.
//
// Memory-node positions EMERGE from memory<->memory 'associates_with' edges
// (springs) while staying WEAKLY anchored to the anatomical placement
// layout.js already computed (the hippocampus->neocortex consolidation
// gradient). Associative clusters form without discarding that gradient:
// the anchor spring keeps every memory near where its heat rank put it, the
// association spring pulls associated memories together, and there is no
// global repulsion term — the dispersed anchors already provide the
// spreading force, keeping the whole pass O((N+E) * iterations).
//
// Only MEMORY nodes move. Every other node kind (entity/symbol/file/domain/
// ...) keeps the exact anatomical position layout.js assigned it.
//
//   source: Fruchterman & Reingold (1991), "Graph Drawing by Force-Directed
//   Placement", Software: Practice & Experience 21(11):1129-1164 — force
//   model (attractive springs + temperature-based annealing) this module's
//   iteration loop follows.
//
// The constants below are a VISUAL CALIBRATION set, not sourced physical
// constants — only the force-model SHAPE (spring + linear cooling) traces to
// Fruchterman & Reingold; the specific gains are tuned for this scene.

window.BRAIN = window.BRAIN || {};

(function () {
  // How weak is "weakly anchored": K_ANCHOR is the pull back to the
  // anatomical placement; K_ASSOC is the pull between associated memories.
  // K_ASSOC > K_ANCHOR by design — the anchor:assoc ratio (0.05:0.45, ~1:9)
  // is the single knob that trades "stays where consolidation put it" vs
  // "clusters with what it's associated with". Raise K_ANCHOR to hew closer
  // to pure anatomy; raise K_ASSOC to let associative clustering dominate.
  var K_ANCHOR = 0.05;
  var K_ASSOC = 0.45;
  // Per-community attractor pull (Change A, associative-community separation).
  // Community membership comes from communities.js (which reads the
  // server-side Leiden + CPM community_id, detected over the co-entity
  // 'associates_with' channel); K_ASSOC already clusters a community's members
  // tightly together, K_COMMUNITY pulls that cluster's mass toward a point
  // OFFSET from the anatomical centroid so distinct communities separate into
  // visible blobs instead of collapsing onto one shared mass. The two gains
  // are complementary (both pull the same member set in the same rough
  // direction), not opposed, so K_ASSOC did not need lowering to make room
  // for it. Visual calibration, not a sourced physical constant — same status
  // as K_ANCHOR/K_ASSOC above.
  var K_COMMUNITY = 0.08;
  // Each distinct community is pulled toward ITS OWN member centroid (members
  // are already near it -> short displacements -> compact blobs, not streaks),
  // nudged outward from the global memory centroid by this fraction of
  // TARGET_RADIUS so neighbouring communities separate. Visual calibration.
  var COMMUNITY_SEP_FRAC = 0.2;
  var ITERATIONS = 200;          // FR annealing steps; temperature decays 1 -> ~0 across these
  var MAX_STEP_FRAC = 0.04;      // per-iteration displacement cap, as a fraction of TARGET_RADIUS
  var EPS_HEAT = 0.05;           // floor for heat in the reinforcement coupling (avoids a zero-heat node killing its own edges)
  var DEFAULT_RADIUS = 80;       // fallback when BRAIN.TARGET_RADIUS isn't set yet (matches edges.js/scene.js fallback)
  // 360 * (1 - 1/phi), phi = (1+sqrt(5))/2 — the golden angle in degrees.
  // Stepping by this angle around a sphere/circle gives a maximally-spread,
  // deterministic sequence of points for any count (Saff, E.B. & Kuijlaars,
  // A.B.J. (1997), "Distributing many points on a sphere", The Mathematical
  // Intelligencer 19(1):5-11). Used below to place one attractor per
  // community; reused by palette.js for the matching per-community hue step.
  var GOLDEN_ANGLE_RAD = Math.PI * (3 - Math.sqrt(5));

  function endId(v) { return (typeof v === 'object' && v) ? v.id : v; }

  function isMemoryNode(node) {
    return (node.kind || node.type) === 'memory';
  }

  // heat_i, heat_j -> sqrt(max(heat_i,eps) * max(heat_j,eps)). Hot pairs
  // (frequently accessed / recently consolidated memories) bind tighter —
  // this is the "heat/access -> attraction" reinforcement coupling.
  function reinforcement(heatA, heatB, epsHeat) {
    var a = Math.max(typeof heatA === 'number' ? heatA : 0, epsHeat);
    var b = Math.max(typeof heatB === 'number' ? heatB : 0, epsHeat);
    return Math.sqrt(a * b);
  }

  // Collect 'associates_with' edges whose BOTH endpoints resolve to memory
  // nodes, with a precomputed per-edge coefficient
  // K_ASSOC * w_norm_ij * reinforce_ij (constant across iterations, since
  // neither the weight nor the heat changes during relaxation).
  function buildAssociationSprings(edges, indexOfId, memMask, nodes, epsHeat, kAssoc) {
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
      springs[k] = { i: row.i, j: row.j, coef: kAssoc * wNorm * row.r };
    }
    return springs;
  }

  // Deterministic unit point #i of n on a sphere via the golden-angle spiral
  // (see GOLDEN_ANGLE_RAD above). Singleton-safe: n<=1 collapses y to 0
  // (equator) rather than dividing by zero.
  function fibonacciSpherePoint(i, n) {
    var y = n > 1 ? 1 - (i / (n - 1)) * 2 : 0;   // -1 .. 1
    var r = Math.sqrt(Math.max(0, 1 - y * y));
    var theta = GOLDEN_ANGLE_RAD * i;
    return { x: Math.cos(theta) * r, y: y, z: Math.sin(theta) * r };
  }

  // One attractor per DISTINCT community, placed at that community's OWN
  // member centroid and pushed COMMUNITY_SEP_FRAC * targetRadius outward from
  // the global memory centroid so neighbours separate. Members already sit
  // near their centroid, so the pull is short — communities condense into
  // compact blobs rather than streaking toward a distant shared ring. Only
  // communities with >= BRAIN.MIN_COMMUNITY_SIZE members get an entry (the
  // rest fall back to their anatomical anchor in buildAttractorByRow).
  // Deterministic: centroids follow the (stable) positions; the near-centre
  // degenerate case (community centroid ~ global centroid) falls back to a
  // deterministic golden-angle direction so it still separates reproducibly.
  function buildCommunityAttractors(nodes, positions, memIdx, communities, targetRadius) {
    var communityOf = communities.communityOf, sizes = communities.sizes;
    var minSize = BRAIN.MIN_COMMUNITY_SIZE || 1;
    var gx = 0, gy = 0, gz = 0, N = memIdx.length;
    for (var m = 0; m < N; m++) {
      var go = memIdx[m] * 3;
      gx += positions[go]; gy += positions[go + 1]; gz += positions[go + 2];
    }
    if (N > 0) { gx /= N; gy /= N; gz /= N; }
    var acc = {};  // cid -> [sumX, sumY, sumZ, memberCount]
    for (var m2 = 0; m2 < N; m2++) {
      var row = memIdx[m2], o = row * 3;
      var cid = communityOf.get(nodes[row].id);
      if (cid == null || (sizes.get(cid) || 0) < minSize) continue;
      var a = acc[cid] || (acc[cid] = [0, 0, 0, 0]);
      a[0] += positions[o]; a[1] += positions[o + 1]; a[2] += positions[o + 2]; a[3] += 1;
    }
    var sep = targetRadius * COMMUNITY_SEP_FRAC;
    var pts = new Array(communities.count);
    for (var key in acc) {
      var cell = acc[key], k = cell[3];
      var ccx = cell[0] / k, ccy = cell[1] / k, ccz = cell[2] / k;
      var dx = ccx - gx, dy = ccy - gy, dz = ccz - gz;
      var len = Math.sqrt(dx * dx + dy * dy + dz * dz);
      var ux, uy, uz;
      if (len > 1e-3) { ux = dx / len; uy = dy / len; uz = dz / len; }
      else { var v = fibonacciSpherePoint(parseInt(key, 10), communities.count); ux = v.x; uy = v.y; uz = v.z; }
      pts[key] = { x: ccx + ux * sep, y: ccy + uy * sep, z: ccz + uz * sep };
    }
    return pts;
  }

  // Per-memory-row attractor target, flattened to a positions-shaped
  // Float64Array so applyIteration can index it exactly like `anchors`.
  // Rows with no resolvable community (should not happen — detectCommunities
  // assigns every memory node a community, singleton or not) fall back to
  // their own anchor, i.e. contribute zero community force.
  function buildAttractorByRow(nodes, anchors, memIdx, communityOf, sizes, attractorPts) {
    // Only DISTINCT communities (>= BRAIN.MIN_COMMUNITY_SIZE members) get a
    // separating attractor; smaller/singleton communities stay at their
    // anatomical anchor (attractor == anchor => zero community force), so the
    // few real associative clusters separate into blobs instead of thousands
    // of tiny groups streaking toward thousands of scattered attractor points.
    var minSize = BRAIN.MIN_COMMUNITY_SIZE || 1;
    var attrByRow = new Float64Array(anchors.length);
    for (var m = 0; m < memIdx.length; m++) {
      var row = memIdx[m], o = row * 3;
      var cid = communityOf.get(nodes[row].id);
      var big = cid != null && sizes && (sizes.get(cid) || 0) >= minSize;
      var pt = big ? attractorPts[cid] : null;
      attrByRow[o] = pt ? pt.x : anchors[o];
      attrByRow[o + 1] = pt ? pt.y : anchors[o + 1];
      attrByRow[o + 2] = pt ? pt.z : anchors[o + 2];
    }
    return attrByRow;
  }

  // One FR-style displacement pass: anchor spring + community-attractor pull
  // for every memory node, association spring for every precomputed edge,
  // temperature-scaled and step-capped, then clamped back inside the
  // cortical surface. attrByRow/kCommunity are null/0 when community
  // detection found nothing to separate (falls back to pure anchor+assoc).
  function applyIteration(memIdx, springs, positions, anchors, dx, dy, dz,
    kAnchor, temp, maxStep, surface, kCommunity, attrByRow) {
    for (var m = 0; m < memIdx.length; m++) { var i3 = memIdx[m] * 3; dx[i3] = dy[i3] = dz[i3] = 0; }
    for (var m2 = 0; m2 < memIdx.length; m2++) {
      var i = memIdx[m2], o = i * 3;
      dx[o] += kAnchor * (anchors[o] - positions[o]);
      dy[o] += kAnchor * (anchors[o + 1] - positions[o + 1]);
      dz[o] += kAnchor * (anchors[o + 2] - positions[o + 2]);
    }
    if (attrByRow && kCommunity) {
      for (var m4 = 0; m4 < memIdx.length; m4++) {
        var i4 = memIdx[m4], o4 = i4 * 3;
        dx[o4] += kCommunity * (attrByRow[o4] - positions[o4]);
        dy[o4] += kCommunity * (attrByRow[o4 + 1] - positions[o4 + 1]);
        dz[o4] += kCommunity * (attrByRow[o4 + 2] - positions[o4 + 2]);
      }
    }
    for (var s = 0; s < springs.length; s++) {
      var sp = springs[s], oi = sp.i * 3, oj = sp.j * 3, c = sp.coef;
      var ex = c * (positions[oj] - positions[oi]);
      var ey = c * (positions[oj + 1] - positions[oi + 1]);
      var ez = c * (positions[oj + 2] - positions[oi + 2]);
      dx[oi] += ex; dy[oi] += ey; dz[oi] += ez;
      dx[oj] -= ex; dy[oj] -= ey; dz[oj] -= ez;
    }
    for (var m3 = 0; m3 < memIdx.length; m3++) {
      var j = memIdx[m3], p = j * 3;
      var sx = dx[p] * temp, sy = dy[p] * temp, sz = dz[p] * temp;
      var mag = Math.sqrt(sx * sx + sy * sy + sz * sz);
      if (mag > maxStep) { var k = maxStep / mag; sx *= k; sy *= k; sz *= k; }
      var c2 = BRAIN.clampToSurface(positions[p] + sx, positions[p + 1] + sy, positions[p + 2] + sz, surface);
      positions[p] = c2.x; positions[p + 1] = c2.y; positions[p + 2] = c2.z;
    }
  }

  // nodes: graph nodes (same order as `positions`); positions: Float32Array
  // (3N), MUTATED in place for memory-node rows only; edges: graph edges
  // (any kind — this filters to 'associates_with' itself); indexOfId: id ->
  // row-in-`positions` Map (same one buildEdges uses); surface: from
  // BRAIN.buildSurface (needs radiusInDir, consumed via BRAIN.clampToSurface).
  // opts overrides the module constants (used by the numeric test harness to
  // run a small deterministic pass without touching the tuned defaults).
  // opts.communities lets the caller reuse an already-computed
  // BRAIN.detectCommunities() result (boot.js needs it before this runs, to
  // colour memory nodes by community) instead of recomputing it here; when
  // omitted this falls back to computing it itself via BRAIN.detectCommunities
  // if that module is loaded, so relaxAssociative stays usable standalone
  // (e.g. from the numeric test harness) without requiring the caller to
  // always wire communities.js first.
  // Returns { moved, iterations, meanShift, edgeCount, communityCount }.
  BRAIN.relaxAssociative = function (nodes, positions, edges, indexOfId, surface, opts) {
    opts = opts || {};
    var kAnchor = typeof opts.kAnchor === 'number' ? opts.kAnchor : K_ANCHOR;
    var kAssoc = typeof opts.kAssoc === 'number' ? opts.kAssoc : K_ASSOC;
    var kCommunity = typeof opts.kCommunity === 'number' ? opts.kCommunity : K_COMMUNITY;
    var iterations = typeof opts.iterations === 'number' ? opts.iterations : ITERATIONS;
    var epsHeat = typeof opts.epsHeat === 'number' ? opts.epsHeat : EPS_HEAT;
    var radius = (typeof opts.targetRadius === 'number') ? opts.targetRadius : (BRAIN.TARGET_RADIUS || DEFAULT_RADIUS);
    var maxStep = typeof opts.maxStep === 'number' ? opts.maxStep : radius * MAX_STEP_FRAC;

    var n = nodes.length;
    var memMask = new Uint8Array(n);
    var memIdx = [];
    for (var i = 0; i < n; i++) {
      if (isMemoryNode(nodes[i])) { memMask[i] = 1; memIdx.push(i); }
    }

    var springs = buildAssociationSprings(edges, indexOfId, memMask, nodes, epsHeat, kAssoc);
    if (memIdx.length === 0) {
      return { moved: 0, iterations: 0, meanShift: 0, edgeCount: springs.length, communityCount: 0 };
    }

    var anchors = positions.slice();  // anatomical placement, frozen as the anchor target
    var dx = new Float64Array(n * 3), dy = new Float64Array(n * 3), dz = new Float64Array(n * 3);

    var communities = opts.communities ||
      (BRAIN.detectCommunities ? BRAIN.detectCommunities(nodes, edges, indexOfId) : null);
    var attrByRow = null;
    var communityCount = communities ? communities.count : 0;
    if (communities && communities.count > 0) {
      var attractorPts = buildCommunityAttractors(nodes, positions, memIdx, communities, radius);
      attrByRow = buildAttractorByRow(nodes, anchors, memIdx, communities.communityOf, communities.sizes, attractorPts);
    }

    for (var it = 0; it < iterations; it++) {
      var temp = 1 - it / iterations;   // linear FR annealing, 1 -> ~0
      applyIteration(memIdx, springs, positions, anchors, dx, dy, dz, kAnchor, temp, maxStep, surface,
        kCommunity, attrByRow);
    }

    var totalShift = 0;
    for (var m = 0; m < memIdx.length; m++) {
      var o = memIdx[m] * 3;
      var ddx = positions[o] - anchors[o], ddy = positions[o + 1] - anchors[o + 1], ddz = positions[o + 2] - anchors[o + 2];
      totalShift += Math.sqrt(ddx * ddx + ddy * ddy + ddz * ddz);
    }

    return {
      moved: memIdx.length,
      iterations: iterations,
      meanShift: totalShift / memIdx.length,
      edgeCount: springs.length,
      communityCount: communityCount
    };
  };
})();
