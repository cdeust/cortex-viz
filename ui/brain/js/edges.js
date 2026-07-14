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
  // Hover/selection highlight (BRAIN.highlightNode): edges INCIDENT to the
  // node go fully opaque (HL_FLOOR) AND recolour to the terracotta selection
  // accent (ehl=1 -> mix in the shader), while every other edge fades to
  // HL_DIM * its base alpha so the incident web is the only thing lit. Floor
  // raised 0.85->1.0 and dim lowered 0.05->0.02 to widen the separation on
  // the dense cloud (screenshot: "Read .c" 25 links lost in 358k edges).
  // UI-legibility params, not sourced.
  var HL_FLOOR = 1.0;
  var HL_DIM = 0.02;
  // Screen-space stroke width (CSS px) for the selected node's incident edges,
  // drawn as a LineSegments2 fat-line OVERLAY on top of the 1px web. Plain
  // THREE.LineSegments ignores lineWidth>1 on WebGL/ANGLE, so the terracotta
  // web was hairline-thin and lost in the dense cloud even after the non-
  // neighbour dimming (user report 2026-07-09: "je te demande des lignes
  // bold"). 2.5px reads as a deliberate bold trace without haloing. Overlay is
  // built on select / disposed on deselect (only ~20 incident edges), so the
  // fat-line cost never touches the full 358k-edge cloud. UI-legibility param.
  var HL_BOLD_PX = 2.5;
  // The fat-line overlay for the current selection (LineSegments2), or null when
  // nothing is highlighted. Rebuilt per selection change, disposed on deselect.
  var overlay = null;
  var overlayRes = new THREE.Vector2();

  function endId(v) { return (typeof v === 'object' && v) ? v.id : v; }

  var VERT = [
    'attribute float ealpha;',
    'attribute vec3 ecolor;',
    'attribute float ehl;',
    'varying float vA;',
    'varying vec3 vC;',
    'varying float vHL;',
    'void main() {',
    '  vA = ealpha; vC = ecolor; vHL = ehl;',
    '  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);',
    '}',
  ].join('\n');

  var FRAG = [
    'uniform vec3 uAccent;',
    'varying float vA;',
    'varying vec3 vC;',
    'varying float vHL;',
    // The selected node's incident edges mix toward the terracotta SELECTION
    // accent (--accent-ink) so they read as one lit web — the same accent the
    // selection ring uses (interact.js). vHL is 0 for every non-incident edge,
    // so the rest of the web keeps its data colours. NOT glow: a normal-blended
    // opaque line, no additive bloom (DS gate G6). Terracotta-as-selection is
    // the one sanctioned accent use (DS gate G4). source: AI Architect DS gate.
    'void main() { gl_FragColor = vec4(mix(vC, uAccent, vHL), vA); }',
  ].join('\n');

  // Terracotta selection accent, read live from the DS token reader (same
  // source + fallback as the selection ring in interact.js). Baked into the
  // material uniform at build; re-read on a surface toggle below.
  function accentHex() {
    return (window.CortexPalette && window.CortexPalette.hex('--accent-ink')) || '#8a4420';
  }

  // Three.js bakes the accent into the material uniform, so a surface toggle
  // (paper <-> ink) needs an explicit re-read + re-tint — same pattern as the
  // selection ring (interact.js) and the brain mesh. Attached once.
  window.addEventListener('cortex:surface-change', function () {
    var lines = BRAIN.edgeLines;
    if (lines && lines.material.uniforms && lines.material.uniforms.uAccent) {
      lines.material.uniforms.uAccent.value.set(accentHex());
    }
    // The fat-line overlay bakes the same accent into its own material uniform,
    // so it needs the identical re-read on a paper<->ink toggle.
    if (overlay) overlay.material.color.set(accentHex());
  });

  // LineMaterial draws in screen space, so its `resolution` uniform must track
  // the canvas size or the stroke width scales wrong after a window resize.
  // Kept in sync here (the overlay is short-lived, but a resize mid-selection
  // must not leave it stale).
  window.addEventListener('resize', function () {
    if (overlay) {
      BRAIN.renderer.getSize(overlayRes);
      overlay.material.resolution.copy(overlayRes);
    }
  });

  // Tear down the current fat-line overlay (geometry + material are GPU
  // resources — dispose them, don't just detach). No-op when none exists.
  function disposeHighlightOverlay() {
    if (!overlay) return;
    BRAIN.world.remove(overlay);
    overlay.geometry.dispose();
    overlay.material.dispose();
    overlay = null;
  }

  // Build a fat-line overlay for the edges whose indices are in `edgeRows`
  // (the selected node's incident edges). Their segment vertices already live
  // in the base geometry's position buffer as consecutive start/end PAIRS —
  // exactly LineSegments2's expected layout — so we copy each edge's own vertex
  // range out and hand the flat array to LineSegmentsGeometry.setPositions. The
  // overlay draws terracotta (the selection accent, DS gate G4) at HL_BOLD_PX,
  // normal-blended (no additive glow, DS gate G6), depth-test off so it floats
  // over the hull like the base web, at a renderOrder between the web (1) and
  // the node cloud (2). Replaces any previous overlay.
  function buildHighlightOverlay(edgeRows) {
    disposeHighlightOverlay();
    if (!edgeRows.length) return;
    var idx = BRAIN.edgeIndex;
    var posArr = BRAIN.edgeLines.geometry.getAttribute('position').array;
    var flat = [];
    for (var j = 0; j < edgeRows.length; j++) {
      var i = edgeRows[j];
      var from = idx.vStart[i] * 3, to = from + idx.vCount[i] * 3;
      for (var f = from; f < to; f++) flat.push(posArr[f]);
    }
    var geo = new THREE.LineSegmentsGeometry();
    geo.setPositions(flat);
    BRAIN.renderer.getSize(overlayRes);
    var mat = new THREE.LineMaterial({
      color: new THREE.Color(accentHex()),
      linewidth: HL_BOLD_PX,
      worldUnits: false,          // linewidth is CSS px, not world units
      transparent: true,
      depthTest: false,
    });
    mat.resolution.copy(overlayRes);
    overlay = new THREE.LineSegments2(geo, mat);
    overlay.renderOrder = 1.5;    // above the 1px web (1), under the node cloud (2)
    overlay.frustumCulled = false;
    BRAIN.world.add(overlay);
  }

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

  // Pass 1: resolve endpoints + control points, size the buffers. Returns the
  // per-edge routing arrays plus segment-count totals that pass 2 needs to
  // size its own buffers.
  function resolveEdgeRouting(ctx) {
    var edges = ctx.edges, positions = ctx.positions, indexOfId = ctx.indexOfId;
    var regionKey = ctx.regionKey, hemi = ctx.hemi, atlas = ctx.atlas, R = ctx.R;
    var E = edges.length;
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
    return {
      E: E, srcRow: srcRow, dstRow: dstRow, ctrl: ctrl, segCnt: segCnt,
      totalSeg: totalSeg, curved: curved, dropped: dropped,
    };
  }

  // Pass 2: fill segment geometry (2 verts/segment). vStart/vCount/baseAlpha
  // persist per-edge (indexed by the SAME `i` as edges[]) so a later filter
  // change (repaintEdgeFilter) can re-derive each edge's alpha and splat it
  // across exactly its own vertex range without rebuilding geometry.
  // vStart defaults to -1 (untouched = dropped edge, never written below).
  function fillEdgeBuffers(ctx, routing) {
    var positions = ctx.positions, nodeColors = ctx.nodeColors;
    var shortLen = ctx.shortLen, longLen = ctx.longLen, span = ctx.span;
    var E = routing.E, srcRow = routing.srcRow, dstRow = routing.dstRow;
    var ctrl = routing.ctrl, segCnt = routing.segCnt, totalSeg = routing.totalSeg;
    var seg = new Float32Array(totalSeg * 6);
    var alpha = new Float32Array(totalSeg * 2);
    var ecol = new Float32Array(totalSeg * 6);
    var vStart = new Int32Array(E).fill(-1);
    var vCount = new Int32Array(E);
    var baseAlpha = new Float32Array(E);
    var p = 0;  // vertex pointer (counts vertices, *3 for floats)
    var pa = bezierBuffers();
    for (var i = 0; i < E; i++) {
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
    return { seg: seg, alpha: alpha, ecol: ecol, vStart: vStart, vCount: vCount, baseAlpha: baseAlpha };
  }

  // Build the THREE.LineSegments mesh from filled buffers and add it to the
  // scene. Depth-test off so the synapse web floats OVER the opaque brain
  // hull — the opaque shell (depthWrite:true) would otherwise occlude every
  // interior tract, leaving only the front-most edges. renderOrder 1 draws
  // the web after the hull (0) and under the node cloud (2). source: DS Spec
  // V-01.
  function buildEdgeMesh(buffers, totalSeg) {
    var geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(buffers.seg, 3));
    geom.setAttribute('ealpha', new THREE.BufferAttribute(buffers.alpha, 1));
    geom.setAttribute('ecolor', new THREE.BufferAttribute(buffers.ecol, 3));
    // Highlight flag per vertex (0 = data colour, 1 = mix to accent). Splatted
    // by highlightNode over an edge's own vertex range, exactly like ealpha;
    // starts all-zero so a fresh build shows no selection tint.
    geom.setAttribute('ehl', new THREE.BufferAttribute(new Float32Array(totalSeg * 2), 1));

    var mat = new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG,
      uniforms: { uAccent: { value: new THREE.Color(accentHex()) } },
      transparent: true, blending: THREE.NormalBlending, depthWrite: false,
      depthTest: false,
    });
    var lines = new THREE.LineSegments(geom, mat);
    lines.renderOrder = 1;
    lines.frustumCulled = false;
    BRAIN.world.add(lines);
    return lines;
  }

  function logEdgeStats(E, dropped, curved, totalSeg) {
    console.log('[brain] edges:', E, '| drawn:', E - dropped, '| tract-routed:', curved,
      '-> segments:', totalSeg);
    if (dropped > 0) {
      console.warn('[brain] dropped', dropped, 'edges (' +
        (100 * dropped / Math.max(E, 1)).toFixed(2) +
        '%) whose endpoint was filtered out of the node set.');
    }
  }

  // edges, positions, indexOfId, nodeColors as before; regionKey/hemi from
  // layout.js; atlas from anatomy.js (for tract bows).
  BRAIN.buildEdges = function (edges, positions, indexOfId, nodeColors, regionKey, hemi, atlas) {
    var R = BRAIN.TARGET_RADIUS || 80;
    // Built once per call (never inside the per-edge loop) and threaded through
    // both passes so each helper stays within the §4.4 4-parameter limit.
    var ctx = {
      edges: edges, positions: positions, indexOfId: indexOfId, nodeColors: nodeColors,
      regionKey: regionKey, hemi: hemi, atlas: atlas, R: R,
      shortLen: SHORT_FRAC * R, longLen: LONG_FRAC * R,
    };
    ctx.span = Math.max(ctx.longLen - ctx.shortLen, 1e-3);

    var routing = resolveEdgeRouting(ctx);
    var buffers = fillEdgeBuffers(ctx, routing);
    var lines = buildEdgeMesh(buffers, routing.totalSeg);

    BRAIN.edgeLines = lines;
    BRAIN.edgeCount = routing.totalSeg;
    BRAIN.curvedEdgeCount = routing.curved;
    BRAIN.droppedEdgeCount = routing.dropped;
    // Persisted for repaintEdgeFilter() (below) — keyed by the same edge
    // index `i` as the `edges` array passed in.
    BRAIN.edgeIndex = {
      srcRow: routing.srcRow, dstRow: routing.dstRow,
      vStart: buffers.vStart, vCount: buffers.vCount, baseAlpha: buffers.baseAlpha,
    };
    logEdgeStats(routing.E, routing.dropped, routing.curved, routing.totalSeg);
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
    // Clear any selection tint too: back to the plain filter state means no
    // node is highlighted, so every edge reverts to its data colour (ehl=0).
    var hlAttr = lines.geometry.getAttribute('ehl');
    var hlArr = hlAttr.array;
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
      for (var v = 0; v < vc; v++) { arr[vs + v] = a; hlArr[vs + v] = 0; }
    }
    attr.needsUpdate = true;
    hlAttr.needsUpdate = true;
    // Back to the plain filter state == no selection, so the fat-line overlay
    // for the previous selection must go too.
    disposeHighlightOverlay();
  };

  // Highlight node `row` and its associations: edges INCIDENT to it brighten to
  // >= HL_FLOOR while every other edge fades to HL_DIM * base, and — in the same
  // pass — the set of neighbour rows is collected and handed to
  // BRAIN.highlightPoints so the endpoints those edges lead to swell too. Reuses
  // the exact per-edge vertex-range splat as repaintEdgeFilter (no geometry
  // rebuild) and honours BRAIN.filterKind. `row < 0` (or null) restores the
  // plain filter state for both edges and points. Callers invoke it only when
  // the highlighted row CHANGES (one buffer re-upload per node, not per tick).
  BRAIN.highlightNode = function (row) {
    var idx = BRAIN.edgeIndex, lines = BRAIN.edgeLines;
    if (!idx || !lines) return;
    if (row == null || row < 0) {
      BRAIN.repaintEdgeFilter();
      if (BRAIN.highlightPoints) BRAIN.highlightPoints(null);
      return;
    }
    var attr = lines.geometry.getAttribute('ealpha');
    var arr = attr.array;
    var hlAttr = lines.geometry.getAttribute('ehl');
    var hlArr = hlAttr.array;
    var kind = BRAIN.filterKind, kindByRow = BRAIN.nodeKindByRow;
    var neighbours = new Set();
    neighbours.add(row);
    var incidentRows = [];   // edge indices to redraw as the fat-line overlay
    for (var i = 0; i < idx.vCount.length; i++) {
      var vc = idx.vCount[i];
      if (vc === 0) continue;
      var sr = idx.srcRow[i], dr = idx.dstRow[i];
      var incident = sr === row || dr === row;
      if (incident) { neighbours.add(sr === row ? dr : sr); incidentRows.push(i); }
      var ff = 1.0;
      if (kind && kindByRow) {
        var sk = kindByRow[sr], tk = kindByRow[dr];
        ff = (sk === kind || tk === kind) ? 1.0 : FILTER_EDGE_DIM;
      }
      var a = incident ? Math.max(idx.baseAlpha[i], HL_FLOOR) * ff
                       : idx.baseAlpha[i] * HL_DIM * ff;
      var hv = incident ? 1 : 0;
      var vs = idx.vStart[i];
      for (var v = 0; v < vc; v++) { arr[vs + v] = a; hlArr[vs + v] = hv; }
    }
    attr.needsUpdate = true;
    hlAttr.needsUpdate = true;
    // Redraw just this node's incident edges as true bold strokes on top of the
    // now-recoloured 1px web (the thin terracotta web still shows through where
    // the fat line doesn't cover, keeping the connection visible end to end).
    buildHighlightOverlay(incidentRows);
    if (BRAIN.highlightPoints) BRAIN.highlightPoints(neighbours);
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
