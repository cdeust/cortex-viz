// Cortex Brain View — Three.js scene scaffold.
//
// Owns the renderer, camera, controls, lights and the per-frame loop.
// Everything visual is added to BRAIN.world (a Group at the origin) so the
// brain mesh and the node cloud share one transform and orbit together.
// No post-processing: the brand doctrine is "no black backgrounds, data
// views included" (AI Architect Design System README §3) — the canvas is the
// paper record, not a glow-lit dark instrument, so there is no bloom/vignette/
// film-grain pass, and the dependency surface stays three core + OrbitControls
// + GLTFLoader.

window.BRAIN = window.BRAIN || {};

(function () {
  var TARGET_RADIUS = 80;      // normalized brain radius in world units
  var CAMERA_DISTANCE = 2.3;   // multiples of TARGET_RADIUS for the initial pull-back
  var FOG_DENSITY = 0.0016;

  var container = document.getElementById('view');

  // Surface-correct background: CortexPalette.hex('--canvas') resolves the
  // design-system token LIVE on the current surface (cream --paper-0 by
  // default; the warm-neutral --ink-0 only in the opt-in legacy instrument
  // mode) — never a hardcoded near-black hex. Falls back to the paper cream
  // if /shared/palette.js failed to load, so the scene never boots to black.
  function canvasHex() {
    return (window.CortexPalette && window.CortexPalette.hex('--canvas')) || '#f2efe9';
  }

  var scene = new THREE.Scene();
  scene.background = new THREE.Color(canvasHex());
  scene.fog = new THREE.FogExp2(canvasHex(), FOG_DENSITY);

  var camera = new THREE.PerspectiveCamera(
    55, window.innerWidth / window.innerHeight, 0.1, 4000
  );
  camera.position.set(0, 0, TARGET_RADIUS * CAMERA_DISTANCE);

  var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  container.appendChild(renderer.domElement);

  var controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.rotateSpeed = 0.5;
  controls.minDistance = TARGET_RADIUS * 0.4;
  controls.maxDistance = TARGET_RADIUS * 8;

  // Neutral, warm-white lighting — a surface to read the ink mesh and data
  // points by, not a coloured glow. Flat ambient fill + one soft directional
  // key light; no saturated rim light (the old cyan-key/pink-rim pair read
  // as a lit-from-neon-signage instrument, wrong for a paper record).
  scene.add(new THREE.AmbientLight(0xffffff, 1.0));
  var key = new THREE.DirectionalLight(0xfff4e6, 0.55);
  key.position.set(1, 1, 1);
  scene.add(key);

  // Re-paint the canvas + fog when the surface toggles (paper <-> ink) —
  // Three.js bakes colour, it cannot read the CSS custom property change.
  window.addEventListener('cortex:surface-change', function () {
    var hex = canvasHex();
    scene.background.set(hex);
    scene.fog.color.set(hex);
  });

  var world = new THREE.Group();
  scene.add(world);

  var frameCbs = [];
  function onFrame(cb) { frameCbs.push(cb); }

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    for (var i = 0; i < frameCbs.length; i++) frameCbs[i]();
    renderer.render(scene, camera);
  }
  animate();

  window.addEventListener('resize', function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // Frame the whole brain from a 3/4 vantage. A dead-front view (the old
  // reset) collapses the brain to a flat disc where lobes overlap and depth
  // is unreadable — which made "reset" feel like it did nothing. This pulls
  // back to a raised anterolateral 3/4 angle so the hemispheres, temporal
  // lobes and frontal pole separate in depth. source: standard 3/4 anatomical
  // presentation angle (yaw ~35°, pitch ~18°), tuned 2026-07-02.
  function fitView() {
    focusAnim = null;
    controls.target.set(0, 0, 0);
    var d = TARGET_RADIUS * CAMERA_DISTANCE;
    var yaw = 0.62;    // ~35° azimuth
    var pitch = 0.32;  // ~18° elevation
    camera.position.set(
      d * Math.cos(pitch) * Math.sin(yaw),
      d * Math.sin(pitch),
      d * Math.cos(pitch) * Math.cos(yaw)
    );
    controls.update();
  }

  // Smoothly recentre the view on a world point (used when navigating to a
  // node via a connection link or a deep pick). Keeps the current viewing
  // distance/angle and just pans the orbit pivot there, optionally dollying in
  // when the requested distance is closer than where we are — so a node on the
  // far side of the cortex comes to the centre instead of staying unreachable.
  var focusAnim = null;
  var FOCUS_MS = 520;  // source: feels-instant-but-trackable pan, tuned 2026-06-30
  function nowMs() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function focusOn(target, distance) {
    var offset = camera.position.clone().sub(controls.target);
    var curDist = offset.length() || 1;
    var dist = distance && distance < curDist ? distance : curDist;
    var camTo = target.clone().add(offset.multiplyScalar(dist / curDist));
    focusAnim = {
      t0: nowMs(),
      fromT: controls.target.clone(), toT: target.clone(),
      fromC: camera.position.clone(), toC: camTo,
    };
  }
  onFrame(function () {
    if (!focusAnim) return;
    var p = Math.min(1, (nowMs() - focusAnim.t0) / FOCUS_MS);
    var e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;  // easeInOutQuad
    controls.target.lerpVectors(focusAnim.fromT, focusAnim.toT, e);
    camera.position.lerpVectors(focusAnim.fromC, focusAnim.toC, e);
    controls.update();
    if (p >= 1) focusAnim = null;
  });

  BRAIN.TARGET_RADIUS = TARGET_RADIUS;
  BRAIN.scene = scene;
  BRAIN.camera = camera;
  BRAIN.renderer = renderer;
  BRAIN.controls = controls;
  BRAIN.world = world;
  BRAIN.onFrame = onFrame;
  BRAIN.fitView = fitView;
  BRAIN.focusOn = focusOn;
})();
