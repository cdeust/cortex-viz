// Cortex Atom Memory Graph — Node Builder
// Flat data-coloured nodes: spheres for memories, octahedrons for entities.
// No glow halos, no bloom layer — colour and shape alone carry the data
// (ui/shared/README.md: "drop all glows — lift comes from hairlines, not
// bloom. A legend dot is a flat filled disc.").

(function() {

  // Shared label canvas
  function createLabel(text, color) {
    var canvas = document.createElement('canvas');
    var w = 512, h = 64;
    canvas.width = w; canvas.height = h;
    var ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);

    // Draw pipe separator + text
    ctx.font = '500 28px "JetBrains Mono", monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    // Pipe separator
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.6;
    ctx.fillText('|', 10, h / 2);
    ctx.globalAlpha = 1.0;
    ctx.fillText(text, 30, h / 2);

    var tex = new THREE.CanvasTexture(canvas);
    tex.minFilter = THREE.LinearFilter;
    var mat = new THREE.SpriteMaterial({
      map: tex, transparent: true, opacity: 0.9,
      depthWrite: false, sizeAttenuation: true,
    });
    var sprite = new THREE.Sprite(mat);
    sprite.scale.set(24, 3, 1);
    sprite.position.set(5, 4, 0);
    sprite.name = 'label';
    sprite.visible = false;
    return sprite;
  }

  function protectedHex() {
    return (window.CortexPalette && window.CortexPalette.hex('--warn-ink')) || '#b0956a';
  }

  // ─── Memory Node ─────────────────────────────────────────────
  JMD.createMemoryNode = function(m) {
    var typeColor = new THREE.Color(JMD.TYPE_COLORS[m.store_type] || JMD.TYPE_COLORS.episodic);
    var heat = m.heat || 0;
    var importance = m.importance || 0.5;
    var scale = 1.5 + importance * 1.2 + heat * 0.8;

    var group = new THREE.Group();

    // Core sphere — flat data colour, minimal emissive (just enough to
    // read against the fog at range; NOT a bloom source).
    var mat = new THREE.MeshStandardMaterial({
      color: typeColor,
      emissive: typeColor,
      emissiveIntensity: 0.12,
      metalness: 0.1, roughness: 0.55,
      transparent: true, opacity: 0.95,
    });
    var core = new THREE.Mesh(JMD.sphereGeo, mat);
    core.scale.setScalar(scale);
    group.add(core);

    // Label
    var labelText = (m.content || '').slice(0, 25).replace(/\n/g, ' ');
    if (labelText.length > 22) labelText = labelText.slice(0, 22) + '...';
    group.add(createLabel(labelText, '#' + typeColor.getHexString()));

    // Protected decision ring — flat wireframe torus in the "verified /
    // live" amber, not a glow. (McGaugh 2004)
    if (m.is_protected) {
      var ringGeo = new THREE.TorusGeometry(scale * 1.3, 0.08, 8, 32);
      var ringMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(protectedHex()), transparent: true, opacity: 0.6,
        wireframe: true,
      });
      var ring = new THREE.Mesh(ringGeo, ringMat);
      ring.rotation.x = Math.PI / 2;
      group.add(ring);
    }

    // Team/global indicator — a small flat agent-coloured marker dot
    // (Wegner 1987 TMS), not an additive glow.
    if (m.is_global && m.agent_context) {
      var agentHex = JMD.agentColor(m.agent_context);
      var dotGeo = new THREE.SphereGeometry(scale * 0.28, 8, 6);
      var dotMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(agentHex) });
      var dot = new THREE.Mesh(dotGeo, dotMat);
      dot.position.set(scale * 0.9, scale * 0.9, 0);
      group.add(dot);
    }

    group.userData = { baseScale: scale, coreMesh: core };
    return group;
  };

  // ─── Entity Node ─────────────────────────────────────────────
  JMD.createEntityNode = function(e) {
    var color = new THREE.Color(JMD.TYPE_COLORS.entity);
    var heat = e.heat || 0.5;
    var scale = 2.0 + heat * 1.5;

    var group = new THREE.Group();

    // Octahedron core — flat data colour, minimal emissive.
    var mat = new THREE.MeshStandardMaterial({
      color: color,
      emissive: color,
      emissiveIntensity: 0.15,
      metalness: 0.15, roughness: 0.45,
      transparent: true, opacity: 0.95,
    });
    var core = new THREE.Mesh(JMD.octaGeo || JMD.sphereGeo, mat);
    core.scale.setScalar(scale);
    group.add(core);

    // Label
    var labelText = (e.name || 'entity').toUpperCase().slice(0, 20);
    group.add(createLabel(labelText, '#' + color.getHexString()));

    group.userData = { baseScale: scale, coreMesh: core };
    return group;
  };
})();
