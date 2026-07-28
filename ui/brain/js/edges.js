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
// crossings would compete at equal weight).
//
// GPU-INSTANCED CURVES, not a CPU-expanded polyline per edge. The graph is not
// sampled, thinned, or capped — every edge whose endpoints are both in the node
// set is drawn, at any corpus size. The live graph has been observed at 531,252
// edges and as large as 5,526,180, ~99% cross-region (curved); expanding each
// into K_CURVE-1 line SEGMENTS on the main thread (the previous approach) meant
// millions of segments — a blocking JS loop building hundreds of MB of
// Float32Array geometry, plus a control-point object per curved edge (GC
// pressure), before a single frame could render.
//   source: measured 2026-07-08 (curl-drained /api/graph/full/stream in 1.5s
//   for 1.17 GB — network was not the bottleneck; the CPU-side segment
//   expansion was; on that snapshot it produced 27.4M segments).
// Curve evaluation is exactly the identical-per-instance math GPUs parallelize,
// so it moves there: the CPU writes one small per-edge attribute record
// (endpoints + control point + endpoint colours + alpha — O(E), no per-segment
// expansion, no per-edge allocation), and an InstancedBufferGeometry draws a
// single shared K_CURVE-point line-strip template once per edge, instanced. The
// vertex shader evaluates the quadratic Bezier per instance. A control point
// equal to the segment midpoint makes that same formula collapse to an exact
// straight line (u²A + 2ut·mid(A,B) + t²B == (1-t)A + tB when C=(A+B)/2), so
// straight edges need no special case and no second draw call.
//
// repaintEdgeFilter / highlightNode get CHEAPER under this layout: alpha and the
// highlight flag are ONE float per EDGE (instance attributes), not one per
// vertex-per-segment, so a repaint writes E floats instead of totalSeg*2 and
// indexes by edge row directly — no vStart/vCount vertex-range bookkeeping.

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
  var K_CURVE = 6;         // template points per edge (=> 5 instanced segments)
  var BOW_MIN = 0.15, BOW_MAX = 1.0;  // edge-length scaling of the tract bow
  // User-driven per-kind filter (BRAIN.filterKind, set by clicking a legend
  // row — boot.js). Default null (NO filtering): every edge keeps its
  // computed length-based alpha. When a kind is selected, edges INCIDENT to
  // a node of that kind (either endpoint) keep full alpha; every other edge
  // dims to this fraction. UI-legibility param, not sourced.
  var FILTER_EDGE_DIM = 0.04;
  // Hover/selection highlight (BRAIN.highlightNode): edges INCIDENT to the
  // node go fully opaque (HL_FLOOR) AND recolour to the terracotta selection
  // accent (iHl=1 -> mix in the shader), while every other edge fades to
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
  // fat-line cost never touches the full edge cloud. UI-legibility param.
  var HL_BOLD_PX = 2.5;
  // The fat-line overlay for the current selection (LineSegments2), or null when
  // nothing is highlighted. Rebuilt per selection change, disposed on deselect.
  var overlay = null;
  var overlayRes = new THREE.Vector2();

  function endId(v) { return (typeof v === 'object' && v) ? v.id : v; }

  var VERT = [
    'attribute vec3 iA;',
    'attribute vec3 iB;',
    'attribute vec3 iC;',
    'attribute vec3 iColorA;',
    'attribute vec3 iColorB;',
    'attribute float iAlpha;',
    'attribute float iHl;',
    'varying float vA;',
    'varying vec3 vC;',
    'varying float vHL;',
    'void main() {',
    // The shared template's only payload is the curve parameter in position.x.
    '  float t = position.x;',
    '  float u = 1.0 - t;',
    '  vec3 p = u * u * iA + 2.0 * u * t * iC + t * t * iB;',
    '  vA = iAlpha; vC = mix(iColorA, iColorB, t); vHL = iHl;',
    '  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);',
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

  // The one shared line-strip template every instance reuses: K_CURVE-1
  // segments as consecutive vertex PAIRS (LineSegments layout), carrying only
  // the curve parameter t in position.x. Identical for straight and curved
  // edges — the control point decides which the shader draws.
  function curveTemplate() {
    var steps = K_CURVE - 1;
    var pos = new Float32Array(steps * 2 * 3);
    for (var k = 0; k < steps; k++) {
      pos[k * 6] = k / steps;             // segment start t
      pos[k * 6 + 3] = (k + 1) / steps;   // segment end t
    }
    return pos;
  }

  // Evaluate the same quadratic Bezier the vertex shader does, on the CPU, for
  // ONE instance. Used only by the fat-line overlay (a handful of incident
  // edges), never on the full cloud — the whole point of the instanced layout
  // is that the hot path never touches this.
  function bezierPoint(inst, i, t, out) {
    var u = 1 - t, w0 = u * u, w1 = 2 * u * t, w2 = t * t;
    var o = i * 3;
    out[0] = w0 * inst.iA[o] + w1 * inst.iC[o] + w2 * inst.iB[o];
    out[1] = w0 * inst.iA[o + 1] + w1 * inst.iC[o + 1] + w2 * inst.iB[o + 1];
    out[2] = w0 * inst.iA[o + 2] + w1 * inst.iC[o + 2] + w2 * inst.iB[o + 2];
  }

  // Build a fat-line overlay for the instances in `rows` (the selected node's
  // incident edges), re-evaluating each one's curve at the same K_CURVE
  // samples the GPU uses so the bold stroke traces the identical path. Draws
  // terracotta (selection accent, DS gate G4) at HL_BOLD_PX, normal-blended
  // (no additive glow, DS gate G6), depth-test off so it floats over the hull
  // like the base web, at a renderOrder between the web (1) and the node
  // cloud (2). Replaces any previous overlay.
  function buildHighlightOverlay(rows) {
    disposeHighlightOverlay();
    if (!rows.length) return;
    var inst = BRAIN.edgeInstances;
    var steps = K_CURVE - 1;
    var cur = [0, 0, 0], nxt = [0, 0, 0];
    var flat = [];
    for (var j = 0; j < rows.length; j++) {
      var i = rows[j];
      bezierPoint(inst, i, 0, cur);
      for (var k = 0; k < steps; k++) {
        bezierPoint(inst, i, (k + 1) / steps, nxt);
        flat.push(cur[0], cur[1], cur[2], nxt[0], nxt[1], nxt[2]);
        cur[0] = nxt[0]; cur[1] = nxt[1]; cur[2] = nxt[2];
      }
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

  // Resolve the tract control point for an edge, or false when it stays
  // straight. ctx: {atlas, R} — built once per buildEdges call. rt: a
  // per-edge scratch object reused across the whole routing loop — regA/
  // hemiA/regB/hemiB/ax/ay/az/bx/by/bz are set by the caller before each
  // call; cx/cy/cz are written back here on a curved result. Zero allocation:
  // rt is allocated once and its fields are overwritten every edge.
  function controlPoint(ctx, rt) {
    var tb = ctx.atlas.tractBow(rt.regA, rt.hemiA, rt.regB, rt.hemiB);
    if (!tb) return false;
    var mx = (rt.ax + rt.bx) / 2, my = (rt.ay + rt.by) / 2, mz = (rt.az + rt.bz) / 2;
    var s = Math.min(Math.max(rt.len / ctx.R, BOW_MIN), BOW_MAX);
    var w = ctx.atlas.bowToWorld(tb.bow);
    rt.cx = mx + w.x * s; rt.cy = my + w.y * s; rt.cz = mz + w.z * s;
    if (tb.midline) rt.cx = mx * 0.15;  // corpus-callosum arch crosses near midline
    return true;
  }

  // Per-edge length fade between ctx.shortLen (full strength) and ctx.longLen
  // (floor). Reads the length rt.len the caller already measured — the single
  // sqrt per edge is shared with the bow scale in controlPoint.
  function edgeAlpha(ctx, rt) {
    var f = (ctx.longLen - rt.len) / ctx.span;
    if (f > 1) f = 1; else if (f < FLOOR) f = FLOOR;
    return BASE_ALPHA * f;
  }

  // Allocate the per-edge instance record. Sized to the edge count; the kept
  // count (instances actually filled) is returned by fillInstances below.
  function allocInstances(E) {
    return {
      iA: new Float32Array(E * 3), iB: new Float32Array(E * 3),
      iC: new Float32Array(E * 3),
      iColorA: new Float32Array(E * 3), iColorB: new Float32Array(E * 3),
      iAlpha: new Float32Array(E), iHl: new Float32Array(E),
      srcRow: new Int32Array(E), dstRow: new Int32Array(E),
      baseAlpha: new Float32Array(E), count: 0,
    };
  }

  // Write one resolved edge into instance slot `n`. Straight edges store the
  // MIDPOINT as their control point, which makes the shader's Bezier collapse
  // to an exact straight line — no special case, no second draw call.
  function writeInstance(inst, n, rt, ctx) {
    var o = n * 3;
    inst.iA[o] = rt.ax; inst.iA[o + 1] = rt.ay; inst.iA[o + 2] = rt.az;
    inst.iB[o] = rt.bx; inst.iB[o + 1] = rt.by; inst.iB[o + 2] = rt.bz;
    inst.iC[o] = rt.cx; inst.iC[o + 1] = rt.cy; inst.iC[o + 2] = rt.cz;
    var nc = ctx.nodeColors, so = rt.so, to = rt.to;
    inst.iColorA[o] = nc[so]; inst.iColorA[o + 1] = nc[so + 1]; inst.iColorA[o + 2] = nc[so + 2];
    inst.iColorB[o] = nc[to]; inst.iColorB[o + 1] = nc[to + 1]; inst.iColorB[o + 2] = nc[to + 2];
    inst.srcRow[n] = rt.si; inst.dstRow[n] = rt.ti;
    var a = edgeAlpha(ctx, rt);
    inst.baseAlpha[n] = a; inst.iAlpha[n] = a; inst.iHl[n] = 0;
  }

  // Single pass over the edge list: resolve endpoints, route, and write the
  // instance record. O(E) with no per-segment expansion and no per-edge
  // allocation — `rt` is one reused scratch object.
  function fillInstances(ctx, inst) {
    var edges = ctx.edges, positions = ctx.positions, indexOfId = ctx.indexOfId;
    var E = edges.length, n = 0, curved = 0, dropped = 0;
    var rt = { si: 0, ti: 0, so: 0, to: 0, regA: 0, hemiA: 0, regB: 0, hemiB: 0,
      ax: 0, ay: 0, az: 0, bx: 0, by: 0, bz: 0, cx: 0, cy: 0, cz: 0, len: 0 };
    for (var i = 0; i < E; i++) {
      var si = indexOfId.get(endId(edges[i].source));
      var ti = indexOfId.get(endId(edges[i].target));
      // Endpoint filtered out of the node set (e.g. calls/member_of/imports to
      // a node the snapshot excluded). Skip it, but COUNT it — a silent drop
      // reads as "every edge drawn" when it is not. source: 22,643 of 5.53M
      // edges (0.41%) dropped, measured 2026-07-01.
      if (si == null || ti == null) { dropped++; continue; }
      rt.si = si; rt.ti = ti; rt.so = si * 3; rt.to = ti * 3;
      rt.regA = ctx.regionKey[si]; rt.hemiA = ctx.hemi[si];
      rt.regB = ctx.regionKey[ti]; rt.hemiB = ctx.hemi[ti];
      rt.ax = positions[rt.so]; rt.ay = positions[rt.so + 1]; rt.az = positions[rt.so + 2];
      rt.bx = positions[rt.to]; rt.by = positions[rt.to + 1]; rt.bz = positions[rt.to + 2];
      var dx = rt.bx - rt.ax, dy = rt.by - rt.ay, dz = rt.bz - rt.az;
      rt.len = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (controlPoint(ctx, rt)) {
        curved++;
      } else {
        rt.cx = (rt.ax + rt.bx) / 2; rt.cy = (rt.ay + rt.by) / 2; rt.cz = (rt.az + rt.bz) / 2;
      }
      writeInstance(inst, n, rt, ctx);
      n++;
    }
    inst.count = n; inst.curved = curved; inst.dropped = dropped;
    return inst;
  }

  function instAttr(arr, size, n) {
    return new THREE.InstancedBufferAttribute(arr.subarray(0, n * size), size);
  }

  // Build the instanced LineSegments mesh and add it to the scene. Depth-test
  // off so the synapse web floats OVER the opaque brain hull — the opaque
  // shell (depthWrite:true) would otherwise occlude every interior tract,
  // leaving only the front-most edges. renderOrder 1 draws the web after the
  // hull (0) and under the node cloud (2). source: DS Spec V-01.
  // frustumCulled MUST stay false: vertex positions are computed in the
  // shader, so three.js's bounding sphere (derived from the t-only template)
  // does not describe where the edges actually are.
  function buildEdgeMesh(inst) {
    var n = inst.count;
    var geom = new THREE.InstancedBufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(curveTemplate(), 3));
    geom.setAttribute('iA', instAttr(inst.iA, 3, n));
    geom.setAttribute('iB', instAttr(inst.iB, 3, n));
    geom.setAttribute('iC', instAttr(inst.iC, 3, n));
    geom.setAttribute('iColorA', instAttr(inst.iColorA, 3, n));
    geom.setAttribute('iColorB', instAttr(inst.iColorB, 3, n));
    geom.setAttribute('iAlpha', instAttr(inst.iAlpha, 1, n));
    geom.setAttribute('iHl', instAttr(inst.iHl, 1, n));
    geom.instanceCount = n;

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

  function logEdgeStats(E, inst) {
    console.log('[brain] edges:', E, '| drawn:', inst.count, '| tract-routed:',
      inst.curved, '-> instanced segments:', inst.count * (K_CURVE - 1));
    if (inst.dropped > 0) {
      console.warn('[brain] dropped', inst.dropped, 'edges (' +
        (100 * inst.dropped / Math.max(E, 1)).toFixed(2) +
        '%) whose endpoint was filtered out of the node set.');
    }
  }

  // edges, positions, indexOfId, nodeColors as before; regionKey/hemi from
  // layout.js; atlas from anatomy.js (for tract bows).
  BRAIN.buildEdges = function (edges, positions, indexOfId, nodeColors, regionKey, hemi, atlas) {
    var R = BRAIN.TARGET_RADIUS || 80;
    // Built once per call (never inside the per-edge loop) and threaded through
    // the fill pass so each helper stays within the §4.4 4-parameter limit.
    var ctx = {
      edges: edges, positions: positions, indexOfId: indexOfId, nodeColors: nodeColors,
      regionKey: regionKey, hemi: hemi, atlas: atlas, R: R,
      shortLen: SHORT_FRAC * R, longLen: LONG_FRAC * R,
    };
    ctx.span = Math.max(ctx.longLen - ctx.shortLen, 1e-3);

    var inst = fillInstances(ctx, allocInstances(edges.length));
    var lines = buildEdgeMesh(inst);

    BRAIN.edgeLines = lines;
    BRAIN.edgeInstances = inst;
    BRAIN.edgeCount = inst.count;
    BRAIN.curvedEdgeCount = inst.curved;
    BRAIN.droppedEdgeCount = inst.dropped;
    logEdgeStats(edges.length, inst);
    return lines;
  };

  // The per-edge alpha factor under the CURRENT BRAIN.filterKind (null => 1.0
  // for all, matching the un-filtered build exactly). BRAIN.nodeKindByRow
  // (boot.js) supplies each endpoint's kind by row.
  function filterFactor(inst, i) {
    var kind = BRAIN.filterKind, kindByRow = BRAIN.nodeKindByRow;
    if (!kind || !kindByRow) return 1.0;
    var sk = kindByRow[inst.srcRow[i]], tk = kindByRow[inst.dstRow[i]];
    return (sk === kind || tk === kind) ? 1.0 : FILTER_EDGE_DIM;
  }

  // Re-derive every edge's alpha from its persisted length-based baseAlpha and
  // the current filter, writing ONE float per edge — no geometry rebuild, and
  // no vertex-range splat (the instance layout made that bookkeeping
  // unnecessary). Same cheap repaint shape as points.js's repaintPointFilter.
  BRAIN.repaintEdgeFilter = function () {
    var inst = BRAIN.edgeInstances, lines = BRAIN.edgeLines;
    if (!inst || !lines) return;
    for (var i = 0; i < inst.count; i++) {
      inst.iAlpha[i] = inst.baseAlpha[i] * filterFactor(inst, i);
      // Back to the plain filter state means no node is highlighted, so every
      // edge reverts to its data colour.
      inst.iHl[i] = 0;
    }
    lines.geometry.getAttribute('iAlpha').needsUpdate = true;
    lines.geometry.getAttribute('iHl').needsUpdate = true;
    // Plain filter state == no selection, so the previous fat-line overlay goes.
    disposeHighlightOverlay();
  };

  // Highlight node `row` and its associations: edges INCIDENT to it brighten to
  // >= HL_FLOOR while every other edge fades to HL_DIM * base, and — in the same
  // pass — the set of neighbour rows is collected and handed to
  // BRAIN.highlightPoints so the endpoints those edges lead to swell too.
  // Honours BRAIN.filterKind. `row < 0` (or null) restores the plain filter
  // state for both edges and points. Callers invoke it only when the
  // highlighted row CHANGES (one buffer re-upload per node, not per tick).
  BRAIN.highlightNode = function (row) {
    var inst = BRAIN.edgeInstances, lines = BRAIN.edgeLines;
    if (!inst || !lines) return;
    if (row == null || row < 0) {
      BRAIN.repaintEdgeFilter();
      if (BRAIN.highlightPoints) BRAIN.highlightPoints(null);
      return;
    }
    var neighbours = new Set();
    neighbours.add(row);
    var incidentRows = [];   // instance indices to redraw as the fat-line overlay
    for (var i = 0; i < inst.count; i++) {
      var sr = inst.srcRow[i], dr = inst.dstRow[i];
      var incident = sr === row || dr === row;
      if (incident) { neighbours.add(sr === row ? dr : sr); incidentRows.push(i); }
      var ff = filterFactor(inst, i);
      inst.iAlpha[i] = incident ? Math.max(inst.baseAlpha[i], HL_FLOOR) * ff
                                : inst.baseAlpha[i] * HL_DIM * ff;
      inst.iHl[i] = incident ? 1 : 0;
    }
    lines.geometry.getAttribute('iAlpha').needsUpdate = true;
    lines.geometry.getAttribute('iHl').needsUpdate = true;
    // Redraw just this node's incident edges as true bold strokes on top of the
    // now-recoloured 1px web (the thin terracotta web still shows through where
    // the fat line doesn't cover, keeping the connection visible end to end).
    buildHighlightOverlay(incidentRows);
    if (BRAIN.highlightPoints) BRAIN.highlightPoints(neighbours);
  };

  // Exposed for the test harness: the pure geometry pieces, so the Bezier
  // collapse-to-straight identity and the template layout can be asserted
  // without a WebGL context.
  BRAIN._edgeInternals = {
    curveTemplate: curveTemplate, bezierPoint: bezierPoint, K_CURVE: K_CURVE,
  };
})();
