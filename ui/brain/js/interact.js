// Cortex Brain View — selection + hover.
//
// Picking a node from a 279k-point additive cloud is done in SCREEN SPACE: the
// node whose projected pixel is closest to the cursor wins (with only a slight
// front-bias to break ties between overlapping nodes). Click and hover run the
// EXACT SAME search — so the node the hover ring pins is the node a click
// selects (WYSIWYG). A previous version resolved a click with a front-most
// raycast instead: inside the brain volume that grabbed whatever surface node
// sat in front of the interior node under the cursor, selecting an unrelated
// node (user report 2026-07-08). A world-space ray cannot honour "the node I'm
// pointing at" in a dense volumetric cloud — only the screen-space projection
// can. The detail card is the galaxy's panel, opened via BRAIN.selectNode
// (detail_bridge).

window.BRAIN = window.BRAIN || {};

(function () {
  var CLICK_PX = 34;         // screen-space pick radius for a click
  var HOVER_PX = 18;         // tighter radius for the hover tooltip
  var HOVER_MS = 90;         // min gap between hover searches (≈11 Hz)

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
  // Selection/hover ring colour: the DS reserves terracotta (--accent-ink) for
  // exactly this — accent/SELECTION only, never a data category or generic
  // chrome wash (AI Architect DS gate G4). A raw white/cyan ring was chrome
  // colour with no token behind it. Both rings share the one accent colour,
  // differentiated by opacity/radius only: full accent = committed selection,
  // dim accent = hover preview of what a click will select.
  function accentHex() {
    return (window.CortexPalette && window.CortexPalette.hex('--accent-ink')) || '#8a4420';
  }
  // Selection ring (bright) + a dimmer HOVER ring so you see exactly which
  // node a click will land on before committing — the additive cloud blurs
  // individual nodes, so pre-click feedback is what makes picking feel precise.
  // Sized close to a node (BASE_SIZE ~1.35 world) so it pinpoints rather than
  // encircling a whole cluster. source: ring-too-big report 2026-07-03.
  var ring = makeRing(accentHex(), 0.95, 1.1, 1.7);
  var hoverRing = makeRing(accentHex(), 0.6, 0.85, 1.35);

  // Three.js bakes the ring colour, so a surface toggle (paper <-> ink) needs
  // an explicit re-read + re-tint, same pattern as scene.js/brain_mesh.js.
  window.addEventListener('cortex:surface-change', function () {
    var c = accentHex();
    ring.material.color.set(c);
    hoverRing.material.color.set(c);
  });

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

  BRAIN.initPicking = function (nodes) {
    BRAIN.scene.add(ring);
    BRAIN.scene.add(hoverRing);
    var dom = BRAIN.renderer.domElement;
    var tip = document.getElementById('brain-tip');
    var down = null;
    var lastHover = 0;
    var pickIdx = -1;     // node the hover ring currently pins == what a click commits
    var hoverRow = -1;    // node currently under the cursor (-1 = none)
    var selectedRow = -1; // node locked by a click; its highlight persists until deselect
    var shownRow = -1;    // row whose highlight is currently painted (skips redundant repaints)

    // Show the HOVERED node's associations while hovering; otherwise fall back
    // to the SELECTED node's (so a committed selection keeps its edges + neighbour
    // nodes lit until the user clicks away or closes the detail panel — both emit
    // graph:deselectNode). Repaints only when the effective row changes.
    function applyHighlight() {
      var target = hoverRow >= 0 ? hoverRow : selectedRow;
      if (target === shownRow) return;
      if (BRAIN.highlightNode) BRAIN.highlightNode(target);
      shownRow = target;
    }

    // Pin the hover ring + tooltip on `idx` (or hide when idx < 0), and record
    // it as the node a click will select. Shared by the throttled hover and the
    // pointerdown refresh so the LABEL, the RING, and the eventual SELECTION are
    // always the same node — the passive hover is throttled and can lag the
    // cursor in the dense core, which made a click look like it grabbed an
    // unrelated node (user report 2026-07-08).
    function pinHover(idx, clientX, clientY) {
      pickIdx = idx;
      // Light up the hovered node's associations (edges + neighbour nodes).
      hoverRow = idx;
      applyHighlight();
      if (idx < 0) {
        dom.style.cursor = '';
        hoverRing.visible = false;
        if (tip) tip.style.display = 'none';
        return;
      }
      dom.style.cursor = 'pointer';
      showHoverRing(worldOf(idx));
      if (tip) {
        var n = nodes[idx];
        var kind = n.kind || n.type || '';
        tip.textContent = (kind ? kind + ' · ' : '') + (n.label || n.id || '').slice(0, 80);
        tip.style.left = (clientX + 14) + 'px';
        tip.style.top = (clientY + 14) + 'px';
        tip.style.display = 'block';
      }
    }

    dom.addEventListener('pointerdown', function (e) {
      down = { x: e.clientX, y: e.clientY };
      // Recompute the pick at the EXACT press point, unthrottled and with the
      // forgiving CLICK_PX radius, and snap the ring/tooltip to it — so the
      // release commits precisely the node shown, not a throttle-stale one.
      pinHover(nearestToCursor(e.clientX, e.clientY, CLICK_PX), e.clientX, e.clientY);
    });

    dom.addEventListener('pointerup', function (e) {
      // Only a click if the pointer barely moved (else it was an orbit drag).
      if (!down || Math.abs(e.clientX - down.x) > 4 || Math.abs(e.clientY - down.y) > 4) return;
      if (pickIdx < 0) {
        ring.visible = false;
        if (window.JUG && JUG.deselectNode) JUG.deselectNode();
        return;
      }
      // Ring placement is handled by the graph:selectNode listener below, so a
      // node selected via a connection link gets the same highlight.
      BRAIN.selectNode(nodes[pickIdx]);   // exactly the node the ring pinned at press
    });

    // Hover: throttled label tooltip + pointer cursor. Skipped while dragging.
    dom.addEventListener('pointermove', function (e) {
      if (e.buttons) { if (tip) tip.style.display = 'none'; return; }
      var now = (window.performance && performance.now) ? performance.now() : Date.now();
      if (now - lastHover < HOVER_MS) return;
      lastHover = now;
      pinHover(nearestToCursor(e.clientX, e.clientY, HOVER_PX), e.clientX, e.clientY);
    });

    dom.addEventListener('pointerleave', function () {
      hoverRing.visible = false;
      if (tip) tip.style.display = 'none';
      hoverRow = -1;
      applyHighlight();   // fall back to the selected node's highlight, or clear
    });

    if (window.JUG && JUG.on) {
      // Highlight whatever node becomes selected — by direct click here, or by
      // clicking a connection link in the detail panel (which selects by id).
      JUG.on('graph:selectNode', function (node) {
        var i = BRAIN.indexOfId ? BRAIN.indexOfId.get(node.id) : null;
        if (i == null) { ring.visible = false; return; }
        showRing(worldOf(i));
        selectedRow = i;   // lock the highlight on the selection until deselect
        applyHighlight();
      });
      JUG.on('graph:deselectNode', function () {
        ring.visible = false;
        selectedRow = -1;
        applyHighlight();
      });
    }
  };
})();
