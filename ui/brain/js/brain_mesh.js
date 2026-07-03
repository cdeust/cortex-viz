// Cortex Brain View — anatomical mesh loader.
//
// Loads the vendored CC-BY brain GLB (ui/brain/models/brain.glb), recentres
// and rescales it to a fixed world radius, swaps every surface to one
// translucent material so the node cloud shows through, and extracts the
// triangle soup (positions + cumulative area) that layout.js samples to
// place nodes inside the cortex. All triangle coordinates are returned in
// the SAME normalized world space the displayed mesh lives in, so sampled
// points land on/inside the visible surface.

window.BRAIN = window.BRAIN || {};

(function () {
  function makeShellMaterial() {
    // Faint containing ghost: the glowing edge web (edges.js) now defines the
    // brain's form, so the anatomical surface is dimmed way down to a barely
    // there membrane. depthWrite off so it never hides the web or nodes.
    return new THREE.MeshStandardMaterial({
      color: 0x9fb4d8,
      roughness: 0.55,
      metalness: 0.0,
      transparent: true,
      opacity: 0.03,
      side: THREE.DoubleSide,
      depthWrite: false,
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

  BRAIN.loadBrain = function (url) {
    return new Promise(function (resolve, reject) {
      var loader = new THREE.GLTFLoader();
      loader.load(
        url,
        function (gltf) {
          var root = gltf.scene;
          root.traverse(function (obj) {
            if (obj.isMesh) obj.material = makeShellMaterial();
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
