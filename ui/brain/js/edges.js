// Cortex Brain View — the synapse web, routed along white-matter tracts.
//
// Every graph edge is drawn as NORMAL-blended line geometry (not additive — the
// DS forbids glow-by-accumulation, G6), endpoints read from the anatomical
// positions layout.js placed. Instead of straight chords through the interior,
// CROSS-REGION edges bow along the major fasciculi — fornix/cingulum (medial
// temporal <-> hubs), uncinate (temporal <-> orbitofrontal), SLF/arcuate
// (frontal <-> parietal), corpus callosum (left <-> right) — so connectivity
// reads as real brain wiring. Short same-region edges stay straight.
//   source: Catani & Thiebaut de Schotten (2008) "A diffusion tensor imaging
//   tractography atlas for virtual in vivo dissections", Cortex 44:1105-1132.
//
// The per-edge alpha FADES WITH LENGTH so the dense surface web leads and long
// interior crossings sink to a floor (still worth doing under normal blending:
// without it, the dense short-edge surface web and the sparse long interior
// crossings would compete at equal weight). Curved edges are sampled into
// K_CURVE points; the segment budget is bounded (curved-edge count is logged)
// so a future tweak can't silently blow the vertex count.

window.BRAIN = window.BRAIN || {};

(function () {
  var SHORT_FRAC = 0.07;   // edges this short (x brain radius) read at full strength
  var LONG_FRAC = 0.62;    // edges this long fade to the floor
  // Deep-ink lines on cream need more contrast than the same hue did as a
  // near-white glow on near-black — bumped from 0.045 (tuned for the ink
  // canvas) so the synapse web stays legible on paper. source: paper
  // re-ink pass 2026-07-04 (README data-family re-inking rule).
  var BASE_ALPHA = 0.09;
  var FLOOR = 0.04;        // fraction of BASE kept for the longest edges
  var K_CURVE = 6;         // sample points per tract-routed edge (=> 5 segments)
  var BOW_MIN = 0.15, BOW_MAX = 1.0;  // edge-length scaling of the tract bow
  // User-driven per-kind filter (BRAIN.filterKind, set by clicking a legend
  // row — boot.js). Default null (NO filtering): every edge keeps its
  // computed length-based alpha. When a kind is selected, edges INCIDENT to
  // a node of that kind (either endpoint) keep full alpha; every other edge
  // dims to this fraction. UI-legibility param, not sourced.
  var FILTER_EDGE_DIM = 0.04;

  function endId(v) { return (typeof v === 'object' && v) ? v.id : v; }

  var VERT = [
    'attribute float ealpha;',
    'attribute vec3 ecolor;',
    'varying float vA;',
    'varying vec3 vC;',
    'void main() {',
    '  vA = ealpha; vC = ecolor;',
    '  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);',
    '}',
  ].join('\n');

  var FRAG = [
    'varying float vA;',
    'varying vec3 vC;',
    'void main() { gl_FragColor = vec4(vC, vA); }',
  ].join('\n');

  // Resolve the tract control point for an edge, or null when it stays straight.
  function controlPoint(atlas, regA, hemiA, regB, hemiB, ax, ay, az, bx, by, bz, R) {
    var tb = atlas.tractBow(regA, hemiA, regB, hemiB);
    if (!tb) return null;
    var mx = (ax + bx) / 2, my = (ay + by) / 2, mz = (az + bz) / 2;
    var len = Math.sqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay) + (bz - az) * (bz - az));
    var s = Math.min(Math.max(len / R, BOW_MIN), BOW_MAX);
    var w = atlas.bowToWorld(tb.bow);
    var cx = mx + w.x * s, cy = my + w.y * s, cz = mz + w.z * s;
    if (tb.midline) cx = mx * 0.15;  // corpus-callosum arch crosses near midline
    return { x: cx, y: cy, z: cz };
  }

  function edgeAlpha(ax, ay, az, bx, by, bz, shortLen, longLen, span) {
    var len = Math.sqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay) + (bz - az) * (bz - az));
    var f = (longLen - len) / span;
    if (f > 1) f = 1; else if (f < FLOOR) f = FLOOR;
    return BASE_ALPHA * f;
  }

  // edges, positions, indexOfId, nodeColors as before; regionKey/hemi from
  // layout.js; atlas from anatomy.js (for tract bows).
  BRAIN.buildEdges = function (edges, positions, indexOfId, nodeColors, regionKey, hemi, atlas) {
    var R = BRAIN.TARGET_RADIUS || 80;
    var shortLen = SHORT_FRAC * R, longLen = LONG_FRAC * R;
    var span = Math.max(longLen - shortLen, 1e-3);
    var E = edges.length;

    // Pass 1: resolve endpoints + control points, size the buffers.
    var srcRow = new Int32Array(E), dstRow = new Int32Array(E);
    var ctrl = new Float32Array(E * 3);
    var segCnt = new Uint8Array(E);
    var totalSeg = 0, curved = 0, dropped = 0;
    for (var i = 0; i < E; i++) {
      var si = indexOfId.get(endId(edges[i].source));
      var ti = indexOfId.get(endId(edges[i].target));
      // Endpoint filtered out of the node set (e.g. calls/member_of/imports to
      // a node the snapshot excluded). Skip it, but COUNT it — a silent drop
      // reads as "every edge drawn" when it is not. source: 22,643 of 5.53M
      // edges (0.41%) dropped, measured 2026-07-01.
      if (si == null || ti == null) { srcRow[i] = -1; dropped++; continue; }
      srcRow[i] = si; dstRow[i] = ti;
      var so = si * 3, to = ti * 3;
      var cp = controlPoint(atlas, regionKey[si], hemi[si], regionKey[ti], hemi[ti],
        positions[so], positions[so + 1], positions[so + 2],
        positions[to], positions[to + 1], positions[to + 2], R);
      if (cp) {
        ctrl[i * 3] = cp.x; ctrl[i * 3 + 1] = cp.y; ctrl[i * 3 + 2] = cp.z;
        segCnt[i] = K_CURVE - 1; totalSeg += K_CURVE - 1; curved++;
      } else {
        segCnt[i] = 1; totalSeg += 1;
      }
    }

    // Pass 2: fill segment geometry (2 verts/segment). vStart/vCount/baseAlpha
    // persist per-edge (indexed by the SAME `i` as edges[]) so a later filter
    // change (repaintEdgeFilter) can re-derive each edge's alpha and splat it
    // across exactly its own vertex range without rebuilding geometry.
    // vStart defaults to -1 (untouched = dropped edge, never written below).
    var seg = new Float32Array(totalSeg * 6);
    var alpha = new Float32Array(totalSeg * 2);
    var ecol = new Float32Array(totalSeg * 6);
    var vStart = new Int32Array(E).fill(-1);
    var vCount = new Int32Array(E);
    var baseAlpha = new Float32Array(E);
    var p = 0;  // vertex pointer (counts vertices, *3 for floats)
    var pa = bezierBuffers();
    for (i = 0; i < E; i++) {
      if (srcRow[i] < 0 || segCnt[i] === 0) continue;
      var so = srcRow[i] * 3, to = dstRow[i] * 3;
      var a = edgeAlpha(positions[so], positions[so + 1], positions[so + 2],
        positions[to], positions[to + 1], positions[to + 2], shortLen, longLen, span);
      var vBefore = p;
      p = writeEdge(i, so, to, ctrl, segCnt[i], positions, nodeColors, seg, ecol, alpha, p, a, pa);
      vStart[i] = vBefore;
      vCount[i] = p - vBefore;
      baseAlpha[i] = a;
    }

    var geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(seg, 3));
    geom.setAttribute('ealpha', new THREE.BufferAttribute(alpha, 1));
    geom.setAttribute('ecolor', new THREE.BufferAttribute(ecol, 3));

    var mat = new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG,
      transparent: true, blending: THREE.NormalBlending, depthWrite: false,
      // depthTest off so the synapse web floats OVER the opaque brain hull — the
      // opaque shell (depthWrite:true) would otherwise occlude every interior
      // tract, leaving only the front-most edges. renderOrder 1 draws the web
      // after the hull (0) and under the node cloud (2). source: DS Spec V-01.
      depthTest: false,
    });
    var lines = new THREE.LineSegments(geom, mat);
    lines.renderOrder = 1;
    lines.frustumCulled = false;
    BRAIN.world.add(lines);
    BRAIN.edgeLines = lines;
    BRAIN.edgeCount = totalSeg;
    BRAIN.curvedEdgeCount = curved;
    BRAIN.droppedEdgeCount = dropped;
    // Persisted for repaintEdgeFilter() (below) — keyed by the same edge
    // index `i` as the `edges` array passed in.
    BRAIN.edgeIndex = { srcRow: srcRow, dstRow: dstRow, vStart: vStart, vCount: vCount, baseAlpha: baseAlpha };
    console.log('[brain] edges:', E, '| drawn:', E - dropped, '| tract-routed:', curved,
      '-> segments:', totalSeg);
    if (dropped > 0) {
      console.warn('[brain] dropped', dropped, 'edges (' +
        (100 * dropped / Math.max(E, 1)).toFixed(2) +
        '%) whose endpoint was filtered out of the node set.');
    }
    return lines;
  };

  // Re-derive every edge's alpha from its persisted length-based baseAlpha
  // and the CURRENT BRAIN.filterKind (null => factor 1.0 for all, matching
  // the un-filtered build exactly), then splat each edge's factor across
  // exactly its own vertex range and re-upload — no geometry rebuild, same
  // cheap repaint shape as points.js's repaintPointFilter. BRAIN.nodeKindByRow
  // (boot.js) supplies each endpoint's kind by row.
  BRAIN.repaintEdgeFilter = function () {
    var idx = BRAIN.edgeIndex, lines = BRAIN.edgeLines;
    if (!idx || !lines) return;
    var attr = lines.geometry.getAttribute('ealpha');
    var arr = attr.array;
    var kind = BRAIN.filterKind;
    var kindByRow = BRAIN.nodeKindByRow;
    for (var i = 0; i < idx.vCount.length; i++) {
      var vc = idx.vCount[i];
      if (vc === 0) continue;
      var ff = 1.0;
      if (kind && kindByRow) {
        var sk = kindByRow[idx.srcRow[i]], tk = kindByRow[idx.dstRow[i]];
        ff = (sk === kind || tk === kind) ? 1.0 : FILTER_EDGE_DIM;
      }
      var a = idx.baseAlpha[i] * ff;
      var vs = idx.vStart[i];
      for (var v = 0; v < vc; v++) arr[vs + v] = a;
    }
    attr.needsUpdate = true;
  };

  // Scratch arrays reused across edges to avoid per-edge allocation.
  function bezierBuffers() { return { cur: [0, 0, 0], nxt: [0, 0, 0] }; }

  // Write one edge (straight: 1 segment; curved: K_CURVE-1 Bezier segments) into
  // the geometry buffers starting at vertex pointer `p`; returns the new
  // pointer. `a` is the edge's final alpha (already length-scaled by the
  // caller) applied to every vertex this edge writes.
  function writeEdge(i, so, to, ctrl, segCount, positions, nodeColors,
    seg, ecol, alpha, p, a, pa) {
    var ax = positions[so], ay = positions[so + 1], az = positions[so + 2];
    var bx = positions[to], by = positions[to + 1], bz = positions[to + 2];
    var cx = ctrl[i * 3], cy = ctrl[i * 3 + 1], cz = ctrl[i * 3 + 2];
    var steps = segCount;          // straight => 1, curved => K_CURVE-1
    var cur = pa.cur, nxt = pa.nxt;
    pointAt(ax, ay, az, cx, cy, cz, bx, by, bz, 0, steps, cur);
    for (var k = 0; k < steps; k++) {
      pointAt(ax, ay, az, cx, cy, cz, bx, by, bz, (k + 1) / steps, steps, nxt);
      var t0 = k / steps, t1 = (k + 1) / steps;
      p = pushVert(seg, ecol, alpha, p, cur, so, to, t0, nodeColors, a);
      p = pushVert(seg, ecol, alpha, p, nxt, so, to, t1, nodeColors, a);
      cur[0] = nxt[0]; cur[1] = nxt[1]; cur[2] = nxt[2];
    }
    return p;
  }

  // Point at parameter t — straight lerp when steps==1, Bezier otherwise.
  function pointAt(ax, ay, az, cx, cy, cz, bx, by, bz, t, steps, out) {
    if (steps === 1) { out[0] = ax + (bx - ax) * t; out[1] = ay + (by - ay) * t; out[2] = az + (bz - az) * t; return; }
    var u = 1 - t, w0 = u * u, w1 = 2 * u * t, w2 = t * t;
    out[0] = w0 * ax + w1 * cx + w2 * bx;
    out[1] = w0 * ay + w1 * cy + w2 * by;
    out[2] = w0 * az + w1 * cz + w2 * bz;
  }

  // Push one vertex: position from `pt`, colour lerped between endpoint colours
  // by parameter t, alpha `a`.
  function pushVert(seg, ecol, alpha, p, pt, so, to, t, nodeColors, a) {
    var o = p * 3;
    seg[o] = pt[0]; seg[o + 1] = pt[1]; seg[o + 2] = pt[2];
    ecol[o] = nodeColors[so] + (nodeColors[to] - nodeColors[so]) * t;
    ecol[o + 1] = nodeColors[so + 1] + (nodeColors[to + 1] - nodeColors[so + 1]) * t;
    ecol[o + 2] = nodeColors[so + 2] + (nodeColors[to + 2] - nodeColors[so + 2]) * t;
    alpha[p] = a;
    return p + 1;
  }
})();
