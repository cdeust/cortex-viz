// Cortex Brain View — graph data.
//
// Fetches the SAME graph the unified galaxy renders, via the throttled
// NDJSON stream (/api/graph/full/stream through JUG.streamFullGraph —
// graph_stream_loader.js, included by brain-viz.html). The single-document
// /api/graph/full form crossed ~1.17 GB decompressed, which
// response.json() cannot hold — streaming frames through a bounded queue
// delivers the same totality (every domain, skill, command, hook, agent,
// mcp, tool hub, file, discussion, memory, entity and AST symbol, plus all
// edges) without ever materialising the whole document. The legacy
// single-fetch path remains as fallback for older servers.
//
// Returns the accumulated nodes/edges/meta plus a kind histogram for the
// stats panel and the per-domain anchors (galaxy x/y + member counts) that
// regions.js carves the brain into lobes with. onProgress (optional) is
// called with {nodes, edges, node_total, edge_total} as frames land, so
// boot.js can show a live loading count.

window.BRAIN = window.BRAIN || {};

(function () {
  function summarize(nodes, edges, meta) {
    var byKind = {};
    var domNodes = {};   // id -> the kind:domain node (carries galaxy x/y)
    var domCount = {};   // domain_id -> member count
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var k = node.kind || node.type || 'unknown';
      byKind[k] = (byKind[k] || 0) + 1;
      var did = node.domain_id;
      if (did) domCount[did] = (domCount[did] || 0) + 1;
      if (k === 'domain') domNodes[node.id] = node;
    }
    var domains = Object.keys(domNodes).map(function (id) {
      return { id: id, label: (domNodes[id].label || id).replace(/^domain:/, ''),
               count: domCount[id] || 0 };
    }).sort(function (a, b) { return b.count - a.count; });
    var domainPos = {};
    domains.forEach(function (d) {
      domainPos[d.id] = { x: domNodes[d.id].x, y: domNodes[d.id].y };
    });
    return {
      nodes: nodes, edges: edges, meta: meta || {}, byKind: byKind,
      domains: domains, domainIds: domains.map(function (d) { return d.id; }),
      domainPos: domainPos,
    };
  }

  function fetchLegacy() {
    return fetch('/api/graph/full', { headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (r.status === 503) {
          throw new Error('graph snapshot still warming up (no build has finished yet)');
        }
        if (!r.ok) throw new Error('graph fetch failed: HTTP ' + r.status);
        return r.json();
      })
      .then(function (g) {
        return summarize(g.nodes || [], g.edges || g.links || [], g.meta);
      });
  }

  function streamOnce(onProgress) {
    var nodes = [];
    var edges = [];
    return JUG.streamFullGraph({
      apply: function (n, e) {
        for (var i = 0; i < n.length; i++) nodes.push(n[i]);
        for (var j = 0; j < e.length; j++) edges.push(e[j]);
      },
      onProgress: onProgress || function () {},
    }).then(function (res) {
      return { res: res, nodes: nodes, edges: edges };
    });
  }

  BRAIN.fetchGraph = function (onProgress) {
    if (!window.JUG || typeof JUG.streamFullGraph !== 'function') {
      return fetchLegacy();
    }
    return streamOnce(onProgress).then(function (got) {
      // A truncated stream (server restarted mid-transfer) must never
      // render as the full brain — retry once from scratch (the brain
      // accumulates locally, so a clean second pass replaces the partial).
      if (got.res && got.res.truncated) {
        console.warn('[brain] stream truncated — retrying once');
        return streamOnce(onProgress);
      }
      return got;
    }).then(function (got) {
      if (!got.res || !got.res.ok || !got.nodes.length) return fetchLegacy();
      return summarize(got.nodes, got.edges, got.res.meta);
    }).catch(function (err) {
      console.warn('[brain] stream load failed, trying legacy:', err && err.message);
      return fetchLegacy();
    });
  };
})();
