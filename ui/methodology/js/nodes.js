// Cortex Methodology Map — Nodes
window.CMV = window.CMV || {};

/**
 * Create a flat, filled data-marker sphere — no glow/bloom shell (design
 * system doctrine: "a legend dot is a flat filled disc").
 * @param {string} color - Hex color string (e.g. '#3D8FA6').
 * @param {number} size - Base size for the sphere.
 * @returns {THREE.Group} Group containing the marker mesh.
 */
CMV.createGlowSphere = function (color, size) {
  var hex = parseInt(color.replace('#', ''), 16);

  var marker = new THREE.Mesh(
    new THREE.SphereGeometry(size * 0.55, 16, 16),
    new THREE.MeshBasicMaterial({ color: hex })
  );

  var group = new THREE.Group();
  group.add(marker);
  return group;
};
