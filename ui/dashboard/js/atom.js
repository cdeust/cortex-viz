// Cortex Memory Dashboard — Atom-Shell Layout
//
// Nodes spread over concentric spherical "electron shells" around a nucleus,
// navigated in 3D. A node's shell (distance from centre) encodes its
// thermodynamic HEAT: hot/active memories occupy outer high-energy shells,
// cold/consolidated memories collapse toward the nucleus ground state.
//
// Within a shell, nodes sit on a near-uniform spherical Fibonacci lattice
// (golden-angle spiral).
// source: González, Á. (2010). "Measurement of areas on a sphere using
//   Fibonacci and latitude–longitude lattices." Mathematical Geosciences
//   42(1), 49–64. Golden angle = π(3 − √5).

(function() {
  // ── Tunable visual parameters (layout aesthetics, not derived constants) ──
  var N_SHELLS = 6;     // discrete energy levels (electron shells)
  var R0 = 45;          // nucleus-shell radius, world units
  var DR = 38;          // radius added per shell outward
  var GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // source: González (2010)

  var shellMeshes = [];

  function heatOf(node) {
    var d = node.data || {};
    var h = typeof d.heat === 'number' ? d.heat : (node.isEntity ? 0.5 : 0);
    return h < 0 ? 0 : (h > 1 ? 1 : h);
  }

  // Hot memories → outer shell (high energy); cold → nucleus (ground state).
  function shellIndex(heat) {
    var s = Math.floor(heat * N_SHELLS);
    return s >= N_SHELLS ? N_SHELLS - 1 : s;
  }

  function layoutAtomShells() {
    var nodes = JMD.allNodes;
    if (nodes.length === 0) return;

    // Bucket node indices into shells by heat.
    var shells = [];
    for (var s = 0; s < N_SHELLS; s++) shells.push([]);
    nodes.forEach(function(n, i) { shells[shellIndex(heatOf(n))].push(i); });

    // Place each shell's nodes on a spherical Fibonacci lattice.
    shells.forEach(function(members, si) {
      var radius = R0 + si * DR;
      var M = members.length;
      members.forEach(function(nodeIdx, k) {
        var y = M === 1 ? 0 : 1 - 2 * (k + 0.5) / M; // latitude in [-1, 1]
        var rxz = Math.sqrt(Math.max(0, 1 - y * y));
        var theta = k * GOLDEN_ANGLE;
        nodes[nodeIdx].group.position.set(
          Math.cos(theta) * rxz * radius,
          y * radius,
          Math.sin(theta) * rxz * radius
        );
      });
    });

    buildShellGuides(shells);
  }

  // Faint wireframe spheres mark each occupied shell — the visible "orbitals".
  function buildShellGuides(shells) {
    clearShellGuides();
    shells.forEach(function(members, si) {
      if (members.length === 0) return;
      var radius = R0 + si * DR;
      var mesh = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 24, 16),
        new THREE.MeshBasicMaterial({
          color: 0x00d2ff, wireframe: true,
          transparent: true, opacity: 0.04,
        })
      );
      JMD.scene.add(mesh);
      shellMeshes.push(mesh);
    });
  }

  function clearShellGuides() {
    shellMeshes.forEach(function(m) {
      JMD.scene.remove(m);
      m.geometry.dispose();
      m.material.dispose();
    });
    shellMeshes = [];
  }

  // Export
  JMD.layoutAtomShells = layoutAtomShells;
  JMD.clearShellGuides = clearShellGuides;
})();
