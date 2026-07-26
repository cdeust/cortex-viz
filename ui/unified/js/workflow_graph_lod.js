// Cortex — Workflow Graph LOD + edge-coverage decisions (pure seams).
//
// Extracted from workflow_graph.js (issue #41: 500-line file cap, §4.1).
// Both functions are pure and mutation-tested (stryker.conf.json scopes the
// wfgEdgeCoverage range; wfgLodTier is recorded in tests/js/MUTATION_NOTES.md
// from issue #35). Kept together as the "how much of the graph do we
// materialise, and account for what we drop" concern (§1.1 SRP).
//
// Publishes the two seams the render/coverage layers read:
//   window.JUG._wfg.lodTier      (issue #35 seam, PR #40)
//   window.JUG._wfg.edgeCoverage (issue #36 seam, PR #42)
(function () {
  // ── LOD aggregation tiers ─────────────────────────────────────────────────
  // Node-count thresholds that decide how much of the graph the simulation and
  // renderer materialise. Above each bound the main thread starts to stall, so
  // the layout progressively drops work: pin symbols + drop dense sim edges
  // (heavy), start every node AT its deterministic slot then decay fast so the
  // first paint is the settled galaxy (snapToSlots), and drop the densest
  // symbol↔symbol `calls` edges from the render set entirely (extreme).
  // source: tasks/galaxy-lag-and-ap-aggregation-audit.md (measured main-thread
  // freeze points on the N≈17k → N≈27k jump; see also ADR-0047).
  function wfgLodTier(nodeCount) {
    return {
      heavy: nodeCount > 8000,
      snapToSlots: nodeCount > 15000,
      extreme: nodeCount > 25000,
    };
  }

  // Edge-set reduction for one render, and its coverage accounting (issue #36).
  // Pure: given the input edges, the id-set of rendered nodes, and whether the
  // EXTREME tier is engaged, return the SURVIVING raw edges plus the two
  // DISJOINT drop counts the coverage indicator names —
  //   * droppedEdges  = dense `calls` edges the EXTREME LOD tier sheds (0 when
  //                     not extreme): a genuine aggregation the user must see.
  //   * danglingEdges = edges whose endpoint was sampled out of the node set:
  //                     a data-integrity prune, at every tier.
  // Post: droppedEdges + danglingEdges + rendered.length === input.length, so
  // the accounting is exhaustive (no edge is silently unaccounted for). mount()
  // consumes `rendered`; the counts ride on JUG.__wfgRendered.
  function wfgEdgeCoverage(dataEdges, nodeIdSet, extreme) {
    var input = dataEdges || [];
    var afterLod = extreme
      ? input.filter(function (e) { return e.kind !== 'calls'; })
      : input;
    var rendered = afterLod.filter(function (e) {
      var s = typeof e.source === 'object' ? e.source.id : e.source;
      var t = typeof e.target === 'object' ? e.target.id : e.target;
      return nodeIdSet[s] && nodeIdSet[t];
    });
    return {
      rendered: rendered,
      droppedEdges: input.length - afterLod.length,
      danglingEdges: afterLod.length - rendered.length,
    };
  }

  window.JUG = window.JUG || {};
  window.JUG._wfg = window.JUG._wfg || {};
  window.JUG._wfg.lodTier = wfgLodTier;
  window.JUG._wfg.edgeCoverage = wfgEdgeCoverage;
})();
