// Cortex Brain View — boot / composition root.
//
// Loads the graph data and the brain mesh in parallel, places every node
// inside the cortex, builds the point cloud, wires picking, and fills the
// stats + legend chrome. This is the only module that knows about all the
// others; each of them stays single-purpose.

window.BRAIN = window.BRAIN || {};

(function () {
  var MODEL_URL = '/brain/models/brain.glb';

  // ?assoc=0 disables the associative relaxation pass (force_layout.js) for
  // A/B comparison against the pure anatomical placement — default ON.
  BRAIN.ASSOC_RELAX_ON = location.search.indexOf('assoc=0') === -1;

  // User-driven per-kind isolate filter (points.js/edges.js read this
  // directly, same namespace pattern as ASSOC_RELAX_ON). null = NO
  // filtering — every kind renders at full alpha (the required default). Set
  // by clicking a "Node colours" row in the legend (wireLegendFilter, below);
  // never driven by a URL param — this replaced a hardcoded ?focus=memory
  // mode that only ever isolated one hardcoded kind.
  BRAIN.filterKind = null;

  // Colour MEMORY nodes by their associative community (communities.js +
  // force_layout.js's per-community attractor) instead of by consolidation
  // stage. Default true because it directly serves the "distinct
  // communities" goal Change A exists for; entity/symbol/file/etc. colours
  // are never touched by this flag (resolveNodeColor below gates it to
  // kind === 'memory' only).
  BRAIN.COLOR_BY_COMMUNITY = true;

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
    'file', 'discussion', 'memory', 'entity', 'symbol', 'wiki'];

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
    });
  }

  // Ambient-mass neutral fallback token (DS gate G7: opaque --field-point,
  // never a literal hex). CortexPalette.hex() is the primary reader; when the
  // palette script hasn't loaded yet, fall back to reading the CSS custom
  // property straight off the root (surface-toggle.js stamps data-surface
  // before first paint, so the property is already the correct one for the
  // current surface). No literal hex anywhere in this fallback chain.
  function fieldPointHex() {
    if (window.CortexPalette && window.CortexPalette.hex) {
      var v = window.CortexPalette.hex('--field-point');
      if (v) return v;
    }
    var raw = getComputedStyle(document.documentElement).getPropertyValue('--field-point');
    return raw ? raw.trim() : null;
  }

  // Per-node RGB using the galaxy's own colour for each node (node.color carries
  // the kind/heat colour the unified graph renders), so the brain is
  // differentiated by node type exactly like the galaxy. Float32Array(3*N),
  // shared by the point cloud AND the edge web (edges gradient between their
  // endpoints' colours). Falls back to the neutral --field-point token (never
  // a literal hex) when neither JUG nor the node's own colour resolves.
  // Memory nodes: when COLOR_BY_COMMUNITY is on and BRAIN.communities has
  // been computed (start(), before this is first called), colour from the
  // node's associative community instead of the galaxy's per-kind colour.
  // Every other kind is untouched — falls straight through to the existing
  // JUG/galaxy resolution below.
  function resolveNodeColor(n) {
    if (BRAIN.COLOR_BY_COMMUNITY && BRAIN.communities && (n.kind || n.type) === 'memory') {
      var cid = BRAIN.communities.communityOf.get(n.id);
      // Only DISTINCT communities (>= BRAIN.MIN_COMMUNITY_SIZE) get a hue —
      // same gate force_layout.js uses for attractors, so colour and position
      // agree: a memory in a tiny/singleton community keeps the default
      // per-kind colour and stays at its anatomical anchor.
      var big = cid != null && BRAIN.communities.sizes &&
        (BRAIN.communities.sizes.get(cid) || 0) >= (BRAIN.MIN_COMMUNITY_SIZE || 1);
      if (big && BRAIN.PALETTE && BRAIN.PALETTE.communityColor) {
        return BRAIN.PALETTE.communityColor(cid);
      }
    }
    return (window.JUG && JUG.getNodeColor) ? JUG.getNodeColor(n) : ((n && n.color) || fieldPointHex());
  }

  function buildNodeColors(nodes) {
    var arr = new Float32Array(nodes.length * 3);
    var c = new THREE.Color();
    var fallback = fieldPointHex();
    for (var i = 0; i < nodes.length; i++) {
      var hex = resolveNodeColor(nodes[i]) || fallback;
      // hex may still be null only if CortexPalette AND getComputedStyle both
      // failed to resolve the token (broken page load) — skip .set() rather
      // than baking a literal, leaving THREE.Color's own default.
      if (hex) { try { c.set(hex); } catch (e) { if (fallback) c.set(fallback); } }
      arr[i * 3] = c.r; arr[i * 3 + 1] = c.g; arr[i * 3 + 2] = c.b;
    }
    return arr;
  }

  // Re-ink the node cloud on a surface toggle. JUG._tok (unified/js/config.js)
  // is rehydrated from CortexPalette on every cortex:surface-change, so
  // re-running resolveNodeColor picks up the new surface's tones automatically
  // — this only rebuilds the colour buffer and re-uploads it; it never touches
  // the position buffer, so no node moves and no simulation is involved
  // (positions are placed once in start(), below, and never recomputed here).
  var lastNodes = null;
  function repaintNodeColors() {
    if (!lastNodes || !BRAIN.points) return;
    var colors = buildNodeColors(lastNodes);
    var attr = BRAIN.points.geometry.getAttribute('ncolor');
    if (!attr) return;
    attr.array.set(colors);
    attr.needsUpdate = true;
  }
  window.addEventListener('cortex:surface-change', repaintNodeColors);

  // Reverse a resolved hex back to the live JUG._tok CATEGORY KEY it came
  // from ('hub' / 'info' / 'episodic' / 'semantic' / …) so a legend swatch
  // can be tagged with a stable TOKEN NAME instead of a hex frozen at build
  // time. JUG._tok is re-hydrated from CortexPalette on every
  // cortex:surface-change (unified/js/config.js), so re-reading it through
  // the category name is how the legend re-inks without touching node data
  // again. Falls back to the literal hex (no re-ink) only if JUG._tok is
  // unavailable — same accepted-risk shape as resolveNodeColor's fallback.
  function colorToCategory(hex) {
    if (!window.JUG || !JUG._tok || hex == null) return hex;
    var norm = String(hex).toUpperCase();
    for (var key in JUG._tok) {
      if (String(JUG._tok[key]).toUpperCase() === norm) return key;
    }
    return hex;
  }

  // category -> live hex. Unknown categories (the colorToCategory literal-hex
  // fallback path) pass through unchanged.
  function categoryHex(cat) {
    if (window.JUG && JUG._tok && JUG._tok[cat] != null) return JUG._tok[cat];
    return cat;
  }

  // Re-paint every tagged legend dot from its category — called once after
  // fillLegend builds the DOM and again on cortex:surface-change. The dots
  // carry no baked hex; `data-color-cat` is the only state.
  function paintLegendDots() {
    var dots = document.querySelectorAll('#legend .leg-dot[data-color-cat]');
    for (var i = 0; i < dots.length; i++) {
      dots[i].style.background = categoryHex(dots[i].getAttribute('data-color-cat'));
    }
  }
  window.addEventListener('cortex:surface-change', paintLegendDots);

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

  // EXHAUSTIVE legend: every distinct sub-kind a kind renders gets its own
  // labelled row. Colour is semantic (memory→consolidation stage, entity/
  // symbol→type, file→primary tool), so a single swatch per kind would hide
  // most of what's on screen. Sub-kinds resolve from the node's own canonical
  // metadata (entityType / symbol_type / stage — the values the server bakes
  // the colour FROM) via BRAIN.PALETTE.subLabelFor; the colour reverse-lookup
  // inside it is only a fallback for old payloads. Grouping by LABEL (not by
  // raw hex) means a re-inked server palette can never lump distinct kinds
  // into "other" again (root fix, user report 2026-07-04). Each row's swatch
  // is the most common colour actually rendered for that sub-kind.
  function fillLegend(data) {
    var PAL = BRAIN.PALETTE;
    // kind -> { categoryKey -> count }, and first category per kind. The
    // category comes from resolveNodeColor() reversed through
    // colorToCategory() — the SAME resolution buildNodeColors used to paint
    // the actual point cloud, so a legend swatch never shows a colour
    // nothing on screen is wearing (root cause: this used to read the raw,
    // per-sub-kind n.color the SERVER bakes, which JUG.getNodeColor collapses
    // into a few DD-04 categories for the rendered points — the two drifted).
    var byKindCat = {};
    var firstCat = {};
    // kind -> { subLabel -> { n: count, cats: { categoryKey -> count } } }
    var byKindLabel = {};
    for (var i = 0; i < data.nodes.length; i++) {
      var n = data.nodes[i];
      var k = n.kind || n.type;
      if (!k) continue;
      var cat = colorToCategory(resolveNodeColor(n));
      if (!firstCat[k]) firstCat[k] = cat;
      var m = byKindCat[k] || (byKindCat[k] = {});
      m[cat] = (m[cat] || 0) + 1;
      if (PAL && PAL.isGraded(k)) {
        var sub = PAL.subLabelFor(n) || 'other';
        var lm = byKindLabel[k] || (byKindLabel[k] = {});
        var rec = lm[sub] || (lm[sub] = { n: 0, cats: {} });
        rec.n += 1;
        rec.cats[cat] = (rec.cats[cat] || 0) + 1;
      }
    }
    var host = document.getElementById('legend');
    var esc = function (s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    };
    // Dots carry a category TAG, not a baked colour — paintLegendDots()
    // resolves the actual hex immediately after and again on every
    // cortex:surface-change. `kindKey`, when given, makes the row a
    // keyboard-and-click filter toggle (wireLegendFilter, below) — used only
    // on kind-level rows/headers, never on per-sub-kind rows, since the
    // filter isolates a whole kind, not a sub-label.
    var row = function (cat, label, count, cls, kindKey) {
      var kindAttrs = kindKey
        ? ' data-kind="' + esc(kindKey) + '" tabindex="0" role="button" aria-pressed="false"'
        : '';
      var kindCls = kindKey ? ' leg-clickable' : '';
      return '<div class="leg-item' + (cls ? ' ' + cls : '') + kindCls + '"' + kindAttrs + '>' +
        '<span class="leg-dot" data-color-cat="' + esc(cat) + '"></span>' +
        '<span class="leg-label">' + esc(label) + '</span>' +
        (count != null ? '<span class="leg-n">' + count.toLocaleString('en-US') +
          '</span>' : '') + '</div>';
    };
    // Memory-systems map (where each kind lives in the brain). Each system's
    // swatch uses its representative kind's ACTUAL rendered colour category
    // so it matches the nodes on screen — not a separate hand-picked palette.
    // sys.colorCat (anatomy.js) is only reached when the graph has zero
    // rendered nodes of that repKind.
    var html = '<div class="leg-head">Memory systems → regions</div>';
    (BRAIN.MEMORY_SYSTEMS || []).forEach(function (sys) {
      var cat = (sys.repKind && firstCat[sys.repKind]) || sys.colorCat || 'info';
      html += row(cat, sys.label);
    });
    html += '<div class="leg-note">Regions registered from MNI atlas centroids ' +
      '(affine fit). Memory depth = heat rank (relative).</div>';

    // Exhaustive node legend — every sub-kind, every kind that renders it.
    // Kind-level rows/headers are click-filterable ("Node colours row →
    // isolate this kind"); text hint added once as a leg-note below.
    html += '<div class="leg-head" style="margin-top:10px">Node colours ' +
      '<span style="text-transform:none;letter-spacing:normal">(click to isolate)</span></div>';
    KIND_ORDER.forEach(function (k) {
      var cmap = byKindCat[k];
      if (!cmap) return;
      var kindLabel = k.replace('_', ' ');
      // Memory, coloured by association community (Change A): the per-stage
      // breakdown below would show one essentially-random community hue per
      // stage-label row, which is noise, not signal — a single summary row
      // instead. Falls through to the normal per-stage breakdown when
      // COLOR_BY_COMMUNITY is off.
      if (k === 'memory' && BRAIN.COLOR_BY_COMMUNITY && BRAIN.communities) {
        html += row('info', 'memory — coloured by association community', data.byKind[k], null, k);
        html += '<div class="leg-note">' + BRAIN.communities.count.toLocaleString('en-US') +
          ' communities detected (Leiden + CPM over co-entity associations, server-side).</div>';
        return;
      }
      var cats = Object.keys(cmap).sort(function (a, b) { return cmap[b] - cmap[a]; });
      var lmap = byKindLabel[k];
      var labels = lmap ? Object.keys(lmap).sort(function (a, b) {
        return lmap[b].n - lmap[a].n;
      }) : [];
      if (labels.length <= 1) {
        // Single sub-kind (or ungraded kind): one row, kind name + total.
        html += row(cats[0], kindLabel, data.byKind[k], null, k);
        return;
      }
      // Graded kind: header with the kind + total, then one row per sub-kind
      // (count-descending), swatched with its most common rendered category.
      // The header itself (not the sub-rows) is the click-filter target.
      html += '<div class="leg-subhead leg-clickable" data-kind="' + esc(k) +
        '" tabindex="0" role="button" aria-pressed="false">' + esc(kindLabel) +
        '<span class="leg-n">' + (data.byKind[k] || 0).toLocaleString('en-US') +
        '</span></div>';
      labels.forEach(function (lab) {
        var rec = lmap[lab];
        var cat = Object.keys(rec.cats).sort(function (a, b) {
          return rec.cats[b] - rec.cats[a];
        })[0];
        html += row(cat, lab, rec.n, 'sub');
      });
    });
    host.innerHTML = html;
    paintLegendDots();
    wireLegendFilter(host);
    updateLegendActiveState();
  }

  // Sets BRAIN.filterKind (toggle off if re-clicking the active kind), then
  // asks points.js/edges.js to re-derive alpha/size from it — cheap
  // attribute repaint, no position/geometry rebuild (same shape as
  // repaintNodeColors above).
  function toggleFilterKind(kind) {
    BRAIN.filterKind = (BRAIN.filterKind === kind) ? null : kind;
    if (BRAIN.repaintPointFilter) BRAIN.repaintPointFilter();
    if (BRAIN.repaintEdgeFilter) BRAIN.repaintEdgeFilter();
    updateLegendActiveState();
  }
  // Exposed so search.js can clear a blocking legend filter before flying to
  // a result the filter would otherwise render invisible — same mechanism
  // the legend's own click/keyboard handlers use, not a re-derived copy.
  BRAIN.toggleFilterKind = toggleFilterKind;

  function updateLegendActiveState() {
    var rows = document.querySelectorAll('#legend [data-kind]');
    for (var i = 0; i < rows.length; i++) {
      var active = rows[i].getAttribute('data-kind') === BRAIN.filterKind;
      rows[i].classList.toggle('leg-active', active);
      rows[i].setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }

  // Delegated click + keyboard (Enter/Space) handling on the legend host —
  // wired once (legendWired guard) since fillLegend only rebuilds #legend's
  // innerHTML on the initial load in this view, but the guard makes a future
  // re-render safe without double-firing. WCAG 2.1 AA: every filter row is a
  // real keyboard target (tabindex + role=button + Enter/Space activation),
  // not a click-only div.
  var legendWired = false;
  function wireLegendFilter(host) {
    if (legendWired) return;
    legendWired = true;
    host.addEventListener('click', function (ev) {
      var el = ev.target.closest ? ev.target.closest('[data-kind]') : null;
      if (el) toggleFilterKind(el.getAttribute('data-kind'));
    });
    host.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ' && ev.key !== 'Spacebar') return;
      var el = ev.target.closest ? ev.target.closest('[data-kind]') : null;
      if (!el) return;
      ev.preventDefault();
      toggleFilterKind(el.getAttribute('data-kind'));
    });
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
        lastNodes = data.nodes;
        // Feed the search worker once the full node set is final (id/label/
        // path/kind only — search.js builds its own trigram index off this).
        // Guarded: the page must still boot if search.js failed to load.
        if (BRAIN.searchInit) BRAIN.searchInit(data.nodes);
        // indexOfId (id -> row in `positions`/`data.nodes`) is normally built
        // by installDetailBridge, which runs after buildPoints. Community
        // detection AND colouring both need it earlier — build the same
        // ordinal map (nodes[] order == positions row order, per placeNodes)
        // right after fetch. installDetailBridge rebuilds an identical Map
        // later; harmless. Per-row kind lookup (BRAIN.nodeKindByRow) rides
        // along the same pass — edges.js's filter repaint needs it to test
        // an edge's endpoint kinds without re-touching data.nodes each time.
        var ordinal = new Map();
        var kindByRow = new Array(data.nodes.length);
        for (var oi = 0; oi < data.nodes.length; oi++) {
          ordinal.set(data.nodes[oi].id, oi);
          kindByRow[oi] = data.nodes[oi].kind || data.nodes[oi].type;
        }
        BRAIN.indexOfId = ordinal;
        BRAIN.nodeKindByRow = kindByRow;
        if (BRAIN.detectCommunities) {
          setStatus('detecting associative communities…');
          BRAIN.communities = BRAIN.detectCommunities(data.nodes, data.edges, BRAIN.indexOfId);
          console.log('[brain] associative communities:', BRAIN.communities.count);
        }
        // buildNodeColors reads BRAIN.communities via resolveNodeColor, so it
        // must run AFTER community detection above.
        var nodeColors = buildNodeColors(data.nodes);
        setStatus('building the anatomical atlas…');
        var atlas = BRAIN.buildAtlas(soup.box);
        var surface = BRAIN.buildSurface(soup);
        var domainInfo = buildDomainInfo(data, surface);
        setStatus('placing ' + data.nodes.length.toLocaleString('en-US') + ' nodes by memory system…');
        var placed = BRAIN.placeNodes(data.nodes, atlas, surface, domainInfo);
        var positions = placed.positions;
        if (BRAIN.ASSOC_RELAX_ON && BRAIN.relaxAssociative) {
          setStatus('relaxing associative memory clusters…');
          var relaxStats = BRAIN.relaxAssociative(data.nodes, positions, data.edges, BRAIN.indexOfId, surface,
            { communities: BRAIN.communities });
          console.log('[brain] associative relax:', relaxStats);
        }
        BRAIN.nodePositions = positions;
        // No cortical scaffold net: the opaque ink shell (brain_mesh.js) already
        // carries the anatomical form; a knn net of surface vertices read as a
        // blue wireframe under the membrane that buried the data cloud (user
        // report 2026-07-07) — textbook-plate = clean silhouette + data over it.
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
