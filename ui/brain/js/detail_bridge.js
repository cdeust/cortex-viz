// Cortex Brain View — galaxy detail-panel bridge.
//
// Reuses the unified galaxy's detail subsystem (JUG._fmt / JUG._memSci /
// detail_panel.js) verbatim so a node picked in the brain shows the EXACT
// same rich card the galaxy shows — header, signal strength, metrics gauges,
// content, tags, emotion, biological state, connections, badges and the
// memory-science meaning/explained panels. Those modules drive themselves off
// the JUG namespace and the same /api/graph/node enrichment the galaxy uses;
// this bridge only supplies the few renderer-side hooks they expect (node
// lookup, edge adjacency, selection) which normally come from the galaxy's
// force-graph renderer.

window.BRAIN = window.BRAIN || {};

(function () {
  function edgeEnd(e, which) {
    var v = e[which];
    return typeof v === 'object' && v ? v.id : v;
  }

  // Build the node index + per-node edge adjacency once, and publish the
  // renderer hooks the galaxy panel reads.
  BRAIN.installDetailBridge = function (nodes, edges) {
    var JUG = window.JUG;
    if (!JUG) return;

    var index = new Map();
    var ordinal = new Map();   // id -> position in nodes[] == row in the points buffer
    for (var i = 0; i < nodes.length; i++) { index.set(nodes[i].id, nodes[i]); ordinal.set(nodes[i].id, i); }
    JUG._nodeIndex = index;
    BRAIN.indexOfId = ordinal;

    var adj = new Map();
    function bucket(id, e) {
      var a = adj.get(id);
      if (!a) { a = []; adj.set(id, a); }
      a.push(e);   // every edge a node touches — no cap
    }
    for (var k = 0; k < edges.length; k++) {
      var e = edges[k];
      bucket(edgeEnd(e, 'source'), e);
      bucket(edgeEnd(e, 'target'), e);
    }
    BRAIN._adj = adj;

    JUG.getGraph = function () {
      return { graphData: function () { return { nodes: nodes, edges: edges }; } };
    };
    // Clicking a connection in the panel navigates: select it AND fly the
    // camera to it, since the linked node is often across the cortex.
    JUG.selectNodeById = function (id) {
      var n = index.get(id);
      if (!n) return;
      BRAIN.selectNode(n);
      BRAIN.focusNode(n);
    };
    JUG.deselectNode = function () {
      JUG.state.selectedNode = null;
      JUG.emit('graph:deselectNode');
    };
    // Galaxy (not trace) rendering path, so memory/discussion nodes get their
    // full PG-enriched cards rather than the single-event trace renderer.
    JUG.state.activeView = 'galaxy';
  };

  // Open the galaxy detail card for a brain node. Scope the connections to
  // this node's adjacency, then emit graph:selectNode — detail_panel.js opens
  // the panel and enriches it from /api/graph/node on its own.
  BRAIN.selectNode = function (node) {
    var JUG = window.JUG;
    if (!JUG || !node) return;
    JUG._currentEdges = (BRAIN._adj && BRAIN._adj.get(node.id)) || [];
    JUG.state.selectedNode = node;
    JUG.emit('graph:selectNode', node);
  };

  // Pan the camera to a node by its row in the points buffer. Dolly in to a
  // comfortable framing if we're currently far out, so the target is legible.
  BRAIN.focusNode = function (node) {
    var pos = BRAIN.nodePositions;
    var i = BRAIN.indexOfId ? BRAIN.indexOfId.get(node.id) : null;
    if (i == null || !pos || !BRAIN.focusOn) return;
    var target = new THREE.Vector3(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
    BRAIN.focusOn(target, (BRAIN.TARGET_RADIUS || 80) * 0.9);
  };
})();
