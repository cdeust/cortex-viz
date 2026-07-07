// Cortex Brain View — associative community READER.
//
// Communities are no longer computed in the browser. They are detected
// SERVER-SIDE (cortex_viz/core/community_detection.py) with Leiden +
// CPM on the sparse co-entity association channel, and each memory node
// arrives from /api/graph/full carrying a ``community_id`` field. This
// module just reads that field into the { communityOf, sizes, count }
// shape the rest of the brain view (force_layout.js attractors,
// palette.js hues, boot.js colouring) already consumes — the contract
// is unchanged, only the SOURCE of the community id moved off the client.
//
// Why the move: the previous in-browser label-propagation (Raghavan,
// Albert & Kumara 2007) collapsed the dense combined substrate into ONE
// mega-community (87-93% of memories under a single label, measured
// 2026-07-07) — LPA optimizes no global objective and percolates one
// label across a hub-heavy graph. Leiden + CPM on the co-entity channel
// (server-side) does not, and CPM is resolution-limit-free so it keeps
// small topical communities instead of merging them (Traag, Van Dooren
// & Nesterov 2011). The brain view still RENDERS all three additive
// association channels; only DETECTION was moved and narrowed.
//
//   source: Traag, V.A., Waltman, L. & van Eck, N.J. (2019), "From
//   Louvain to Leiden: guaranteeing well-connected communities",
//   Scientific Reports 9:5233.

window.BRAIN = window.BRAIN || {};

(function () {
  // A memory's community is DISTINCT (own spatial attractor in
  // force_layout.js, own hue in palette.js) only when at least this many
  // members share it; smaller communities stay diffuse at their
  // anatomical anchor and take the default per-kind colour. This keeps a
  // sparse graph's thousands of singleton/tiny communities from becoming
  // a chaotic starburst of attractors and hues. Visual-legibility
  // threshold, not sourced — unchanged from the prior LPA reader.
  BRAIN.MIN_COMMUNITY_SIZE = 12;

  function isMemoryNode(node) {
    return (node.kind || node.type) === 'memory';
  }

  // nodes: full graph node array. `edges` and `indexOfId` are accepted
  // for call-site compatibility (boot.js passes them) but unused — the
  // community assignment now travels on the nodes themselves. Returns
  // { communityOf: Map(nodeId -> communityId:int),
  //   sizes: Map(communityId -> memberCount), count: int }. A memory
  // whose server payload carries no ``community_id`` (e.g. a snapshot
  // built before server-side detection, or a build where the optional
  // igraph/leidenalg deps were absent so detection degraded) is simply
  // omitted — boot.js then colours it by kind, exactly as it does for a
  // sub-threshold community. So a missing field degrades gracefully to
  // the pre-community colouring rather than throwing.
  BRAIN.detectCommunities = function (nodes, edges, indexOfId) {
    var communityOf = new Map();
    var sizes = new Map();
    var maxCid = -1;
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (!isMemoryNode(node)) continue;
      var cid = node.community_id;
      if (cid == null) continue;  // no server assignment → colour by kind
      communityOf.set(node.id, cid);
      sizes.set(cid, (sizes.get(cid) || 0) + 1);
      if (cid > maxCid) maxCid = cid;
    }
    // Server community ids are contiguous 0..N-1 (co-entity Leiden
    // communities first, then one fresh id per isolated memory), so the
    // distinct-community count is maxCid + 1; 0 when nothing was tagged.
    return { communityOf: communityOf, sizes: sizes, count: maxCid + 1 };
  };
})();
