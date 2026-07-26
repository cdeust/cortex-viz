// Cortex — Workflow Graph topology preparation + force helpers.
//
// Extracted from workflow_graph.js (issue #41: 500-line file cap, §4.1).
// Two cohesive halves of "turn raw nodes/edges into a laid-out context, and
// the pure force closures the d3 simulation drives" (§1.1 SRP):
//   prepareTopology — Fibonacci-spiral domain anchors, domainOf, primary
//                     tool_hub, degree/adjacency, per-node slot (via the
//                     slots module).
//   force helpers   — linkDistance/linkStrength/chargeStrength/slotForce/
//                     symbolMultiCenterForce/interDomainRepelForce/
//                     collisionRadius (pure closures over ctx).
//
// Consumes window.JUG._wfgConst + window.JUG._wfg.computeSlots (both loaded
// first). Publishes window.JUG._wfg.prepareTopology and
// window.JUG._wfg.forces (consumed by the orchestrator in workflow_graph.js).
(function () {
  var C = window.JUG._wfgConst;
  var KIND_RADIUS = C.KIND_RADIUS;
  var FILE_R = C.FILE_R, DISC_R = C.DISC_R, MEM_R = C.MEM_R;
  var SECTOR_SIDE_ANGLE = C.SECTOR_SIDE_ANGLE;
  var SHELL_LEVELS = C.SHELL_LEVELS;
  var EDGE_DISTANCE = C.EDGE_DISTANCE, EDGE_STRENGTH = C.EDGE_STRENGTH;
  var CROSS_DOMAIN_DISTANCE = C.CROSS_DOMAIN_DISTANCE;
  var CROSS_DOMAIN_STRENGTH = C.CROSS_DOMAIN_STRENGTH;
  var computeSlots = window.JUG._wfg.computeSlots;

  // ── Topology: Fibonacci-spiral domain anchors; domainOf; primary tool_hub;
  //    degree; adjacency; per-node slot (radial hierarchy).
  function prepareTopology(nodes, edges, width, height) {
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var domains = nodes.filter(function (n) { return n.kind === 'domain'; });
    // Trace graphs (domain → session → chain → file) are a tree, not the
    // galaxy's L1–L6 radial shells. Detect via the active view OR the
    // data schema OR trace-only kinds — on a fresh load only domains are
    // present (no session/action yet), so kind-sniffing alone would
    // wrongly draw the galaxy rings on the default trace screen.
    var _view = (window.JUG && JUG.state && JUG.state.activeView) || '';
    var _schema = (window.JUG && JUG.state && JUG.state.lastData &&
                   JUG.state.lastData.meta && JUG.state.lastData.meta.schema) || '';
    var isTrace = _view === 'trace' || _schema === 'trace.v1' || nodes.some(function (n) {
      var k = n.kind || n.type;
      return k === 'session' || k === 'action' || k === 'prompt';
    });

    var cx = width / 2, cy = height / 2;
    // Each domain's outer shell is roughly FILE_R + cushion; Fibonacci
    // spiral average spacing is R·√(π/N). Pick baseR so the spacing
    // exceeds the shell diameter — rings never collide.
    var N = Math.max(domains.length, 1);
    var shellDiameter = 2 * FILE_R + 60;
    var baseR = Math.max(
      Math.min(width, height) * 0.42,
      shellDiameter * Math.sqrt(N / Math.PI) * 0.65,
    );
    var phi = Math.PI * (3 - Math.sqrt(5));  // golden angle
    var anchors = {};
    domains.forEach(function (d, i) {
      var r = baseR * Math.sqrt((i + 0.5) / N);
      var theta = i * phi;
      anchors[d.id] = { x: cx + r * Math.cos(theta), y: cy + r * Math.sin(theta) };
      d.x = anchors[d.id].x; d.y = anchors[d.id].y;
      d.fx = d.x; d.fy = d.y;                // pin domain anchors — L1/L2/L3 rings orbit them.
    });

    var domainOf = {};
    nodes.forEach(function (n) {
      if (n.kind === 'domain') { domainOf[n.id] = n.id; return; }
      if (n.domain && byId[n.domain] && byId[n.domain].kind === 'domain') domainOf[n.id] = n.domain;
      else if (n.domain_id && byId[n.domain_id] && byId[n.domain_id].kind === 'domain') {
        domainOf[n.id] = n.domain_id;
      }
    });
    edges.forEach(function (e) {
      if (e.kind !== 'in_domain') return;
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      if (s.kind === 'domain' && !domainOf[t.id]) domainOf[t.id] = s.id;
      if (t.kind === 'domain' && !domainOf[s.id]) domainOf[s.id] = t.id;
    });
    // Trace edges carry the domain DOWN the chain: domain→session
    // (has_session), session→event + event→event (step / next), and
    // action→file (read/edit/write/run). Iterate to a fixed point so a
    // file reached only via action→file still resolves to its domain.
    // `discusses`/`remembers` attach discussion + memory BRANCH nodes to the
    // spine (they hang off a prompt/action without advancing it); they MUST be
    // here too, or those branch nodes never inherit a domain, fail the
    // anchored-domain gate in computeSlots, and pile at the origin as a fan.
    var _traceEdgeKinds = { has_session: 1, step: 1, next: 1,
      read: 1, edit: 1, write: 1, run: 1, discusses: 1, remembers: 1 };
    for (var _pass = 0; _pass < 6; _pass++) {
      var _changed = false;
      for (var _ei = 0; _ei < edges.length; _ei++) {
        var te = edges[_ei];
        if (!_traceEdgeKinds[te.kind]) continue;
        var ss = typeof te.source === 'object' ? te.source.id : te.source;
        var tt = typeof te.target === 'object' ? te.target.id : te.target;
        if (domainOf[ss] && !domainOf[tt]) { domainOf[tt] = domainOf[ss]; _changed = true; }
        else if (domainOf[tt] && !domainOf[ss]) { domainOf[ss] = domainOf[tt]; _changed = true; }
      }
      if (!_changed) break;
    }

    // Parent file per symbol — drives the symbol-petal clustering.
    // Prefer `defined_in` edges; fall back to `path` string match.
    var parentFile = {};
    edges.forEach(function (e) {
      if (e.kind !== 'defined_in') return;
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      if (s.kind === 'symbol' && t.kind === 'file') parentFile[s.id] = t.id;
      else if (t.kind === 'symbol' && s.kind === 'file') parentFile[t.id] = s.id;
    });
    var filesByPath = {};
    nodes.forEach(function (n) {
      if (n.kind === 'file' && n.path) filesByPath[n.path] = n.id;
    });
    nodes.forEach(function (n) {
      if (n.kind !== 'symbol' || parentFile[n.id]) return;
      if (n.path && filesByPath[n.path]) parentFile[n.id] = filesByPath[n.path];
    });
    // Every symbol MUST have a domain or the containment force can't
    // constrain it. Priority:
    //   1. Parent file's domain (derived from `defined_in` edge)
    //   2. node.domain_id / node.domain (server already tags each
    //      symbol with its project's domain id)
    //   3. GLOBAL fallback if somehow neither resolves.
    nodes.forEach(function (n) {
      if (n.kind !== 'symbol') return;
      var pf = parentFile[n.id];
      if (pf && domainOf[pf]) { domainOf[n.id] = domainOf[pf]; return; }
      var did = n.domain_id || (n.domain ? 'domain:' + n.domain : '');
      if (did && byId[did]) { domainOf[n.id] = did; return; }
      if (!domainOf[n.id]) domainOf[n.id] = 'domain:__global__';
    });

    var primaryHub = {}, hubWeight = {};
    edges.forEach(function (e) {
      if (e.kind !== 'tool_used_file') return;
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      var hub = s.kind === 'tool_hub' ? s : (t.kind === 'tool_hub' ? t : null);
      var f = s.kind === 'file' ? s : (t.kind === 'file' ? t : null);
      if (!hub || !f) return;
      if (domainOf[hub.id] && domainOf[hub.id] === domainOf[f.id]) {
        var w = e.weight != null ? e.weight : 1;
        if (!(f.id in hubWeight) || w > hubWeight[f.id]) { hubWeight[f.id] = w; primaryHub[f.id] = hub.id; }
      }
    });

    var degree = {}, adj = {};
    edges.forEach(function (e) {
      degree[e.source] = (degree[e.source] || 0) + 1;
      degree[e.target] = (degree[e.target] || 0) + 1;
      var sd = domainOf[e.source], td = domainOf[e.target];
      e._crossDomain = !!(sd && td && sd !== td);
      if (!adj[e.source]) adj[e.source] = {};
      if (!adj[e.target]) adj[e.target] = {};
      adj[e.source][e.target] = true; adj[e.target][e.source] = true;
    });

    var slotOf = computeSlots(nodes, domains, anchors, domainOf, primaryHub, parentFile, cx, cy, edges, byId, isTrace);

    return { byId: byId, nodes: nodes, edges: edges, domains: domains,
      anchors: anchors, domainOf: domainOf, primaryHub: primaryHub,
      parentFile: parentFile,
      degree: degree, adj: adj, slotOf: slotOf,
      isTrace: isTrace,
      // Trace has no L1–L6 shells or discussion/memory side lanes —
      // suppress them so the canvas draws a clean tree.
      shells: isTrace ? [] : SHELL_LEVELS,
      sideShells: isTrace ? [] : [
        { key: 'L4', r: DISC_R, label: 'L4 discussions', angle: SECTOR_SIDE_ANGLE },
        { key: 'L5', r: MEM_R,  label: 'L5 memories',    angle: -SECTOR_SIDE_ANGLE },
      ], cx: cx, cy: cy, baseR: baseR,
      width: width, height: height };
  }

  // ── Force helpers (pure closures) ──
  function linkDistance(e) {
    if (e._crossDomain) return CROSS_DOMAIN_DISTANCE;
    return EDGE_DISTANCE[e.kind] != null ? EDGE_DISTANCE[e.kind] : 30;
  }
  function linkStrength(e) {
    if (e._crossDomain) return CROSS_DOMAIN_STRENGTH;
    var s = EDGE_STRENGTH[e.kind] != null ? EDGE_STRENGTH[e.kind] : 0.4;
    return s * (e.weight != null ? Math.min(1, 0.3 + e.weight * 0.7) : 1);
  }
  function chargeStrength(n) {
    if (n.kind === 'domain')   return -620;
    if (n.kind === 'tool_hub') return -140;
    if (n.kind === 'agent' || n.kind === 'skill') return -80;
    // Symbols: enough mutual repulsion to spread laterally in the
    // interlock space (Maxwell: -22, local distanceMax).
    if (n.kind === 'symbol')   return -22;
    return -28;
  }
  function slotForce(ctx, k) {
    return function (alpha) {
      var s = k * alpha;
      for (var i = 0; i < ctx.nodes.length; i++) {
        var n = ctx.nodes[i];
        if (n.kind === 'domain') continue;
        var slot = ctx.slotOf[n.id];
        if (!slot) continue;
        n.vx += (slot.x - n.x) * s;
        n.vy += (slot.y - n.y) * s;
      }
    };
  }
  // Multi-centroid attraction (Alexander's deep interlock): a symbol
  // is pulled by EVERY domain it touches via its edges, weighted 1/N
  // where N = number of distinct domains touched. Symbols connected
  // only to their home domain sit near it; cross-domain symbols
  // literally fall into the interlock space between two or more hubs.
  // No containment — position emerges from connectivity alone.
  function symbolMultiCenterForce(ctx) {
    // Precompute each symbol's domain centroid list ONCE.
    var symDomains = {};
    for (var i = 0; i < ctx.nodes.length; i++) {
      var n = ctx.nodes[i];
      if (n.kind !== 'symbol') continue;
      var set = {};
      // Home domain (from parent file or node's own domain_id).
      var home = ctx.domainOf[n.id];
      if (home && ctx.anchors[home]) set[home] = 1;
      symDomains[n.id] = set;
    }
    // Walk every AST edge; for each symbol endpoint, add the OTHER
    // endpoint's domain to its centroid set.
    ctx.edges.forEach(function (e) {
      var k = e.kind;
      if (k !== 'defined_in' && k !== 'calls' &&
          k !== 'imports' && k !== 'member_of') return;
      var sId = typeof e.source === 'object' ? e.source.id : e.source;
      var tId = typeof e.target === 'object' ? e.target.id : e.target;
      var sN = ctx.byId[sId], tN = ctx.byId[tId];
      if (!sN || !tN) return;
      if (sN.kind === 'symbol' && ctx.domainOf[tId] && ctx.anchors[ctx.domainOf[tId]]) {
        symDomains[sId] = symDomains[sId] || {};
        symDomains[sId][ctx.domainOf[tId]] = 1;
      }
      if (tN.kind === 'symbol' && ctx.domainOf[sId] && ctx.anchors[ctx.domainOf[sId]]) {
        symDomains[tId] = symDomains[tId] || {};
        symDomains[tId][ctx.domainOf[sId]] = 1;
      }
    });
    ctx._symDomains = symDomains;

    return function (alpha) {
      var s = 0.06 * alpha;
      for (var i = 0; i < ctx.nodes.length; i++) {
        var n = ctx.nodes[i];
        if (n.kind !== 'symbol') continue;
        var set = symDomains[n.id];
        if (!set) continue;
        var keys = Object.keys(set);
        if (!keys.length) continue;
        var w = s / keys.length;
        for (var j = 0; j < keys.length; j++) {
          var a = ctx.anchors[keys[j]];
          if (!a) continue;
          n.vx += (a.x - n.x) * w;
          n.vy += (a.y - n.y) * w;
        }
      }
    };
  }
  function interDomainRepelForce(ctx, k) {
    return function (alpha) {
      var doms = ctx.domains, strength = k * alpha * 8000;
      for (var i = 0; i < doms.length; i++) {
        var a = doms[i];
        for (var j = i + 1; j < doms.length; j++) {
          var b = doms[j];
          var dx = b.x - a.x, dy = b.y - a.y;
          var d2 = dx * dx + dy * dy + 1;
          var f = strength / d2, inv = 1 / Math.sqrt(d2);
          a.vx -= dx * inv * f; a.vy -= dy * inv * f;
          b.vx += dx * inv * f; b.vy += dy * inv * f;
        }
      }
    };
  }
  function collisionRadius(n, ctx) {
    var base = KIND_RADIUS[n.kind] != null ? KIND_RADIUS[n.kind] : 6;
    return base + Math.min(8, Math.sqrt(ctx.degree[n.id] || 0));
  }

  window.JUG = window.JUG || {};
  window.JUG._wfg = window.JUG._wfg || {};
  window.JUG._wfg.prepareTopology = prepareTopology;
  window.JUG._wfg.forces = {
    linkDistance: linkDistance,
    linkStrength: linkStrength,
    chargeStrength: chargeStrength,
    slotForce: slotForce,
    symbolMultiCenterForce: symbolMultiCenterForce,
    interDomainRepelForce: interDomainRepelForce,
    collisionRadius: collisionRadius,
  };
})();
