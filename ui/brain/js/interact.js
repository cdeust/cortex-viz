// Cortex Brain View — selection + hover.
//
// Picking a node from a 279k-point additive cloud has to be forgiving: a thin
// ray rarely lands exactly on a 1px sprite. So a click first tries a wide
// raycast (front-most hit wins), then falls back to a screen-space nearest
// search — the node whose projected pixel is closest to the cursor (and, among
// near ones, closest to the camera). Hover runs the same search, throttled, to
// show a label tooltip so you can see what you're about to select. The detail
// card itself is the galaxy's panel, opened via BRAIN.selectNode (detail_bridge).

window.BRAIN = window.BRAIN || {};

(function () {
  var RAY_THRESHOLD = 3.0;   // world-space ray proximity for a direct point hit
  var CLICK_PX = 34;         // screen-space fallback radius for a click
  var HOVER_PX = 18;         // tighter radius for the hover tooltip
  var HOVER_MS = 90;         // min gap between hover searches (≈11 Hz)

  var raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = RAY_THRESHOLD;
  var pointer = new THREE.Vector2();
  var tmp = new THREE.Vector3();

  function makeRing(color, opacity, ri, ro) {
    var m = new THREE.Mesh(
      new THREE.RingGeometry(ri, ro, 32),
      new THREE.MeshBasicMaterial({ color: color, side: THREE.DoubleSide, transparent: true, opacity: opacity })
    );
    m.visible = false;
    m.renderOrder = 3;
    return m;
  }
  // Selection ring (bright) + a dimmer HOVER ring so you see exactly which
  // node a click will land on before committing — the additive cloud blurs
  // individual nodes, so pre-click feedback is what makes picking feel precise.
  // Sized close to a node (BASE_SIZE ~1.35 world) so it pinpoints rather than
  // encircling a whole cluster. source: ring-too-big report 2026-07-03.
  var ring = makeRing(0xffffff, 0.95, 1.1, 1.7);
  var hoverRing = makeRing(0x9fe8ff, 0.6, 0.85, 1.35);

  // Project every node to screen pixels; return the index whose projected
  // pixel is NEAREST the cursor (what you're actually pointing at), with only
  // a slight front-bias to break ties between overlapping nodes — so a click
  // lands on the node under the crosshair, not merely the front-most blob in
  // the neighbourhood. One pass over the position buffer; throttled for hover.
  //   source: precision-picking pass 2026-07-03 (front-most heuristic grabbed
  //   the wrong node in dense plumes — user report).
  function nearestToCursor(cx, cy, maxPx) {
    var pts = BRAIN.points;
    if (!pts) return -1;
    pts.updateWorldMatrix(true, false);
    var mw = pts.matrixWorld;
    var cam = BRAIN.camera;
    var pos = pts.geometry.attributes.position;
    var W = window.innerWidth, H = window.innerHeight;
    var maxPx2 = maxPx * maxPx;
    var best = -1, bestScore = Infinity;
    for (var i = 0; i < pos.count; i++) {
      tmp.fromBufferAttribute(pos, i).applyMatrix4(mw).project(cam);
      if (tmp.z < -1 || tmp.z > 1) continue;             // behind camera / clipped
      var px = (tmp.x * 0.5 + 0.5) * W - cx;
      var py = (-tmp.y * 0.5 + 0.5) * H - cy;
      var d2 = px * px + py * py;
      if (d2 > maxPx2) continue;
      // Pixel distance dominates; depth (tmp.z in [-1,1]) adds a small
      // front-bias tie-break (~1.5px-equivalent) so among near-equal hits the
      // visible/front node wins without overriding a clearly-nearer node.
      var score = d2 + (tmp.z + 1) * 12;
      if (score < bestScore) { bestScore = score; best = i; }
    }
    return best;
  }

  function worldOf(index) {
    var pos = BRAIN.points.geometry.attributes.position;
    return tmp.fromBufferAttribute(pos, index).applyMatrix4(BRAIN.points.matrixWorld).clone();
  }

  function showRing(worldPos) {
    ring.position.copy(worldPos);
    ring.lookAt(BRAIN.camera.position);
    ring.visible = true;
  }

  function showHoverRing(worldPos) {
    hoverRing.position.copy(worldPos);
    hoverRing.lookAt(BRAIN.camera.position);
    hoverRing.visible = true;
  }

  function pickAt(clientX, clientY, fallbackPx) {
    pointer.x = (clientX / window.innerWidth) * 2 - 1;
    pointer.y = -(clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(pointer, BRAIN.camera);
    var hits = raycaster.intersectObject(BRAIN.points, false);
    if (hits.length) return { index: hits[0].index, world: hits[0].point.clone() };
    var idx = nearestToCursor(clientX, clientY, fallbackPx);
    return idx >= 0 ? { index: idx, world: worldOf(idx) } : null;
  }

  BRAIN.initPicking = function (nodes) {
    BRAIN.scene.add(ring);
    BRAIN.scene.add(hoverRing);
    var dom = BRAIN.renderer.domElement;
    var tip = document.getElementById('brain-tip');
    var down = null;
    var lastHover = 0;

    dom.addEventListener('pointerdown', function (e) { down = { x: e.clientX, y: e.clientY }; });

    dom.addEventListener('pointerup', function (e) {
      // Only a click if the pointer barely moved (else it was an orbit drag).
      if (!down || Math.abs(e.clientX - down.x) > 4 || Math.abs(e.clientY - down.y) > 4) return;
      var hit = pickAt(e.clientX, e.clientY, CLICK_PX);
      if (!hit) {
        ring.visible = false;
        if (window.JUG && JUG.deselectNode) JUG.deselectNode();
        return;
      }
      // Ring placement is handled by the graph:selectNode listener below, so
      // a node selected via a connection link gets the same highlight.
      BRAIN.selectNode(nodes[hit.index]);
    });

    // Hover: throttled label tooltip + pointer cursor. Skipped while dragging.
    dom.addEventListener('pointermove', function (e) {
      if (e.buttons) { if (tip) tip.style.display = 'none'; return; }
      var now = (window.performance && performance.now) ? performance.now() : Date.now();
      if (now - lastHover < HOVER_MS) return;
      lastHover = now;
      var idx = nearestToCursor(e.clientX, e.clientY, HOVER_PX);
      if (idx < 0) {
        dom.style.cursor = '';
        hoverRing.visible = false;
        if (tip) tip.style.display = 'none';
        return;
      }
      dom.style.cursor = 'pointer';
      showHoverRing(worldOf(idx));   // pin the exact node a click will select
      if (tip) {
        var n = nodes[idx];
        var kind = n.kind || n.type || '';
        tip.textContent = (kind ? kind + ' · ' : '') + (n.label || n.id || '').slice(0, 80);
        tip.style.left = (e.clientX + 14) + 'px';
        tip.style.top = (e.clientY + 14) + 'px';
        tip.style.display = 'block';
      }
    });

    dom.addEventListener('pointerleave', function () {
      hoverRing.visible = false;
      if (tip) tip.style.display = 'none';
    });

    if (window.JUG && JUG.on) {
      // Highlight whatever node becomes selected — by direct click here, or by
      // clicking a connection link in the detail panel (which selects by id).
      JUG.on('graph:selectNode', function (node) {
        var i = BRAIN.indexOfId ? BRAIN.indexOfId.get(node.id) : null;
        if (i == null) { ring.visible = false; return; }
        showRing(worldOf(i));
      });
      JUG.on('graph:deselectNode', function () { ring.visible = false; });
    }
  };
})();
