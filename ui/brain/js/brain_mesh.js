// Cortex Brain View — anatomical mesh loader.
//
// Loads the vendored CC-BY brain GLB (ui/brain/models/brain.glb), recentres
// and rescales it to a fixed world radius, swaps every surface to one
// discreet ink-shell material so the node cloud + synapse web read as the
// brain's real content while the cortex still gives them a legible form, and
// extracts the triangle soup (positions + cumulative area) that layout.js
// samples to place nodes inside the cortex. All triangle coordinates are
// returned in the SAME normalized world space the displayed mesh lives in,
// so sampled points land on/inside the visible surface.

window.BRAIN = window.BRAIN || {};

(function () {
  // The brain shell is drawn like anatomy in a textbook: a clean SINGLE-LAYER
  // silhouette with a PENCIL CONTOUR — never a translucent grey volume.
  //
  // Root cause of the old "too dark" mass: the anatomical mesh has thousands of
  // gyral folds. Any TRANSLUCENT material lets every fold behind the front
  // surface blend through, stacking into grey scribble on the cream page —
  // lowering opacity or adding fresnel does NOT fix fold stacking. The DS
  // (cards/signature-envelope.html: "contour + wash — never a mass") therefore
  // FORBIDS alpha on folded geometry.
  //
  // Fix: render the shell OPAQUE (transparent:false, depthWrite:true). The depth
  // buffer keeps only the NEAREST surface per pixel, so folds can never stack —
  // one clean silhouette (ONE flat opaque hull layer, gate G8). The interior
  // fill reads the DS token --mesh-tint directly (already the page with a
  // measured wash of ink mixed in), and the grazing-angle silhouette lifts to
  // a soft graphite --mesh-line rim (fresnel COLOUR shift, not an alpha shift).
  // All data (nodes + synapse web) renders OVER this hull via depthTest:false,
  // so points read against the page like a textbook plate.
  // source: ui/shared/tokens/surfaces.css --mesh-tint / --mesh-line (per surface);
  //         cards/signature-envelope.html (Spec V-01, Data-viz envelopes)

  // The mesh tokens carry a baked alpha (oklch(... / A)); CortexPalette.hex()
  // composites over black and drops alpha, so it cannot read them faithfully.
  // Read the raw token instead: split the opaque colour (for oklch→sRGB via a
  // 1×1 canvas) from the alpha (drives the shader directly).
  var _probe = document.createElement('canvas');
  _probe.width = _probe.height = 1;
  function _opaqueRGB(cssColor) {
    var x = _probe.getContext('2d');
    x.clearRect(0, 0, 1, 1);
    x.fillStyle = '#000';
    try { x.fillStyle = cssColor; } catch (e) { /* keep #000 */ }
    x.fillRect(0, 0, 1, 1);
    var d = x.getImageData(0, 0, 1, 1).data;
    return [d[0] / 255, d[1] / 255, d[2] / 255];
  }
  function meshToken(name, fallbackRGB, fallbackA) {
    var raw = (window.CortexPalette && window.CortexPalette.readVar(name)) || '';
    if (!raw) return { rgb: fallbackRGB, a: fallbackA };
    var a = fallbackA;
    var m = raw.match(/\/\s*([0-9.]+)\s*\)/); // "… / 0.45)"
    if (m) a = parseFloat(m[1]);
    var opaque = raw.replace(/\/\s*[0-9.]+\s*\)/, ')'); // strip the alpha slash
    return { rgb: _opaqueRGB(opaque), a: a };
  }

  var SHELL_VERT = [
    'varying vec3 vN;',
    'varying vec3 vView;',
    'void main() {',
    '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
    '  vN = normalize(normalMatrix * normal);',
    '  vView = normalize(-mv.xyz);',
    '  gl_Position = projectionMatrix * mv;',
    '}',
  ].join('\n');

  var SHELL_FRAG = [
    'uniform vec3 uTint;',       // opaque interior: page + one --mesh-fill wash
    'uniform vec3 uLine;',       // opaque pencil rim: page + --mesh-line at its α
    'uniform float uPow;',
    'varying vec3 vN;',
    'varying vec3 vView;',
    'void main() {',
    // abs(dot): DoubleSide, so back faces get the same fresnel as front. The
    // shell is OPAQUE — the rim is a COLOUR shift (tint -> line), not an alpha
    // fade, so no fold ever blends through. source: DS envelope Spec V-01.
    '  float f = pow(1.0 - abs(dot(normalize(vN), normalize(vView))), uPow);',
    '  gl_FragColor = vec4(mix(uTint, uLine, f), 1.0);',
    '}',
  ].join('\n');

  // Opaque page colour under the shell — the DS wash and rim are composited over
  // this, so a palette-load failure still yields a cream fallback, not black.
  function canvasRGB() {
    var hex = (window.CortexPalette && window.CortexPalette.hex('--canvas')) || '#f2efe9';
    return _opaqueRGB(hex);
  }
  function mixRGB(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }

  function shellUniforms() {
    // Opaque interior: read --mesh-tint DIRECTLY (surfaces.css already bakes it
    // as `color-mix(in oklab, canvas 96%, ink 4%)`) instead of re-deriving an
    // approximation from --mesh-fill + canvas — a naive sRGB channel blend here
    // is NOT colorimetrically identical to the token's oklab mix, so reading
    // the token avoids silent drift from the DS's authored value. Falls back to
    // the --mesh-fill-over-canvas derivation (still token-sourced, no hex table)
    // only if the token or CortexPalette itself failed to load.
    var page = canvasRGB();
    var tintHex = window.CortexPalette && window.CortexPalette.hex('--mesh-tint');
    var fill = meshToken('--mesh-fill', [0.34, 0.32, 0.28], 0.04);
    var tint = tintHex ? _opaqueRGB(tintHex) : mixRGB(page, fill.rgb, fill.a);
    // Opaque pencil rim: --mesh-line at its own α (≈45% on paper) baked over the
    // tint, so the silhouette keeps the soft graphite pencil weight the DS
    // prescribes without needing a transparent material. source: surfaces.css.
    var line = meshToken('--mesh-line', [0.34, 0.31, 0.27], 0.45);
    var rim = mixRGB(tint, line.rgb, line.a);
    return {
      uTint: { value: new THREE.Vector3(tint[0], tint[1], tint[2]) },
      uLine: { value: new THREE.Vector3(rim[0], rim[1], rim[2]) },
      // higher power = thinner, crisper contour; 2.5 keeps a soft pencil edge.
      uPow: { value: 2.5 },
    };
  }

  function makeShellMaterial() {
    // OPAQUE + depthWrite: the z-buffer keeps only the nearest surface, so the
    // mesh's gyral folds cannot stack into a grey mass. Data (nodes + edges)
    // renders over the hull via its own depthTest:false. source: DS Spec V-01.
    return new THREE.ShaderMaterial({
      uniforms: shellUniforms(),
      vertexShader: SHELL_VERT,
      fragmentShader: SHELL_FRAG,
      transparent: false,
      depthWrite: true,
      depthTest: true,
      side: THREE.DoubleSide,
    });
  }

  // Pull every triangle out of the loaded scene in world space, building a
  // flat Float32Array (9 floats/triangle) plus a cumulative-area table for
  // O(log n) area-weighted sampling.
  function extractTriangles(root) {
    var tris = [];
    var a = new THREE.Vector3();
    var b = new THREE.Vector3();
    var c = new THREE.Vector3();
    root.updateWorldMatrix(true, true);
    root.traverse(function (obj) {
      if (!obj.isMesh || !obj.geometry) return;
      var geom = obj.geometry;
      var pos = geom.attributes.position;
      if (!pos) return;
      var mw = obj.matrixWorld;
      var index = geom.index;
      var triCount = index ? index.count / 3 : pos.count / 3;
      for (var t = 0; t < triCount; t++) {
        var i0 = index ? index.getX(t * 3) : t * 3;
        var i1 = index ? index.getX(t * 3 + 1) : t * 3 + 1;
        var i2 = index ? index.getX(t * 3 + 2) : t * 3 + 2;
        a.fromBufferAttribute(pos, i0).applyMatrix4(mw);
        b.fromBufferAttribute(pos, i1).applyMatrix4(mw);
        c.fromBufferAttribute(pos, i2).applyMatrix4(mw);
        tris.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
      }
    });
    var verts = new Float32Array(tris);
    var n = verts.length / 9;
    var cum = new Float32Array(n);
    var ab = new THREE.Vector3();
    var ac = new THREE.Vector3();
    var total = 0;
    for (var k = 0; k < n; k++) {
      var o = k * 9;
      ab.set(verts[o + 3] - verts[o], verts[o + 4] - verts[o + 1], verts[o + 5] - verts[o + 2]);
      ac.set(verts[o + 6] - verts[o], verts[o + 7] - verts[o + 1], verts[o + 8] - verts[o + 2]);
      total += 0.5 * ab.cross(ac).length();
      cum[k] = total;
    }
    return { verts: verts, cum: cum, totalArea: total };
  }

  // Recentre + uniformly scale the loaded scene so its longest axis spans
  // 2 * TARGET_RADIUS and its centre sits at the origin.
  function normalize(root) {
    root.updateWorldMatrix(true, true);
    var box = new THREE.Box3().setFromObject(root);
    var size = box.getSize(new THREE.Vector3());
    var center = box.getCenter(new THREE.Vector3());
    var maxDim = Math.max(size.x, size.y, size.z) || 1;
    var scale = (2 * BRAIN.TARGET_RADIUS) / maxDim;
    root.scale.setScalar(scale);
    root.position.set(-center.x * scale, -center.y * scale, -center.z * scale);
    root.updateWorldMatrix(true, true);
  }

  // Re-ink the shell on a surface toggle — Three.js bakes colour so it cannot
  // react to the CSS custom-property change itself. Re-read both envelope
  // tokens (colour + alpha flip between the ink and paper postures) and push
  // them into every shell material's uniforms.
  var shellMaterials = [];
  window.addEventListener('cortex:surface-change', function () {
    var u = shellUniforms();
    for (var i = 0; i < shellMaterials.length; i++) {
      var mu = shellMaterials[i].uniforms;
      mu.uTint.value.copy(u.uTint.value);
      mu.uLine.value.copy(u.uLine.value);
    }
  });

  BRAIN.loadBrain = function (url) {
    return new Promise(function (resolve, reject) {
      var loader = new THREE.GLTFLoader();
      loader.load(
        url,
        function (gltf) {
          var root = gltf.scene;
          root.traverse(function (obj) {
            if (obj.isMesh) {
              obj.material = makeShellMaterial();
              shellMaterials.push(obj.material);
            }
          });
          normalize(root);
          var soup = extractTriangles(root);
          root.renderOrder = 0;
          BRAIN.world.add(root);
          // World bounding box (post-normalize) — the anatomical atlas resolves
          // its fractional region centres against this box's centre + extents.
          var box = new THREE.Box3().setFromObject(root);
          resolve({
            mesh: root,
            triangles: soup.verts,
            cumAreas: soup.cum,
            totalArea: soup.totalArea,
            centroid: new THREE.Vector3(0, 0, 0),
            box: box,
          });
        },
        undefined,
        function (err) { reject(err); }
      );
    });
  };
})();
