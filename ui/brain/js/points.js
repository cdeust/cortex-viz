// Cortex Brain View — the node cloud.
//
// Builds one THREE.Points object for the whole graph: a position buffer from
// layout.js, a per-node colour (its domain's lobe hue, so the brain reads as
// coloured territories) and a per-node size derived from the node's heat/size.
// A tiny ShaderMaterial draws each node as a soft round FLAT INK dot — normal
// (not additive) blending, so dense memory regions read as layered ink on
// paper instead of piling up into an additive white-out glow. source:
// AI Architect Design System README §3 ("no black backgrounds... colour only
// from data"; paper doctrine drops emissive glow for flat, legible marks).

window.BRAIN = window.BRAIN || {};

(function () {
  var BASE_SIZE = 1.35;    // world point size for a cold node (was 1.05)
  var HEAT_GAIN = 1.8;     // extra size at heat = 1
  var HUB_KINDS = { domain: 3.4, tool_hub: 2.0, mcp: 2.4, skill: 1.8, agent: 1.8 };
  // Per-point opacity. Normal blending (not additive) means alpha no longer
  // needs to be capped against a white-out ceiling — it can sit closer to
  // opaque so each node reads as a solid ink dot. source: paper re-ink pass.
  var POINT_ALPHA = 0.82;
  // User-driven per-kind filter (BRAIN.filterKind, set by clicking a legend
  // row — boot.js). Default BRAIN.filterKind is null (NO filtering): every
  // node renders at POINT_ALPHA. When a kind is selected, nodes of that kind
  // stay at full alpha and get a small size boost to pop against the dimmed
  // context; every other kind dims to FILTER_DIM_ALPHA. UI-legibility
  // params, not sourced.
  var FILTER_DIM_ALPHA = 0.05;
  var FILTER_KIND_SIZE_GAIN = 1.4;

  var VERT = [
    'attribute float size;',
    'attribute vec3 ncolor;',
    'attribute float palpha;',
    'varying vec3 vColor;',
    'varying float vAlpha;',
    'void main() {',
    '  vColor = ncolor;',
    '  vAlpha = palpha;',
    '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
    '  gl_PointSize = size * (300.0 / -mv.z);',
    '  gl_Position = projectionMatrix * mv;',
    '}',
  ].join('\n');

  var FRAG = [
    'varying vec3 vColor;',
    'varying float vAlpha;',
    'void main() {',
    '  vec2 d = gl_PointCoord - vec2(0.5);',
    '  float r = length(d);',
    '  if (r > 0.5) discard;',
    '  float a = smoothstep(0.5, 0.0, r) * vAlpha;',
    '  gl_FragColor = vec4(vColor, a);',
    '}',
  ].join('\n');

  function nodeSize(n) {
    var hub = HUB_KINDS[n.kind] || 0;
    var heat = typeof n.heat === 'number' ? Math.min(Math.max(n.heat, 0), 1) : 0;
    var size = BASE_SIZE + hub + heat * HEAT_GAIN;
    if (BRAIN.filterKind && (n.kind || n.type) === BRAIN.filterKind) {
      size *= FILTER_KIND_SIZE_GAIN;
    }
    return size;
  }

  // Per-node alpha attribute: POINT_ALPHA everywhere when BRAIN.filterKind is
  // null (default, no filter — unchanged behaviour); when a kind is
  // selected, that kind keeps POINT_ALPHA and everything else dims to
  // FILTER_DIM_ALPHA.
  function nodeAlpha(n) {
    if (!BRAIN.filterKind) return POINT_ALPHA;
    return (n.kind || n.type) === BRAIN.filterKind ? POINT_ALPHA : FILTER_DIM_ALPHA;
  }

  // nodes: array of graph nodes; positions: Float32Array(3*N) from layout.js;
  // nodeColors: Float32Array(3*N) galaxy per-node RGB (built in boot.js).
  BRAIN.buildPoints = function (nodes, positions, nodeColors) {
    var n = nodes.length;
    var sizes = new Float32Array(n);
    var alphas = new Float32Array(n);
    for (var i = 0; i < n; i++) {
      sizes[i] = nodeSize(nodes[i]);
      alphas[i] = nodeAlpha(nodes[i]);
    }
    var geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geom.setAttribute('ncolor', new THREE.BufferAttribute(nodeColors, 3));
    geom.setAttribute('size', new THREE.BufferAttribute(sizes, 1));
    geom.setAttribute('palpha', new THREE.BufferAttribute(alphas, 1));
    geom.computeBoundingSphere();

    var material = new THREE.ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      transparent: true,
      // depthTest off so the node cloud floats OVER the opaque brain hull — a
      // scientist reads the data points against the page, not occluded inside a
      // silhouette. renderOrder 2 draws them after the hull (0) and web (1).
      // source: AI Architect DS envelope Spec V-01 (points over the shell).
      depthTest: false,
      depthWrite: false,
      blending: THREE.NormalBlending,
    });

    var points = new THREE.Points(geom, material);
    points.renderOrder = 2;
    points.frustumCulled = false;
    BRAIN.world.add(points);
    BRAIN.points = points;
    BRAIN.baseSizes = sizes;
    // Kept for repaintFilter() (below) — the only later consumer that needs
    // per-node kind/heat again to recompute size+alpha on a filter change.
    BRAIN.pointNodes = nodes;
    return points;
  };

  // Re-derive size + alpha for every point from the CURRENT BRAIN.filterKind
  // and re-upload both attributes — no position/geometry rebuild, same cheap
  // repaint shape as boot.js's repaintNodeColors. Called by boot.js's legend
  // click handler after it flips BRAIN.filterKind.
  BRAIN.repaintPointFilter = function () {
    if (!BRAIN.points || !BRAIN.pointNodes) return;
    var nodes = BRAIN.pointNodes;
    var sizeAttr = BRAIN.points.geometry.getAttribute('size');
    var alphaAttr = BRAIN.points.geometry.getAttribute('palpha');
    for (var i = 0; i < nodes.length; i++) {
      sizeAttr.array[i] = nodeSize(nodes[i]);
      alphaAttr.array[i] = nodeAlpha(nodes[i]);
    }
    sizeAttr.needsUpdate = true;
    alphaAttr.needsUpdate = true;
  };

  // Highlight the nodes in `rowSet` (a Set of point rows: a hovered/selected
  // node + its graph neighbours): each swells by HL_SIZE_GAIN and renders at
  // full POINT_ALPHA so the endpoints its edges lead to stand out; every other
  // node keeps its filter-derived size/alpha. `rowSet` null/empty restores the
  // plain filter state. Same cheap two-attribute re-upload as
  // repaintPointFilter; callers invoke it only on a hovered/selected CHANGE.
  var HL_SIZE_GAIN = 2.2;
  BRAIN.highlightPoints = function (rowSet) {
    if (!BRAIN.points || !BRAIN.pointNodes) return;
    if (!rowSet || rowSet.size === 0) { BRAIN.repaintPointFilter(); return; }
    var nodes = BRAIN.pointNodes;
    var sizeAttr = BRAIN.points.geometry.getAttribute('size');
    var alphaAttr = BRAIN.points.geometry.getAttribute('palpha');
    for (var i = 0; i < nodes.length; i++) {
      if (rowSet.has(i)) {
        sizeAttr.array[i] = nodeSize(nodes[i]) * HL_SIZE_GAIN;
        alphaAttr.array[i] = POINT_ALPHA;
      } else {
        sizeAttr.array[i] = nodeSize(nodes[i]);
        alphaAttr.array[i] = nodeAlpha(nodes[i]);
      }
    }
    sizeAttr.needsUpdate = true;
    alphaAttr.needsUpdate = true;
  };
})();
