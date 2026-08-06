// Cortex — Workflow Graph orchestration + D3 force wiring.
// Schema: mcp_server/core/workflow_graph_schema.py. Constants, LOD, tokens,
// slots, topology and renderers live in the workflow_graph_* sibling modules,
// loaded before this public renderWorkflowGraph API.
(function () {
  var C = window.JUG._wfgConst;
  var _wfg = window.JUG._wfg;
  var KIND_RADIUS = C.KIND_RADIUS;
  var KIND_COLOR = _wfg.KIND_COLOR;
  var wfgLodTier = _wfg.lodTier;
  var wfgEdgeCoverage = _wfg.edgeCoverage;
  var prepareTopology = _wfg.prepareTopology;
  var _forces = _wfg.forces;
  var linkDistance = _forces.linkDistance;
  var linkStrength = _forces.linkStrength;
  var chargeStrength = _forces.chargeStrength;
  var slotForce = _forces.slotForce;
  var symbolMultiCenterForce = _forces.symbolMultiCenterForce;
  var interDomainRepelForce = _forces.interDomainRepelForce;
  var collisionRadius = _forces.collisionRadius;

  var D3_URL = 'https://cdn.jsdelivr.net/npm/d3@7.8.5/dist/d3.min.js';
  // Always render via canvas. SVG path (mountSVG) cannot grow
  // incrementally — its d3-enter/exit selections are bound once at
  // mount time, so calling handle.append() later would never paint
  // new circles. Canvas reads ctx.nodes/ctx.edges every frame, so
  // pushing into those arrays is enough for the new nodes to show.
  // The visual difference at small N is negligible; ergonomics of
  // a unified renderer path are worth the trade.
  var CANVAS_THRESHOLD = 0;

  function ensureD3(cb) {
    if (window.d3 && window.d3.forceSimulation) return cb();
    var existing = document.querySelector('script[data-cortex-d3]');
    if (existing) { existing.addEventListener('load', cb); return; }
    var s = document.createElement('script');
    s.src = D3_URL; s.async = true; s.defer = true;
    s.setAttribute('data-cortex-d3', '1');
    s.onload = cb;
    s.onerror = function () { console.error('[cortex] failed to load d3 from ' + D3_URL); };
    document.head.appendChild(s);
  }

  function renderWorkflowGraph(container, data) {
    if (!container) throw new Error('renderWorkflowGraph: container required');
    container.innerHTML = '';
    var handle = { destroy: function () {}, select: function () {},
                   data: data, append: function () { return { addedNodes: 0, addedEdges: 0 }; } };
    // The labelled force graph is the default. The label-less tilemap is an
    // explicit raw-scale inspection mode and fetches /api/quadtree itself.
    var qs = (window.location && window.location.search) || '';
    var wantForce = qs.indexOf('viz=force') !== -1;
    var wantTilemap = qs.indexOf('viz=tilemap') !== -1;
    // Force always wins if both query flags are present.
    var useTilemap = wantTilemap && !wantForce;
    if (useTilemap
        && window.JUG && typeof window.JUG.mountTilemap === 'function') {
      var p = window.JUG.mountTilemap(container);
      Promise.resolve(p).then(function (impl) {
        if (impl && impl.destroy) handle.destroy = impl.destroy;
      });
      return handle;
    }
    ensureD3(function () {
      var impl = mount(container, data || { nodes: [], edges: [] });
      handle.destroy = impl.destroy;
      handle.select = impl.select;
      handle.append = impl.append;
    });
    return handle;
  }

  // Deterministic string → [0,1) hash (FNV-1a). Used for stable per-node
  // jitter so re-mounts reproduce the identical layout (no re-shuffle).
  function _hash01(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return (h >>> 0) / 4294967296;
  }

  function mount(container, data) {
    var d3 = window.d3;
    var wfg = window.JUG._wfg;
    var nodes = (data.nodes || []).map(function (n) {
      var c = Object.assign({}, n);
      // Strip server-provided world coords. /api/graph/full ships x/y in
      // [-1,1] world space (domain hubs at 0,0), which clones EVERY node onto
      // a ~2px pile at the screen origin — the force sim then cannot fan 64k+
      // nodes out and collapses into an unreadable ball. v3.14.1 had NO server
      // coords: d3 seeds a phyllotaxis spread and slotForce fans nodes to
      // their deterministic radial-shell slots, which scaled cleanly to 338k+
      // nodes. The client slot layout owns positioning, not the server.
      delete c.x; delete c.y; delete c.vx; delete c.vy; delete c.fx; delete c.fy;
      return c;
    });
    // For very large graphs (>15k nodes) skip the simulation-visible
    // edges entirely — symbol→file/symbol→symbol edges number in the
    // tens of thousands and d3.forceLink on that many pairs freezes
    // the main thread. The slot layout already encodes containment
    // geometrically, so the visual edge of every symbol→file pair is
    // redundant. Keep only structural edges (domain hubs, tools,
    // files ↔ tools, discussions ↔ files, memories) for rendering.
    var _lod = wfgLodTier(nodes.length);
    var HEAVY = _lod.heavy;
    var _nidSet = {};
    for (var _ni = 0; _ni < nodes.length; _ni++) _nidSet[nodes[_ni].id] = 1;
    // Keep AST edges in the simulation — they carry real semantic
    // meaning (symbol contained in file, symbol calls another symbol,
    // file imports symbol, method belongs to class). Layout should
    // REFLECT this connectivity, not randomize it. Only drop the
    // really dense symbol↔symbol edges (`calls`) under extreme load
    // to keep tick-rate manageable.
    var EXTREME = _lod.extreme;
    // Coverage honesty (issue #36): the edge reduction AND its accounting come
    // from the pure wfgEdgeCoverage seam so the collapsed magnitude the
    // indicator DISPLAYS (criterion 3) is computed by tested logic, not
    // inferred from a thinner picture. `_cov.rendered` is the surviving raw
    // edge set (LOD `calls`-drop + dangling prune already applied).
    var _cov = wfgEdgeCoverage(data.edges || [], _nidSet, EXTREME);
    var renderedEdges = _cov.rendered;
    var edges = renderedEdges.map(function (e) {
      return Object.assign({}, e, {
        source: typeof e.source === 'object' ? e.source.id : e.source,
        target: typeof e.target === 'object' ? e.target.id : e.target,
      });
    });
    var width  = container.clientWidth  || window.innerWidth;
    var height = container.clientHeight || window.innerHeight;

    // Report the ACTUAL rendered topology so the HUD legend matches the canvas.
    // The legend used to read JUG.state.lastData, which accumulates every node
    // ever appended and is never pruned — so after a view switch it shows the
    // prior view's count (galaxy canvas, trace count) and over-reports edges the
    // renderer filtered (EXTREME `calls` + dangling). These are the exact node
    // and edge arrays this render draws, with the same kind breakdown polling.js
    // needs (entities = nodes − domain − memory − discussion).
    var _rc = { nodes: nodes.length, edges: edges.length,
                domain: 0, memory: 0, discussion: 0,
                // Coverage-honesty fields (issue #36) — additive; polling.js
                // reads only the counts above. lodTier + droppedEdges let the
                // coverage indicator name the client-side collapse.
                lodTier: _lod,
                droppedEdges: _cov.droppedEdges,
                danglingEdges: _cov.danglingEdges };
    for (var _rci = 0; _rci < nodes.length; _rci++) {
      var _rk = nodes[_rci].kind || nodes[_rci].type || '';
      if (_rk === 'domain') _rc.domain++;
      else if (_rk === 'memory') _rc.memory++;
      else if (_rk === 'discussion') _rc.discussion++;
    }
    if (window.JUG) window.JUG.__wfgRendered = _rc;

    // Topology prep uses the FULL edge set (parent-file map needs
    // `defined_in` edges) but the simulation only sees the rendered set.
    var ctx = prepareTopology(nodes, data.edges || [], width, height);
    ctx.edges = edges;                // simulation edges (possibly filtered)
    ctx.KIND_RADIUS = KIND_RADIUS;
    ctx.KIND_COLOR  = KIND_COLOR;
    // HEAVY: pin symbols at their slot positions so d3 treats them as
    // immovable anchors (skip charge, skip link, skip collide for
    // pinned nodes). The layout is already deterministic via slotOf;
    // simulating 10k+ symbols adds no visual value, only CPU cost.
    // Seed symbols ALONG THE OUTWARD RAY from the domain hub through
    // their parent file, at a random distance past the file. This is
    // the starting configuration that lets symbols flow naturally
    // into the inter-domain gap space rather than orbiting the hub.
    for (var pi = 0; pi < nodes.length; pi++) {
      var pn = nodes[pi];
      if (pn.kind !== 'symbol') continue;
      var dId = ctx.domainOf[pn.id] || 'domain:__global__';
      var anc = ctx.anchors[dId] || ctx.anchors['domain:__global__'];
      var pfId = ctx.parentFile[pn.id];
      var fileSlot = pfId ? ctx.slotOf[pfId] : null;
      if (!anc) continue;
      var origin = fileSlot || anc;
      // Outward unit vector from domain anchor → origin.
      var dx = origin.x - anc.x, dy = origin.y - anc.y;
      var d = Math.hypot(dx, dy);
      var ox, oy;
      if (d < 1) {
        // Fallback: pseudo-random outward ray.
        var t = (pi * 0.37) % (Math.PI * 2);
        ox = Math.cos(t); oy = Math.sin(t);
      } else {
        ox = dx / d; oy = dy / d;
      }
      // DETERMINISTIC jitter keyed on the symbol id (not Math.random): a
      // re-mount (every live activity append re-runs mount()) must reproduce
      // the IDENTICAL layout, or the whole galaxy re-shuffles on every
      // streamed action. Two independent hashes → distance + angle.
      var _h1 = _hash01(pn.id), _h2 = _hash01(pn.id + '~a');
      var pastFile = 30 + _h1 * 120;  // 30..150 px past file
      var angJitter = (_h2 - 0.5) * 0.15;  // ±4° lateral spread
      var cs = Math.cos(angJitter), sn = Math.sin(angJitter);
      var rx = ox * cs - oy * sn;
      var ry = ox * sn + oy * cs;
      pn.x = origin.x + rx * pastFile;
      pn.y = origin.y + ry * pastFile;
    }
    // Node-detail panel is owned exclusively by #detail-panel
    // (detail_panel.js), driven via the graph:selectNode bus event the
    // renderers emit. No second side panel is built here.

    // Maxwell-damped config: see ADR-0047 for the full tuning rationale
    // (Thompson scaling audit on the Gap 10 N≈17k → N≈27k jump).
    //  * alphaDecay HEAVY: 0.028 → 0.018  (repulsive energy ∝ N²)
    //  * velocityDecay: 0.72 → 0.78       (ζ recovered to ~0.65)
    // Other force constants unchanged — slots from computeSlots carry
    // the positioning burden; physics just needs time to converge.
    var slotK    = HEAVY ? 1.2  : 0.85;
    var chargeEn = true;
    var collideI = HEAVY ? 2    : 3;
    var alphaDK  = HEAVY ? 0.018 : 0.022;
    var velDecay = 0.78;

    var sim = d3.forceSimulation(nodes)
      .alpha(1.0).alphaDecay(alphaDK).velocityDecay(velDecay)
      .force('link', d3.forceLink(edges).id(function (n) { return n.id; })
        .distance(linkDistance).strength(linkStrength))
      .force('slot',        slotForce(ctx, slotK))
      .force('interdomain', interDomainRepelForce(ctx, 0.08))
      .force('symmulti', symbolMultiCenterForce(ctx))
      .force('collide', d3.forceCollide()
        .radius(function (n) { return collisionRadius(n, ctx); })
        .strength(0.92).iterations(collideI));
    if (chargeEn) {
      // Local charge (distanceMax 180) so symbol-symbol repulsion
      // doesn't create long-range feedback with the multi-centroid
      // attraction; domains still repel each other via interdomain.
      sim.force('charge', d3.forceManyBody().strength(chargeStrength).distanceMax(180));
    }

    // LARGE galaxy: place every node AT its deterministic slot before the sim
    // runs, so it STARTS at the target radial-shell layout (the v3.14.1 look)
    // instead of cold-starting from a phyllotaxis spread and easing 65k nodes
    // in over ~3.5 s while redrawing all of them each tick. Then decay fast —
    // the sim only does a brief collide de-overlap and stops. Net effect: the
    // initial paint AND every live-activity re-mount render the structured
    // galaxy almost immediately, so the graph stays FLUID. Symbols keep the
    // outward-ray pre-seed assigned above; everything else snaps to slotOf.
    if (_lod.snapToSlots || ctx.isTrace) {
      for (var sp = 0; sp < nodes.length; sp++) {
        var spn = nodes[sp];
        var sps = ctx.slotOf[spn.id];
        if (sps) {
          spn.x = sps.x; spn.y = sps.y;
          if (ctx.isTrace) { spn.fx = spn.x; spn.fy = spn.y; }
        }
      }
      if (ctx.isTrace) sim.stop();
      else sim.alphaDecay(0.12);  // ~50 ticks to a brief de-overlap, then halt
    }

    var useCanvas = nodes.length > CANVAS_THRESHOLD;
    var renderer = useCanvas
      ? wfg.mountCanvas(container, ctx, sim, width, height)
      : wfg.mountSVG(container, ctx, sim, width, height);
    // G2: register this instance as the repaint target for the surface
    // toggle (see the 'cortex:surface-change' listener in the tokens module).
    _wfg.setActiveRenderer(renderer);
    // Debug/verification hook (read-only reference, same pattern as the
    // existing __wfgRendered above) — lets an external probe read live node
    // positions (ctx.nodes[i].x/y) to confirm a surface-change repaint moved
    // zero pixels. No behavior depends on this; it is never read internally.
    if (window.JUG) window.JUG.__wfgCtx = ctx;

    function onResize() {
      var w = container.clientWidth || window.innerWidth;
      var h = container.clientHeight || window.innerHeight;
      renderer.resize(w, h);
      sim.alpha(0.3).restart();
    }
    window.addEventListener('resize', onResize);
    // Incremental append mutates the live arrays. Galaxy batches keep the
    // throttled force path; Trace asks for a topology refresh because its
    // session disks and selection adjacency depend on every newly loaded edge.
    function append(newNodes, newEdges, options) {
      newNodes = newNodes || [];
      newEdges = newEdges || [];
      var addedN = 0, addedE = 0;
      var traceTopology = !!(options && options.topologyAware && ctx.isTrace);
      var oldPos = {}, oldSlots = ctx.slotOf, addedIds = [];
      if (traceTopology) nodes.forEach(function (n) {
        oldPos[n.id] = { x: n.x, y: n.y };
      });
      // Numeric fallbacks prevent d3's NaN -> phyllotaxis reset.
      function _finite(v, fallback) {
        return (typeof v === 'number' && isFinite(v)) ? v : fallback;
      }
      var cx = _finite(ctx.cx, _finite(ctx.width / 2,
                _finite(window.innerWidth / 2, 600)));
      var cy = _finite(ctx.cy, _finite(ctx.height / 2,
                _finite(window.innerHeight / 2, 400)));
      var anchorList = [];
      for (var dk in ctx.anchors) {
        var av = ctx.anchors[dk];
        if (av && isFinite(av.x) && isFinite(av.y)) anchorList.push(av);
      }
      if (anchorList.length === 0) anchorList.push({ x: cx, y: cy });
      for (var i = 0; i < newNodes.length; i++) {
        var n = newNodes[i];
        if (!n || n.id == null) continue;
        var live = ctx.byId[n.id];
        if (live) {
          var enriched = false; for (var field in n) if (Object.prototype.hasOwnProperty.call(n, field) && n[field] != null && live[field] == null) { live[field] = n[field]; enriched = true; }
          if (enriched && window.JUG && typeof JUG.emit === 'function') JUG.emit('graph:nodeUpdated', live); continue;
        }
        var n2 = Object.assign({}, n);
        var didCandidates = [
          n2.domain_id,
          n2.domain && ctx.byId[n2.domain] && ctx.byId[n2.domain].kind === 'domain' ? n2.domain : null,
          n2.domain ? 'domain:' + n2.domain : null,
          n2.domain ? 'domain:' + String(n2.domain).toLowerCase() : null,
        ];
        var did = null;
        var anc = null;
        for (var c = 0; c < didCandidates.length; c++) {
          var cand = didCandidates[c];
          if (cand && ctx.anchors[cand]
              && isFinite(ctx.anchors[cand].x)
              && isFinite(ctx.anchors[cand].y)) {
            did = cand;
            anc = ctx.anchors[cand];
            break;
          }
        }
        if (!anc) {
          anc = anchorList[(Math.random() * anchorList.length) | 0];
          did = 'domain:__global__';
        }
        ctx.domainOf[n2.id] = did;
        var angle = Math.random() * Math.PI * 2;
        var rr = 30 + Math.random() * 100;
        var nx = anc.x + Math.cos(angle) * rr;
        var ny = anc.y + Math.sin(angle) * rr;
        if (!isFinite(nx) || !isFinite(ny)) {
          nx = cx + (Math.random() - 0.5) * 60;
          ny = cy + (Math.random() - 0.5) * 60;
        }
        n2.x = nx;
        n2.y = ny;
        nodes.push(n2);
        ctx.byId[n2.id] = n2;
        addedIds.push(n2.id);
        addedN++; _rc.nodes++;
        var rkind = n2.kind || n2.type || '';
        if (rkind === 'domain') _rc.domain++;
        else if (rkind === 'memory') _rc.memory++;
        else if (rkind === 'discussion') _rc.discussion++;
      }
      for (var j = 0; j < newEdges.length; j++) {
        var e = newEdges[j];
        if (!e) continue;
        var s = (e.source && e.source.id) || e.source;
        var t = (e.target && e.target.id) || e.target;
        if (!ctx.byId[s] || !ctx.byId[t]) continue;
        var e2 = Object.assign({}, e, { source: s, target: t });
        var sd = ctx.domainOf[s], td = ctx.domainOf[t];
        e2._crossDomain = !!(sd && td && sd !== td);
        edges.push(e2);
        addedE++; _rc.edges++;
      }
      if (addedN || addedE) {
        if (traceTopology) {
          // Refresh topology maps in place; the canvas and camera stay mounted.
          var topoEdges = edges.map(function (te) {
            return Object.assign({}, te, {
              source: (te.source && te.source.id) || te.source,
              target: (te.target && te.target.id) || te.target,
            });
          });
          var fresh = prepareTopology(nodes, topoEdges, ctx.width, ctx.height);
          ['byId', 'domains', 'anchors', 'domainOf', 'primaryHub', 'parentFile',
            'degree', 'adj', 'slotOf', 'isTrace', 'shells', 'sideShells',
            'cx', 'cy', 'baseR', 'width', 'height'].forEach(function (key) {
            ctx[key] = fresh[key];
          });
          // Preserve world positions; canonical slots animate without remount/camera change.
          nodes.forEach(function (tn) {
            if (oldPos[tn.id]) {
              tn.x = oldPos[tn.id].x; tn.y = oldPos[tn.id].y;
              tn.vx = 0; tn.vy = 0; tn.fx = tn.x; tn.fy = tn.y;
            }
          });
          var radiusOf = function (tn) { return collisionRadius(tn, ctx); };
          var local = _wfg.packTraceExpansion(ctx, topoEdges, addedIds,
                                              newEdges, oldSlots);
          Object.keys(local.targets).forEach(function (id) {
            var target = local.targets[id];
            ctx.slotOf[id] = { x: target.x, y: target.y };
          });
          nodes.forEach(function (tn) {
            if (!local.moving[tn.id]) { tn.fx = tn.x; tn.fy = tn.y; return; }
            var root = ctx.byId[local.anchorOf[tn.id]];
            if (!oldPos[tn.id] && root) {
              var angle = _hash01(String(tn.id)) * Math.PI * 2;
              var distance = radiusOf(root) + radiusOf(tn);
              tn.x = root.x + Math.cos(angle) * distance;
              tn.y = root.y + Math.sin(angle) * distance;
            }
            tn.vx = 0; tn.vy = 0;
            // Domain anchors stay fixed; only a changed canonical slot yields.
            tn.fx = tn.kind === 'domain' ? tn.x : null;
            tn.fy = tn.kind === 'domain' ? tn.y : null;
          });
          edges.forEach(function (te) {
            var s = (te.source && te.source.id) || te.source;
            var t = (te.target && te.target.id) || te.target;
            te._crossDomain = !!(ctx.domainOf[s] && ctx.domainOf[t]
                                  && ctx.domainOf[s] !== ctx.domainOf[t]);
          });
          sim.nodes(nodes);
          sim.force('link').links(edges);
          sim.force('charge', null);
          sim.force('interdomain', null);
          sim.force('symmulti', null);
          sim.on('end.traceAppend', null);
          sim.alpha(0.3); sim.stop();
          // One continuous interpolation replaces the former late end-event snap.
          _wfg.animateTraceExpansion(ctx, local, sim, renderer,
                                    window.requestAnimationFrame.bind(window));
          if (renderer.redrawNow) renderer.redrawNow();
          else if (renderer.redraw) renderer.redraw();
          return { addedNodes: addedN, addedEdges: addedE,
                   totalNodes: nodes.length, totalEdges: edges.length };
        }
        sim.nodes(nodes);
        sim.force('link').links(edges);
        // Throttled Galaxy reheat lets alpha decay between streaming waves.
        var now = (window.performance && performance.now()) || Date.now();
        var sinceLast = now - (sim._lastReheatAt || 0);
        var bump = sinceLast < 250 ? 0.03 : 0.15;
        if (sim.alpha() < bump) sim.alpha(bump);
        sim.restart();
        sim._lastReheatAt = now;
        if (sim._idleTimer) clearTimeout(sim._idleTimer);
        sim._idleTimer = setTimeout(function () {
          sim._idleTimer = null;
          sim.stop();
        }, 3000);
      }
      return { addedNodes: addedN, addedEdges: addedE,
               totalNodes: nodes.length, totalEdges: edges.length };
    }

    // Pin the seed after settling so later high-volume appends cannot push it.
    //
    // Pinning fires when alpha first drops below 0.08 (visually
    // settled — see Maxwell-damped ADR-0047) OR after 3.5 s wall-
    // clock, whichever comes first. The alpha condition cannot use
    // sim.alphaMin() because the throttled appends keep nudging
    // alpha above the floor; we need a higher threshold that's
    // reached during the seed's natural decay.
    var _pinStartedAt = (window.performance && performance.now()) || Date.now();
    function _pinSettledNodes() {
      if (sim._pinDone) return;
      var now = (window.performance && performance.now()) || Date.now();
      var elapsed = now - _pinStartedAt;
      if (sim.alpha() > 0.08 && elapsed < 3500) {
        setTimeout(_pinSettledNodes, 200);
        return;
      }
      sim._pinDone = true;
      var pinned = 0;
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (n.fx == null) { n.fx = n.x; n.fy = n.y; pinned++; }
      }
      console.log('[wfg] seed layout settled at α=' + sim.alpha().toFixed(3)
                  + ' — pinned ' + pinned + ' nodes');
    }
    setTimeout(_pinSettledNodes, 600);

    var handle = {
      destroy: function () {
        window.removeEventListener('resize', onResize);
        sim._traceAnimationToken = (sim._traceAnimationToken || 0) + 1;
        sim._tracePendingTargets = {}; sim._traceAnimationState = null;
        sim.stop();
        renderer.destroy();
        _wfg.clearActiveRenderer(renderer);
      },
      select: function (id) { renderer.selectId(id); },
      reflow: function () { onResize(); },
      applyFilter: function (pred) {
        if (typeof renderer.applyFilter === 'function') renderer.applyFilter(pred, ctx);
      },
      append: append,
    };
    // Expose a stable hook so the filter-bar driver can reach us.
    window.JUG.wfgApplyFilter = function (pred) { handle.applyFilter(pred); };
    return handle;
  }

  window.JUG = window.JUG || {};
  window.JUG.renderWorkflowGraph = renderWorkflowGraph;
})();
