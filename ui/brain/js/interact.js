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

  // Mutable picking state, held in one object so the extracted helpers below
  // (which used to be closures inside initPicking) can share it by reference
  // without widening their own parameter lists past §4.4.
  function createPickState() {
    return {
      down: null,        // pointerdown origin, for click-vs-drag detection
      lastHover: 0,       // throttle clock for hover searches
      pickIdx: -1,        // node the hover ring currently pins == what a click commits
      hoverRow: -1,        // node currently under the cursor (-1 = none)
      selectedRow: -1,     // node locked by a click; its highlight persists until deselect
      shownRow: -1         // row whose highlight is currently painted (skips redundant repaints)
    };
  }

  // Show the HOVERED node's associations while hovering; otherwise fall back
  // to the SELECTED node's (so a committed selection keeps its edges + neighbour
  // nodes lit until the user clicks away or closes the detail panel — both emit
  // graph:deselectNode). Repaints only when the effective row changes.
  function applyHighlight(state) {
    var target = state.hoverRow >= 0 ? state.hoverRow : state.selectedRow;
    if (target === state.shownRow) return;
    if (BRAIN.highlightNode) BRAIN.highlightNode(target);
    state.shownRow = target;
  }

  // Pin the hover ring + tooltip on `idx` (or hide when idx < 0), and record
  // it as the node a click will select. Shared by the throttled hover and the
  // pointerdown refresh so the LABEL, the RING, and the eventual SELECTION are
  // always the same node — the passive hover is throttled and can lag the
  // cursor in the dense core, which made a click look like it grabbed an
  // unrelated node (user report 2026-07-08).
  // `ctx` bundles the four collaborators pinHover needs (dom, tip, nodes,
  // state) into one parameter object — Introduce Parameter Object, §4.4.
  function pinHover(ctx, idx, clientX, clientY) {
    ctx.state.pickIdx = idx;
    // Light up the hovered node's associations (edges + neighbour nodes).
    ctx.state.hoverRow = idx;
    applyHighlight(ctx.state);
    if (idx < 0) {
      ctx.dom.style.cursor = '';
      hoverRing.visible = false;
      if (ctx.tip) ctx.tip.style.display = 'none';
      return;
    }
    ctx.dom.style.cursor = 'pointer';
    showHoverRing(worldOf(idx));
    if (ctx.tip) {
      var n = ctx.nodes[idx];
      var kind = n.kind || n.type || '';
      ctx.tip.textContent = (kind ? kind + ' · ' : '') + (n.label || n.id || '').slice(0, 80);
      ctx.tip.style.left = (clientX + 14) + 'px';
      ctx.tip.style.top = (clientY + 14) + 'px';
      ctx.tip.style.display = 'block';
    }
  }

  // A click commits the ring-pinned node unless the pointer moved (orbit
  // drag) or nothing was pinned. Split out of the pointerup listener to keep
  // that listener's nesting within §4.5 (guard clauses, no nested if).
  function commitClickIfValid(ctx, e) {
    if (!ctx.state.down || Math.abs(e.clientX - ctx.state.down.x) > 4 || Math.abs(e.clientY - ctx.state.down.y) > 4) return;
    if (ctx.state.pickIdx < 0) {
      ring.visible = false;
      if (window.JUG && JUG.deselectNode) JUG.deselectNode();
      return;
    }
    // Ring placement is handled by the graph:selectNode listener below, so a
    // node selected via a connection link gets the same highlight.
    BRAIN.selectNode(ctx.nodes[ctx.state.pickIdx]);   // exactly the node the ring pinned at press
  }

  // pointerdown/up/move/leave: click-vs-drag detection, throttled hover, and
  // the tooltip/cursor/ring feedback that makes picking feel precise.
  function attachPointerHandlers(ctx) {
    ctx.dom.addEventListener('pointerdown', function (e) {
      ctx.state.down = { x: e.clientX, y: e.clientY };
      // Recompute the pick at the EXACT press point, unthrottled and with the
      // forgiving CLICK_PX radius, and snap the ring/tooltip to it — so the
      // release commits precisely the node shown, not a throttle-stale one.
      pinHover(ctx, nearestToCursor(e.clientX, e.clientY, CLICK_PX), e.clientX, e.clientY);
    });

    ctx.dom.addEventListener('pointerup', function (e) {
      commitClickIfValid(ctx, e);
    });

    // Hover: throttled label tooltip + pointer cursor. Skipped while dragging.
    ctx.dom.addEventListener('pointermove', function (e) {
      if (e.buttons) { if (ctx.tip) ctx.tip.style.display = 'none'; return; }
      var now = (window.performance && performance.now) ? performance.now() : Date.now();
      if (now - ctx.state.lastHover < HOVER_MS) return;
      ctx.state.lastHover = now;
      pinHover(ctx, nearestToCursor(e.clientX, e.clientY, HOVER_PX), e.clientX, e.clientY);
    });

    ctx.dom.addEventListener('pointerleave', function () {
      hoverRing.visible = false;
      if (ctx.tip) ctx.tip.style.display = 'none';
      ctx.state.hoverRow = -1;
      applyHighlight(ctx.state);   // fall back to the selected node's highlight, or clear
    });
  }

  // Highlight whatever node becomes selected — by direct click (handled via
  // pinHover/pointerup above), or by clicking a connection link in the detail
  // panel (which selects by id and only reaches BRAIN through these events).
  function attachSelectionListeners(state) {
    if (!(window.JUG && JUG.on)) return;
    JUG.on('graph:selectNode', function (node) {
      var i = BRAIN.indexOfId ? BRAIN.indexOfId.get(node.id) : null;
      if (i == null) { ring.visible = false; return; }
      showRing(worldOf(i));
      state.selectedRow = i;   // lock the highlight on the selection until deselect
      applyHighlight(state);
    });
    JUG.on('graph:deselectNode', function () {
      ring.visible = false;
      state.selectedRow = -1;
      applyHighlight(state);
    });
  }

  BRAIN.initPicking = function (nodes) {
    BRAIN.scene.add(ring);
    BRAIN.scene.add(hoverRing);
    var ctx = {
      dom: BRAIN.renderer.domElement,
      tip: document.getElementById('brain-tip'),
      nodes: nodes,
      state: createPickState()
    };
    attachPointerHandlers(ctx);
    attachSelectionListeners(ctx.state);
  };
})();
