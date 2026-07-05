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

  var VERT = [
    'attribute float size;',
    'attribute vec3 ncolor;',
    'varying vec3 vColor;',
    'void main() {',
    '  vColor = ncolor;',
    '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
    '  gl_PointSize = size * (300.0 / -mv.z);',
    '  gl_Position = projectionMatrix * mv;',
    '}',
  ].join('\n');

  var FRAG = [
    'uniform float alpha;',
    'varying vec3 vColor;',
    'void main() {',
    '  vec2 d = gl_PointCoord - vec2(0.5);',
    '  float r = length(d);',
    '  if (r > 0.5) discard;',
    '  float a = smoothstep(0.5, 0.0, r) * alpha;',
    '  gl_FragColor = vec4(vColor, a);',
    '}',
  ].join('\n');

  function nodeSize(n) {
    var hub = HUB_KINDS[n.kind] || 0;
    var heat = typeof n.heat === 'number' ? Math.min(Math.max(n.heat, 0), 1) : 0;
    return BASE_SIZE + hub + heat * HEAT_GAIN;
  }

  // nodes: array of graph nodes; positions: Float32Array(3*N) from layout.js;
  // nodeColors: Float32Array(3*N) galaxy per-node RGB (built in boot.js).
  BRAIN.buildPoints = function (nodes, positions, nodeColors) {
    var n = nodes.length;
    var sizes = new Float32Array(n);
    for (var i = 0; i < n; i++) sizes[i] = nodeSize(nodes[i]);
    var geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geom.setAttribute('ncolor', new THREE.BufferAttribute(nodeColors, 3));
    geom.setAttribute('size', new THREE.BufferAttribute(sizes, 1));
    geom.computeBoundingSphere();

    var material = new THREE.ShaderMaterial({
      uniforms: { alpha: { value: POINT_ALPHA } },
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
    return points;
  };
})();
