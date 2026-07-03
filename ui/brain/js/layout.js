// Cortex Brain View — anatomical node placement.
//
// Places each graph node in the brain region that houses its kind of memory
// (see anatomy.js): episodic memories in the medial temporal lobe, semantic
// entities in the anterior temporal lobe, procedural skills/tools in the
// striatum + cerebellum, organizational domains at the connectome's rich-club
// hubs, and so on.
//
// The centrepiece is the EPISODIC CONSOLIDATION gradient: a memory's heat
// (recency/activation) sets where it sits between the hippocampus (hot, just
// encoded, medial-temporal-dependent) and a dispersed neocortical site (cold,
// consolidated, hippocampus-independent).
//   source: McClelland, McNaughton & O'Reilly (1995) "Why there are
//   complementary learning systems in the hippocampus and neocortex",
//   Psychol Rev 102:419-457.  source: Squire & Alvarez (1995) Curr Opin
//   Neurobiol 5:169-177.
//
// Every placed point is clamped just inside the visible cortical surface
// (regions.js radiusInDir). A seeded PRNG keyed on node id makes the layout
// deterministic across reloads.

window.BRAIN = window.BRAIN || {};

(function () {
  var INSET = 0.97;                 // keep points inside the surface
  var DEFAULT_HEAT = 0.5;           // memories with no heat sit mid-gradient
  var COLD_SPREAD = 1.9;            // cold memories disperse this much wider

  // FNV-1a — stable string hash for per-id hemisphere + jitter seed.
  function hashStr(s) {
    var h = 0x811c9dc5;
    s = String(s == null ? '' : s);
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }

  // mulberry32 — small, fast seeded PRNG.
  // source: https://github.com/bryc/code/blob/master/jshash/PRNG.md
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Standard normal via Box-Muller, drawing from the supplied uniform PRNG.
  function gauss(rand) {
    var u = 1 - rand(), v = rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function hemiFor(node) {
    var key = node.domain_id || node.id;
    return (hashStr(key) & 1) ? 1 : -1;
  }

  // Pull a point inward so it sits just inside the cortical surface in its
  // own direction. Leaves interior points (already inside) untouched.
  function clampInside(p, surface) {
    var r = Math.sqrt(p.x * p.x + p.y * p.y + p.z * p.z);
    if (r <= 1e-6) return;
    var maxR = surface.radiusInDir(p.x, p.y, p.z) * INSET;
    if (r > maxR) { var k = maxR / r; p.x *= k; p.y *= k; p.z *= k; }
  }

  // Gaussian blob around a world centre with per-axis sigma.
  function sampleBlob(center, sigma, rand, scale) {
    var s = scale || 1;
    return new THREE.Vector3(
      center.x + gauss(rand) * sigma.x * s,
      center.y + gauss(rand) * sigma.y * s,
      center.z + gauss(rand) * sigma.z * s
    );
  }

  // Rank-normalize memory heat for DISPLAY. The stored heat distribution is
  // heavily degenerate — ~79% of memories fall in a narrow band near 0.47, with
  // a second spike at 0.0 — so mapping heat directly onto the consolidation
  // gradient collapses almost every memory onto one mid-depth shell. Replacing
  // each memory's heat with its empirical rank (fractional CDF; ties share the
  // block-average rank) spreads the population across the full
  // hippocampus->neocortex depth while preserving the EXACT ordering of heat.
  // This is an order-preserving DISPLAY transform, not a re-scoring of memory:
  // relative hot/cold is faithful; absolute depth is illustrative.
  //   source: measured heat histogram of /api/graph/full on 2026-07-01
  //   (108,944 memories; median 0.4688; 86,251 in [0.45,0.55); 477 >= 0.7).
  function buildVizHeat(nodes) {
    var items = [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if ((n.kind || n.type) === 'memory') {
        var h = typeof n.heat === 'number' ? Math.min(Math.max(n.heat, 0), 1) : DEFAULT_HEAT;
        items.push({ id: n.id, h: h });
      }
    }
    items.sort(function (a, b) { return a.h - b.h; });
    var m = items.length, map = {};
    var lo = 0;
    while (lo < m) {
      var hi = lo;
      while (hi < m && items[hi].h === items[lo].h) hi++;   // tie block [lo, hi)
      var norm = m > 1 ? ((lo + hi - 1) / 2) / (m - 1) : 0.5;  // block-average rank
      for (var k = lo; k < hi; k++) map[items[k].id] = norm;
      lo = hi;
    }
    return map;
  }

  // Episodic consolidation: lerp hippocampus -> domain neocortical anchor by
  // (1 - vizHeat); cold memories also disperse wider (COLD_SPREAD). `vizHeat`
  // is the rank-normalized display heat (see buildVizHeat), already in [0, 1].
  function placeMemory(vizHeat, atlas, surface, anchor, hemi, rand) {
    var t = 1 - vizHeat;
    var hot = atlas.centerOf('hippocampus', hemi);
    var cold = anchor || atlas.centerOf('parietal_temporal', hemi);
    var center = new THREE.Vector3(
      hot.x + (cold.x - hot.x) * t,
      hot.y + (cold.y - hot.y) * t,
      hot.z + (cold.z - hot.z) * t
    );
    var sigma = atlas.sigmaOf('hippocampus');
    var p = sampleBlob(center, sigma, rand, 1 + t * (COLD_SPREAD - 1));
    clampInside(p, surface);
    return p;
  }

  // Non-memory node: Gaussian blob in its kind's region.
  function placeGeneric(center, sigma, surface, rand) {
    var p = sampleBlob(center, sigma, rand, 1);
    clampInside(p, surface);
    return p;
  }

  // Region key recorded for a node (drives edge tract selection). A hot memory
  // routes from the hippocampus; a consolidated one from neocortical
  // association cortex. Uses the same rank-normalized vizHeat as placeMemory so
  // routing and position agree.
  function memoryRegionKey(vizHeat) {
    return (1 - vizHeat) < 0.5 ? 'hippocampus' : 'parietal_temporal';
  }

  // nodes: graph nodes; atlas: buildAtlas(box); surface: buildSurface(soup);
  // domainInfo: { index:{id->i}, anchor:{id->Vector3} }.
  // Returns { positions: Float32Array(3N), regionKey: string[], hemi: Int8Array }.
  BRAIN.placeNodes = function (nodes, atlas, surface, domainInfo) {
    var n = nodes.length;
    var out = new Float32Array(n * 3);
    var regionKey = new Array(n);
    var hemiArr = new Int8Array(n);
    var idx = domainInfo.index || {};
    var anchors = domainInfo.anchor || {};
    var vizHeat = buildVizHeat(nodes);

    for (var i = 0; i < n; i++) {
      var node = nodes[i];
      var kind = node.kind || node.type || 'unknown';
      var hemi = hemiFor(node);
      var rand = mulberry32(hashStr(node.id) ^ 0x9e3779b9);
      var p, rkey;

      if (kind === 'memory') {
        var vh = typeof vizHeat[node.id] === 'number' ? vizHeat[node.id] : DEFAULT_HEAT;
        p = placeMemory(vh, atlas, surface, anchors[node.domain_id], hemi, rand);
        rkey = memoryRegionKey(vh);
      } else if (kind === 'domain') {
        rkey = atlas.hubSeat(idx[node.id] || 0);
        p = placeGeneric(atlas.centerOf(rkey, hemi), atlas.sigmaOf(rkey), surface, rand);
      } else {
        rkey = atlas.regionForKind(kind);
        p = placeGeneric(atlas.centerOf(rkey, hemi), atlas.sigmaOf(rkey), surface, rand);
      }

      var j = i * 3;
      out[j] = p.x; out[j + 1] = p.y; out[j + 2] = p.z;
      regionKey[i] = rkey;
      hemiArr[i] = hemi;
    }
    return { positions: out, regionKey: regionKey, hemi: hemiArr };
  };
})();
