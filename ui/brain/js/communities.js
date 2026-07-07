// Cortex Brain View — associative community detection over memory nodes.
//
// Pure graph algorithm, zero THREE / zero I/O, so it can run both in the
// browser (boot.js, before placement/colouring) and in a headless `node -c`
// numeric harness. Detects distinct "associates_with" communities among
// MEMORY nodes only — force_layout.js then pulls each community toward its
// own spatial attractor (see K_COMMUNITY) so associative clusters read as
// separated blobs instead of one central mass.
//
//   source: Raghavan, U.N., Albert, R. & Kumara, S. (2007), "Near linear
//   time algorithm to detect community structures in large-scale networks",
//   Physical Review E 76:036106 — label propagation algorithm (LPA) this
//   module implements.
//
// LPA is normally randomized (random visit order, random tie-break) to
// avoid oscillation. This implementation is DELIBERATELY deterministic
// instead: nodes are visited in ascending row-index order (itself derived
// from ascending node id via `indexOfId`), and a label tie is always broken
// by the LOWEST candidate label id — never at random. Two runs on identical
// input therefore produce byte-identical community assignments, which the
// brain view depends on for stable community colours/attractors across
// reloads (Fruchterman & Reingold-style layouts already assume a stable
// input; a non-deterministic community id would make every reload reshuffle
// colours and attractor positions for no reason).

window.BRAIN = window.BRAIN || {};

(function () {
  var MAX_ITERATIONS = 20;  // bounded LPA sweeps; stops early once labels stabilize

  // A top-k=8 sparse association graph fragments into thousands of tiny
  // communities (mostly singletons/pairs from weakly-linked memories). Giving
  // every one its own spatial attractor + hue produced a chaotic starburst,
  // not legible blobs. So only communities of at least this many members are
  // treated as DISTINCT (own attractor in force_layout.js, own hue in
  // palette.js); smaller ones stay diffuse at their anatomical anchor and take
  // the default per-kind colour. Visual-legibility threshold, not sourced.
  BRAIN.MIN_COMMUNITY_SIZE = 12;

  function endId(v) { return (typeof v === 'object' && v) ? v.id : v; }

  function isMemoryNode(node) {
    return (node.kind || node.type) === 'memory';
  }

  // row -> [neighbour rows], restricted to memory<->memory 'associates_with'
  // edges. Undirected (both directions added); self-loops and duplicate
  // parallel edges are ignored (a repeated neighbour would just double-count
  // its vote in propagate(), skewing degree-weighted ties for no reason).
  function buildAdjacency(edges, indexOfId, memMask) {
    var adj = new Map();
    for (var e = 0; e < edges.length; e++) {
      var edge = edges[e];
      if ((edge.kind || edge.type) !== 'associates_with') continue;
      var si = indexOfId.get(endId(edge.source));
      var ti = indexOfId.get(endId(edge.target));
      if (si == null || ti == null || si === ti) continue;
      if (!memMask[si] || !memMask[ti]) continue;
      addNeighbour(adj, si, ti);
      addNeighbour(adj, ti, si);
    }
    return adj;
  }

  function addNeighbour(adj, a, b) {
    var list = adj.get(a);
    if (!list) { list = []; adj.set(a, list); }
    if (list.indexOf(b) === -1) list.push(b);
  }

  // Deterministic label propagation. memRows is the fixed, ascending visit
  // order for every sweep. Each node adopts the label held by the plurality
  // of its neighbours; ties resolve to the lowest label id. Nodes with no
  // 'associates_with' neighbour never change label — their initial label
  // (their own row index) survives, i.e. they end up a singleton community.
  function propagate(memRows, adj) {
    var label = new Map();
    for (var i = 0; i < memRows.length; i++) label.set(memRows[i], memRows[i]);

    for (var iter = 0; iter < MAX_ITERATIONS; iter++) {
      var changed = false;
      for (var m = 0; m < memRows.length; m++) {
        var row = memRows[m];
        var neighbours = adj.get(row);
        if (!neighbours || neighbours.length === 0) continue;

        var counts = new Map();
        for (var n = 0; n < neighbours.length; n++) {
          var lb = label.get(neighbours[n]);
          counts.set(lb, (counts.get(lb) || 0) + 1);
        }
        var best = label.get(row);
        var bestCount = -1;
        counts.forEach(function (count, lb) {
          if (count > bestCount || (count === bestCount && lb < best)) {
            bestCount = count;
            best = lb;
          }
        });
        if (best !== label.get(row)) { label.set(row, best); changed = true; }
      }
      if (!changed) break;
    }
    return label;
  }

  // Converged raw labels are arbitrary row ids with gaps. Remap to compact
  // ids 0..count-1, assigned in the order communities are first encountered
  // while scanning memRows ascending — deterministic, no sorting of labels
  // required (memRows is already ascending).
  function normalizeLabels(memRows, label) {
    var remap = new Map();
    var next = 0;
    for (var i = 0; i < memRows.length; i++) {
      var root = label.get(memRows[i]);
      if (!remap.has(root)) remap.set(root, next++);
    }
    return { remap: remap, count: next };
  }

  // nodes: full graph node array (any order); edges: full graph edge array;
  // indexOfId: id -> row-in-`nodes` Map (same one force_layout.js/edges.js
  // use). Returns { communityOf: Map(nodeId -> communityId:int),
  // sizes: Map(communityId -> memberCount), count: int }. Memory nodes with
  // no 'associates_with' edge get their own singleton community.
  BRAIN.detectCommunities = function (nodes, edges, indexOfId) {
    var memMask = new Uint8Array(nodes.length);
    var memRows = [];
    for (var i = 0; i < nodes.length; i++) {
      if (isMemoryNode(nodes[i])) { memMask[i] = 1; memRows.push(i); }
    }

    var adj = buildAdjacency(edges, indexOfId, memMask);
    var label = propagate(memRows, adj);
    var norm = normalizeLabels(memRows, label);

    var communityOf = new Map();
    var sizes = new Map();
    for (var m = 0; m < memRows.length; m++) {
      var row = memRows[m];
      var cid = norm.remap.get(label.get(row));
      communityOf.set(nodes[row].id, cid);
      sizes.set(cid, (sizes.get(cid) || 0) + 1);
    }

    return { communityOf: communityOf, sizes: sizes, count: norm.count };
  };
})();
