// Cortex — Workflow Graph: uniform-grid spatial hash for canvas hit-testing.
//
// Replaces the O(N) reverse-scan in workflow_graph_render_canvas.js. Nodes are
// bucketed into a uniform grid keyed by (floor(x/cell), floor(y/cell)); a point
// query inspects only the 3x3 cell neighborhood around the query cell.
//
// Correctness invariant (why 3x3 is sufficient): a node is a hit only when its
// centre lies within its radius r of the query point. Cortex node radii top out
// at nodeRadius(domain)=26 (+6 bump, +2 pad) = 34 world units (workflow_graph.js
// KIND_RADIUS). With cell=200 > max r, |centre - point| <= r < cell implies the
// centre's cell differs from the point's cell by at most 1 on each axis — so the
// 3x3 neighborhood always contains every possible hit. If a caller ever uses a
// cell smaller than the largest radius this invariant breaks; the constructor
// caps nothing, so keep cell > max node radius.
//
// Pattern source: uniform spatial hashing / grid bucketing — Ericson, "Real-Time
// Collision Detection" (2005), Ch. 7 "Spatial Partitioning", uniform grids.
// Borrowed conceptually from supermemory's 200px grid hit-test (closed engine;
// reimplements the idea, benchmarked on its own merits — see
// tasks/borrow-from-supermemory-handover.md §4).
(function () {
  function SpatialHash(cellSize) {
    this.cell = cellSize || 200;   // world units; must exceed max node radius
    this.buckets = new Map();      // "cx,cy" -> array of node indices
  }

  SpatialHash.prototype._key = function (cx, cy) { return cx + ',' + cy; };

  // Bucket every node with a finite position. Stores indices into `nodes` so the
  // caller keeps draw-order (topmost-wins) semantics: a higher index was drawn
  // later, i.e. on top.
  SpatialHash.prototype.build = function (nodes) {
    this.buckets.clear();
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.x == null || n.y == null) continue;
      if (!isFinite(n.x) || !isFinite(n.y)) continue;
      var cx = Math.floor(n.x / this.cell), cy = Math.floor(n.y / this.cell);
      var k = this._key(cx, cy);
      var b = this.buckets.get(k);
      if (!b) { b = []; this.buckets.set(k, b); }
      b.push(i);
    }
    return this;
  };

  // Returns the node indices in the 3x3 cell neighborhood of (x, y). The caller
  // performs the precise circle test and topmost-wins tiebreak.
  SpatialHash.prototype.queryNeighborhood = function (x, y) {
    var out = [];
    var cx = Math.floor(x / this.cell), cy = Math.floor(y / this.cell);
    for (var dx = -1; dx <= 1; dx++) {
      for (var dy = -1; dy <= 1; dy++) {
        var b = this.buckets.get(this._key(cx + dx, cy + dy));
        if (b) { for (var i = 0; i < b.length; i++) out.push(b[i]); }
      }
    }
    return out;
  };

  var root = (typeof window !== 'undefined') ? window
           : (typeof globalThis !== 'undefined') ? globalThis : this;
  root.JUG = root.JUG || {};
  root.JUG._wfg = root.JUG._wfg || {};
  root.JUG._wfg.SpatialHash = SpatialHash;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { SpatialHash: SpatialHash };
  }
})();
