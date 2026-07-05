// Cortex Atom Memory Graph — Three.js Scene
// Scene, camera, renderer. No bloom/vignette/grain — lift comes from
// hairlines and flat data colour only (ui/shared/README.md doctrine:
// "ambient grain was deliberately removed... don't add it back").

(function() {
  // Initial size — will be corrected by resizeToContainer() once DOM is ready
  var W = 800;
  var H = 600;

  function bgHex() {
    return (window.CortexPalette && window.CortexPalette.hex('--canvas')) || '#f2ede4';
  }

  // Scene
  var scene = new THREE.Scene();
  scene.background = new THREE.Color(bgHex());
  scene.fog = new THREE.FogExp2(bgHex(), 0.00025);

  // Camera
  var camera = new THREE.PerspectiveCamera(60, W / H, 1, 5000);
  camera.position.set(0, 0, 400);

  // Renderer
  var renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(W, H);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.domElement.id = 'three-canvas';

  // Insert canvas into graph container
  function insertCanvas() {
    var container = document.getElementById('graph-container');
    if (container) {
      container.appendChild(renderer.domElement);
      // Now that it's in the DOM, resize to actual container
      resizeToContainer();
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', insertCanvas);
  } else {
    // DOM ready but layout might not be — use rAF to ensure CSS has applied
    requestAnimationFrame(function() { insertCanvas(); });
  }

  // ─── Resize to actual container dimensions ──────────────────
  function resizeToContainer() {
    var container = document.getElementById('graph-container');
    if (!container) return;
    var rect = container.getBoundingClientRect();
    W = Math.max(rect.width, 100);
    H = Math.max(rect.height, 100);
    camera.aspect = W / H;
    camera.updateProjectionMatrix();
    renderer.setSize(W, H);
  }

  // Controls
  var controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.rotateSpeed = 0.4;
  controls.zoomSpeed = 0.8;
  controls.minDistance = 50;
  controls.maxDistance = 2000;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.3;

  // Lights — neutral chrome, never tinted with data hues (colour only
  // from data, per shared/README.md). One neutral ambient + one neutral
  // directional key light.
  scene.add(new THREE.AmbientLight(0xffffff, 1.4));
  var key = new THREE.DirectionalLight(0xffffff, 0.8);
  key.position.set(100, 300, 200);
  scene.add(key);

  // Re-tint background/fog/lights when the surface posture changes.
  window.addEventListener('cortex:surface-change', function() {
    var c = new THREE.Color(bgHex());
    scene.background = c;
    scene.fog.color = c;
  });

  // Node groups
  var nodeGroup = new THREE.Group();
  scene.add(nodeGroup);

  // Resize on window resize
  window.addEventListener('resize', resizeToContainer);

  // Shared geometries
  var sphereGeo = new THREE.IcosahedronGeometry(1, 2);
  var octaGeo = new THREE.OctahedronGeometry(1, 0);

  // Highlight mesh — a quiet wireframe ring in the chrome accent (the ONE
  // colour chrome may use, as a stamp — never as data). Not a glow: flat
  // wireframe, no additive blending.
  var hlGeo = new THREE.IcosahedronGeometry(1, 2);
  var hlMat = new THREE.MeshBasicMaterial({
    color: parseInt((JMD.EDGE_COLORS_HEX.highlight || '#8a5a45').replace('#', ''), 16),
    transparent: true, opacity: 0.35, wireframe: true,
  });
  var highlightMesh = new THREE.Mesh(hlGeo, hlMat);
  highlightMesh.visible = false;
  scene.add(highlightMesh);

  // Export
  JMD.scene = scene;
  JMD.camera = camera;
  JMD.renderer = renderer;
  JMD.controls = controls;
  JMD.nodeGroup = nodeGroup;
  JMD.sphereGeo = sphereGeo;
  JMD.octaGeo = octaGeo;
  JMD.highlightMesh = highlightMesh;
  JMD.resizeToContainer = resizeToContainer;
})();
