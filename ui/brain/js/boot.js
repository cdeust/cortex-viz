// Cortex Brain View — boot / composition root.
//
// Loads the graph data and the brain mesh in parallel, places every node
// inside the cortex, builds the point cloud, wires picking, and fills the
// stats + legend chrome. This is the only module that knows about all the
// others; each of them stays single-purpose.

window.BRAIN = window.BRAIN || {};

(function () {
  var MODEL_URL = '/brain/models/brain.glb';

  function setStatus(msg) {
    var el = document.getElementById('loading-sub');
    if (el) el.textContent = msg;
  }

  function hideLoading() {
    var el = document.getElementById('loading');
    if (el) el.classList.add('gone');
  }

  function fail(msg) {
    var el = document.getElementById('loading');
    if (el) {
      el.classList.add('error');
      var t = document.getElementById('loading-title');
      var s = document.getElementById('loading-sub');
      if (t) t.textContent = 'Could not load brain view';
      if (s) s.textContent = msg;
    }
  }

  var KIND_ORDER = ['domain', 'skill', 'command', 'hook', 'agent', 'mcp', 'tool_hub',
    'file', 'discussion', 'memory', 'entity', 'symbol'];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
    });
  }

  // Per-node RGB using the galaxy's own colour for each node (node.color carries
  // the kind/heat colour the unified graph renders), so the brain is
  // differentiated by node type exactly like the galaxy. Float32Array(3*N),
  // shared by the point cloud AND the edge web (edges gradient between their
  // endpoints' colours).
  function buildNodeColors(nodes) {
    var arr = new Float32Array(nodes.length * 3);
    var c = new THREE.Color();
    var getColor = (window.JUG && JUG.getNodeColor)
      ? JUG.getNodeColor
      : function (n) { return n.color || '#8aa0c0'; };
    for (var i = 0; i < nodes.length; i++) {
      try { c.set(getColor(nodes[i]) || '#8aa0c0'); } catch (e) { c.set('#8aa0c0'); }
      arr[i * 3] = c.r; arr[i * 3 + 1] = c.g; arr[i * 3 + 2] = c.b;
    }
    return arr;
  }

  // Per-domain placement inputs: a stable index (for round-robin hub seats) and
  // a neocortical surface anchor (the cold end of the memory consolidation
  // gradient, and the coherence point for a domain's entities/symbols).
  function buildDomainInfo(data, surface) {
    var index = {};
    var anchor = {};
    data.domains.forEach(function (d, i) {
      index[d.id] = i;
      anchor[d.id] = surface.anchorForDomain(data.domainPos[d.id], d.id);
    });
    return { index: index, anchor: anchor };
  }

  function fillStats(data) {
    // Rendered-graph rows ONLY. The store-truth rows (s-nodes, s-edges,
    // s-dom, s-mem, s-ent) are owned by vitals.js from /api/stats — the
    // same public totals the galaxy sidebar shows; writing snapshot-derived
    // counts over them made the two views disagree (user report 2026-07-02).
    var bk = data.byKind;
    var fmt = function (n) { return (n || 0).toLocaleString('en-US'); };
    document.getElementById('r-nodes').textContent = fmt(data.nodes.length);
    document.getElementById('r-edges').textContent = fmt(data.edges.length);
    document.getElementById('s-sym').textContent = fmt(bk.symbol);
  }

  // EXHAUSTIVE legend: every distinct colour a kind renders with gets its own
  // labelled row. Colour is semantic (memory→consolidation stage, entity/
  // symbol→type, file→primary tool), so a single swatch per kind would hide
  // most of what's on screen. We tally distinct (colour → count) per kind from
  // the real node colours and label each via BRAIN.PALETTE (mirrors the server
  // palette), so the legend matches the render exactly.
  function fillLegend(data) {
    // kind -> { colorHex(upper) -> count }, and first colour per kind.
    var byKindColor = {};
    var firstColor = {};
    for (var i = 0; i < data.nodes.length; i++) {
      var n = data.nodes[i];
      var k = n.kind || n.type;
      if (!k) continue;
      var c = (n.color || '#8AA0C0').toUpperCase();
      if (!firstColor[k]) firstColor[k] = c;
      var m = byKindColor[k] || (byKindColor[k] = {});
      m[c] = (m[c] || 0) + 1;
    }
    var host = document.getElementById('legend');
    var esc = function (s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    };
    var row = function (color, label, count, cls) {
      return '<div class="leg-item' + (cls ? ' ' + cls : '') + '">' +
        '<span class="leg-dot" style="background:' + color + '"></span>' +
        '<span class="leg-label">' + esc(label) + '</span>' +
        (count != null ? '<span class="leg-n">' + count.toLocaleString('en-US') +
          '</span>' : '') + '</div>';
    };
    var PAL = BRAIN.PALETTE;

    // Memory-systems map (where each kind lives in the brain). Each system's
    // swatch uses its representative kind's ACTUAL rendered colour so it
    // matches the nodes on screen — not a separate hand-picked palette.
    var html = '<div class="leg-head">Memory systems → regions</div>';
    (BRAIN.MEMORY_SYSTEMS || []).forEach(function (sys) {
      var swatch = (sys.repKind && firstColor[sys.repKind]) || sys.color;
      html += row(swatch, sys.label);
    });
    html += '<div class="leg-note">Regions registered from MNI atlas centroids ' +
      '(affine fit). Memory depth = heat rank (relative).</div>';

    // Exhaustive node-colour legend — every colour, every kind that renders it.
    html += '<div class="leg-head" style="margin-top:10px">Node colours</div>';
    KIND_ORDER.forEach(function (k) {
      var cmap = byKindColor[k];
      if (!cmap) return;
      var colors = Object.keys(cmap).sort(function (a, b) { return cmap[b] - cmap[a]; });
      var kindLabel = k.replace('_', ' ');
      if (colors.length === 1 || !(PAL && PAL.isGraded(k))) {
        // Single colour (or ungraded kind): one row, kind name + total.
        html += row(colors[0], kindLabel, data.byKind[k]);
        return;
      }
      // Graded kind: header with the kind + total, then one row per colour.
      html += '<div class="leg-subhead">' + esc(kindLabel) +
        '<span class="leg-n">' + (data.byKind[k] || 0).toLocaleString('en-US') +
        '</span></div>';
      colors.forEach(function (c) {
        var sub = (PAL && PAL.labelFor(k, c)) || 'other';
        html += row(c, sub, cmap[c], 'sub');
      });
    });
    host.innerHTML = html;
  }

  function start() {
    setStatus('fetching graph + brain mesh…');
    var onStream = function (c) {
      setStatus('streaming graph — '
        + c.nodes.toLocaleString('en-US')
        + (c.node_total ? '/' + c.node_total.toLocaleString('en-US') : '')
        + ' nodes, ' + c.edges.toLocaleString('en-US') + ' edges…');
    };
    Promise.all([BRAIN.fetchGraph(onStream), BRAIN.loadBrain(MODEL_URL)])
      .then(function (results) {
        var data = results[0];
        var soup = results[1];
        if (!data.nodes.length) throw new Error('graph returned 0 nodes');
        var nodeColors = buildNodeColors(data.nodes);
        setStatus('building the anatomical atlas…');
        var atlas = BRAIN.buildAtlas(soup.box);
        var surface = BRAIN.buildSurface(soup);
        var domainInfo = buildDomainInfo(data, surface);
        setStatus('placing ' + data.nodes.length.toLocaleString('en-US') + ' nodes by memory system…');
        var placed = BRAIN.placeNodes(data.nodes, atlas, surface, domainInfo);
        var positions = placed.positions;
        BRAIN.nodePositions = positions;
        setStatus('weaving the cortical net…');
        BRAIN.buildScaffold(soup);
        BRAIN.buildPoints(data.nodes, positions, nodeColors);
        BRAIN.installDetailBridge(data.nodes, data.edges);
        // The real synapses: every graph edge, cross-region ones bowed along the
        // major white-matter tracts (fornix, uncinate, SLF, corpus callosum) and
        // coloured by its endpoints (galaxy palette).
        setStatus('routing ' + data.edges.length.toLocaleString('en-US') + ' synapses along white-matter tracts…');
        BRAIN.buildEdges(data.edges, positions, BRAIN.indexOfId, nodeColors,
          placed.regionKey, placed.hemi, atlas);
        BRAIN.initPicking(data.nodes);
        fillStats(data);
        fillLegend(data);
        // The detail panel docks over the right edge (400px) — hide the
        // bottom-right legend while it's open so the two never overlap, and
        // restore it on close. The reset button (top-right) is already
        // cleared by the legend's capped max-height.
        var legendEl = document.getElementById('legend');
        if (window.JUG && JUG.on && legendEl) {
          // The stylesheet sets `#legend { display:block !important }`, so the
          // inline hide must ALSO be !important to win; restore by removing it
          // (reverts to the stylesheet's block).
          JUG.on('graph:selectNode', function () {
            legendEl.style.setProperty('display', 'none', 'important');
          });
          JUG.on('graph:deselectNode', function () {
            legendEl.style.removeProperty('display');
          });
        }
        BRAIN.fitView();
        hideLoading();
        document.getElementById('reset-btn').addEventListener('click', BRAIN.fitView);
        console.log('[brain] rendered', data.nodes.length, 'nodes inside the cortex');
      })
      .catch(function (err) {
        console.error('[brain] boot failed', err);
        fail((err && err.message) || String(err));
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
