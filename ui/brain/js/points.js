// Cortex Brain View — the node cloud.
//
// Builds one THREE.Points object for the whole graph: a position buffer from
// layout.js, a per-node colour (its domain's lobe hue, so the brain reads as
// coloured territories) and a per-node size derived from the node's heat/size.
// A tiny ShaderMaterial
// draws each node as a soft round additive sprite, so ~10^5 nodes render in a
// single draw call and glow like neurons.

window.BRAIN = window.BRAIN || {};

(function () {
  var BASE_SIZE = 1.35;    // world point size for a cold node (was 1.05)
  var HEAT_GAIN = 1.8;     // extra size at heat = 1
  var HUB_KINDS = { domain: 3.4, tool_hub: 2.0, mcp: 2.4, skill: 1.8, agent: 1.8 };
  // Per-point opacity. The colored nodes LEAD the image (the scaffold + synapse
  // web are now a faint background), so a firmer alpha — still capped to avoid
  // additive white-out in the densest memory regions. source: readability pass
  // 2026-07-02 (nodes were washed out under the scaffold net).
  var POINT_ALPHA = 0.52;

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
      depthTest: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    var points = new THREE.Points(geom, material);
    points.renderOrder = 1;
    points.frustumCulled = false;
    BRAIN.world.add(points);
    BRAIN.points = points;
    BRAIN.baseSizes = sizes;
    return points;
  };
})();
