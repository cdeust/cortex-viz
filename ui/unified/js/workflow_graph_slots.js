// Cortex — Workflow Graph slot layout (computeSlots).
//
// Extracted from workflow_graph.js (issue #41: 500-line file cap, §4.1).
// The one concern of "assign each non-domain node its deterministic target
// (x,y) slot expressing the L1-L6 radial hierarchy / trace session disks"
// (§1.1 SRP). Pure geometry: reads the topology maps prepareTopology built,
// writes a slotOf id->{x,y} map. No DOM, no simulation.
//
// Consumes window.JUG._wfgConst (geometry constants, loaded first).
// Publishes window.JUG._wfg.computeSlots — prepareTopology (topology module,
// loaded next) calls it.
(function () {
  var C = window.JUG._wfgConst;
  var GOLDEN = Math.PI * (3 - Math.sqrt(5));
  var TOOL_R = C.TOOL_R, FILE_R = C.FILE_R, SETUP_R = C.SETUP_R;
  var DISC_R = C.DISC_R, MEM_R = C.MEM_R, MCP_R = C.MCP_R;
  var TOOL_LOCAL_ANGLE = C.TOOL_LOCAL_ANGLE;
  var SECTOR_SETUP_HALF = C.SECTOR_SETUP_HALF;
  var SECTOR_SIDE_HALF = C.SECTOR_SIDE_HALF;
  var SECTOR_SIDE_ANGLE = C.SECTOR_SIDE_ANGLE;
  var ENTITY_DOMAIN_BLEND = C.ENTITY_DOMAIN_BLEND;
  var ENTITY_ORPHAN_R = C.ENTITY_ORPHAN_R;
  var ENTITY_HEAT_TAU = C.ENTITY_HEAT_TAU;
  var ENTITY_TOPN = C.ENTITY_TOPN;

  var TRACE_CAUSAL_EDGE = { step: 1, next: 1, did: 1, read: 1, edit: 1,
    write: 1, run: 1, use: 1, call: 1, spawn: 1, fetch: 1,
    discusses: 1, remembers: 1 };
  function edgePair(edge) {
    return [(edge.source && edge.source.id) || edge.source,
      (edge.target && edge.target.id) || edge.target];
  }
  function traceCausalContext(nodes, edges) {
    var byId = {}, rootOf = {}, rootSets = {}, predecessors = {}, shared = {};
    nodes.forEach(function (node) {
      byId[node.id] = node;
      var root = node.kind === 'session' ? node.id
        : (node.session_id ? 'session:' + node.session_id : null);
      if (root) { rootSets[node.id] = {}; rootSets[node.id][root] = true; }
    });
    var causalEdges = (edges || []).filter(function (edge) {
      var pair = edgePair(edge);
      return TRACE_CAUSAL_EDGE[edge.kind] && byId[pair[0]] && byId[pair[1]];
    }).sort(function (left, right) {
      var a = edgePair(left).join('\u0000') + '\u0000' + left.kind;
      var b = edgePair(right).join('\u0000') + '\u0000' + right.kind;
      return a.localeCompare(b);
    });
    // Fixed point, not a pass limit: shuffled or long chains inherit the same
    // canonical session without changing their compact visual order.
    var changed = true;
    while (changed) {
      changed = false;
      causalEdges.forEach(function (edge) {
        var pair = edgePair(edge), sourceRoots = rootSets[pair[0]];
        if (!sourceRoots) return;
        var targetRoots = rootSets[pair[1]] = rootSets[pair[1]] || {};
        Object.keys(sourceRoots).forEach(function (root) {
          if (!targetRoots[root]) { targetRoots[root] = true; changed = true; }
        });
      });
    }
    Object.keys(rootSets).forEach(function (id) {
      var roots = Object.keys(rootSets[id]).sort();
      if (roots.length) rootOf[id] = roots[0];
      if (roots.length > 1) shared[id] = true;
    });
    causalEdges.forEach(function (edge) {
      var pair = edgePair(edge);
      (predecessors[pair[1]] = predecessors[pair[1]] || []).push(pair[0]);
    });
    return { rootOf: rootOf, predecessors: predecessors, shared: shared };
  }

  // Assign each non-domain node a target (x,y) slot expressing the hierarchy:
  //   domain → L1 (setup) → L2 (tools) → L3 (files);  discussions lane;  memories lane.
  function resolveTraceCollisions(nodes, domains, anchors, domainOf, slotOf, radiusOf) {
    var radii = {}, grouped = {}, maxRadius = 0;
    nodes.forEach(function (node) {
      var radius = radiusOf(node);
      radii[node.id] = radius;
      maxRadius = Math.max(maxRadius, radius);
      var domainId = domainOf[node.id];
      if (node.kind !== 'domain' && domainId && slotOf[node.id]) {
        (grouped[domainId] = grouped[domainId] || []).push(node);
      }
    });
    // A cell spans the largest possible collision distance (r1+r2). Therefore
    // an exact query needs only the candidate cell and its eight neighbours.
    var cell = Math.max(Number.EPSILON, 2 * maxRadius), buckets = {};
    function key(x, y) { return Math.floor(x / cell) + ',' + Math.floor(y / cell); }
    function insert(item) {
      var bucket = buckets[key(item.x, item.y)];
      if (!bucket) bucket = buckets[key(item.x, item.y)] = [];
      bucket.push(item);
    }
    function nearby(x, y) {
      var result = [], cx2 = Math.floor(x / cell), cy2 = Math.floor(y / cell);
      for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) {
        var bucket = buckets[(cx2 + dx) + ',' + (cy2 + dy)];
        if (bucket) result.push.apply(result, bucket);
      }
      return result;
    }
    // Every domain anchor is an immutable obstacle before any child is placed;
    // later domains therefore yield around earlier content without moving hubs.
    domains.forEach(function (domain) {
      var anchor = anchors[domain.id];
      if (anchor) insert({ x: anchor.x, y: anchor.y, radius: radii[domain.id] });
    });
    domains.forEach(function (domain) {
      (grouped[domain.id] || []).sort(function (left, right) {
        var band = { session: 0, prompt: 1, action: 2, discussion: 3, memory: 4, file: 5 };
        var lb = band[left.kind] == null ? 6 : band[left.kind];
        var rb = band[right.kind] == null ? 6 : band[right.kind];
        if (lb !== rb) return lb - rb;
        var ls = left.seq == null ? Number.MAX_SAFE_INTEGER : left.seq;
        var rs = right.seq == null ? Number.MAX_SAFE_INTEGER : right.seq;
        return ls === rs ? String(left.id).localeCompare(String(right.id)) : ls - rs;
      }).forEach(function (node) {
        var target = slotOf[node.id], radius = radiusOf(node), penetration = 0;
        function collides(x, y) {
          penetration = 0;
          var candidates = nearby(x, y);
          for (var i = 0; i < candidates.length; i++) {
            var other = candidates[i];
            var distance = Math.hypot(x - other.x, y - other.y);
            penetration = Math.max(penetration, radius + other.radius - distance);
          }
          return penetration > 0;
        }
        var x = target.x, y = target.y;
        if (collides(x, y)) {
          var overlap = penetration;
          for (var k = 1; ; k++) {
            var distance = overlap + 2 * radius * Math.sqrt(k);
            var angle = k * GOLDEN;
            x = target.x + distance * Math.cos(angle);
            y = target.y + distance * Math.sin(angle);
            if (!collides(x, y)) break;
          }
          slotOf[node.id] = { x: x, y: y };
        }
        insert({ x: x, y: y, radius: radius });
      });
    });
  }

  function computeSlots(nodes, domains, anchors, domainOf, primaryHub, parentFile, cx, cy, edges, byId, isTrace, radiusOf) {
    // Group non-domain nodes by (domain, kind).
    var groups = {};
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.kind === 'domain') continue;
      var dom = domainOf[n.id];
      if (!dom || !anchors[dom]) continue;
      if (!groups[dom]) groups[dom] = {};
      if (!groups[dom][n.kind]) groups[dom][n.kind] = [];
      groups[dom][n.kind].push(n);
    }
    var slotOf = {};
    var setupKinds = ['skill', 'hook', 'command', 'agent'];
    var traceCausal = isTrace ? traceCausalContext(nodes, edges)
      : { rootOf: {}, predecessors: {}, shared: {} };

    // ── Entity → linked-memory index (Gap 10 / Kekulé positioning).
    //    One pass over the about_entity edge set builds, per entity,
    //    the list of MEMORY node ids it sits on. Memories without slots
    //    yet (slotOf[memId] absent at this point — memories are slotted
    //    later in the per-domain loop) are resolved lazily in the
    //    second pass below by stashing entity centroids for deferred
    //    computation after memory slots exist.
    var entityMemLinks = {};
    if (edges && edges.length) {
      for (var ei = 0; ei < edges.length; ei++) {
        var e = edges[ei];
        if (e.kind !== 'about_entity') continue;
        var sId = typeof e.source === 'object' ? e.source.id : e.source;
        var tId = typeof e.target === 'object' ? e.target.id : e.target;
        var sKind = byId && byId[sId] ? byId[sId].kind : null;
        var tKind = byId && byId[tId] ? byId[tId].kind : null;
        var memId, entId;
        if (sKind === 'memory' && tKind === 'entity') { memId = sId; entId = tId; }
        else if (tKind === 'memory' && sKind === 'entity') { memId = tId; entId = sId; }
        else continue;
        if (!entityMemLinks[entId]) entityMemLinks[entId] = [];
        entityMemLinks[entId].push(memId);
      }
    }

    // ── Trace file → session map ──────────────────────────────────────────
    // Trace file nodes carry no session_id (session_trace._file_node emits only
    // id/kind/label/path), so they can't self-identify a session disk. But each
    // file is linked to the ACTION that touched it via a read/edit/write/run
    // verb edge, and actions DO carry session_id. Resolve each file to the
    // session of the first action that touched it, so the bySession grouping
    // below can pack the file into that session's disk (outer band) instead of
    // flinging it to the orphan ring. Galaxy files use tool_used_file edges (not
    // these verbs) so this map stays empty for them — they fall through to L3.
    Object.keys(groups).forEach(function (domId) {
      var a = anchors[domId];
      var outward = Math.atan2(a.y - cy, a.x - cx);  // radially outward from graph center
      // For domains near the center the outward axis is unstable — bias upward.
      if (Math.hypot(a.x - cx, a.y - cy) < 5) outward = -Math.PI / 2;
      var g = groups[domId];

      // ── Trace layout: domain → per-session COMPACT SUB-CLUSTERS ──
      // Each session becomes a tight disk of its own work, placed on a
      // ring around the domain hub. ALL of a session's events (prompt /
      // action / file) pack around that session's sub-center in a
      // phyllotaxis (sunflower) spiral — a dense, even, NON-overlapping
      // disk, exactly the compactness the galaxy got from orbiting a hub.
      // No marching-outward rows: cluster radius grows with sqrt(count),
      // so even a 600-event session stays a bounded blob you can read as
      // "this session's work", clearly separated from other sessions.
      var sessions = g.session || [];
      // Group every event under its CLUSTER KEY. Trace events key off
      // session_id; other lenses (e.g. the wiki cross-lens graph) tag
      // each node with a generic `cluster` (scope name, or "_xlens"),
      // so one disk forms per scope with ZERO trace-path changes —
      // trace nodes have no `cluster` and fall back to session:<id>.
      var bySession = {};   // clusterKey -> [nodes]
      // Kinds eligible for a session/lens disk. The guard below admits a node
      // ONLY if it carries a `cluster` (cross-lens) or `session_id` (trace) —
      // galaxy entity/symbol/file/memory have neither, so they're skipped here
      // and placed by their dedicated kind-lane (L3/L5/memories) downstream.
      //
      // ``discussion`` is TRACE-ONLY: trace discussions (assistant turns) carry
      // a session_id and MUST pack into their session disk (otherwise they fall
      // to the origin as a radiating fan — the "trace breaks as we add nodes"
      // report). But galaxy discussion-SUMMARY nodes ALSO carry a session_id,
      // and in the galaxy they belong in the discussions LANE, not a disklet —
      // so only admit discussions when this is a trace layout.
      var clusterKinds = ['prompt', 'action', 'file',
                          'wiki_page', 'entity', 'symbol', 'memory', 'prd'];
      if (isTrace) {
        clusterKinds.push('discussion', 'tool_hub', 'mcp', 'api', 'database',
                          'skill', 'command', 'agent', 'web');
      }
      clusterKinds.forEach(function (kind) {
        (g[kind] || []).forEach(function (n) {
          // cluster (cross-lens) → session_id (trace prompt/action/discussion/
          // memory) → fileSession (trace files, resolved via their action's
          // verb edge). A node matching none belongs to no disk (galaxy
          // entity/symbol/file) and is left to its kind-lane downstream.
          var sid = n.cluster
            || (n.session_id ? 'session:' + n.session_id : null)
            || (isTrace ? traceCausal.rootOf[n.id] : null);
          if (!sid) return;
          (bySession[sid] = bySession[sid] || []).push(n);
        });
      });
      // ── Per-session disks, packed by ACTUAL radius so they never merge ──
      // The galaxy stays readable at 29k nodes because each group is one
      // coherent disk AND the disks are separated by hard gaps. Slots are
      // absolute (slotForce pulls each node to its computed x,y), so the
      // separation must be solved HERE — a runtime disk-collision would
      // fight slotForce and reproduce the blob. We size every session's
      // disk by its event count, then walk a ring placing each disk at a
      // cumulative angle equal to its own angular width (+gap), growing the
      // ring radius until the whole run fits in 2π. Even, non-overlapping,
      // exactly the galaxy's "tight disks with gaps".
      var DOT = 13;                       // ~node spacing in the spiral
      var GAP = 16;                       // hard gap between adjacent disks
      function clusterRadius(count) { return DOT * Math.sqrt(Math.max(count, 1)) + 14; }

      // Build one cluster per session (union of session nodes + any events
      // whose session node hasn't loaded yet), each with a type-major item
      // order so the phyllotaxis lays prompts inner, actions mid, files
      // outer — kind reads as concentric bands within one clean disk.
      var KIND_BAND = { prompt: 0, action: 1, file: 2 };
      var sessNodeBySid = {};
      sessions.forEach(function (s) { sessNodeBySid['session:' + (s.session_id || '')] = s; });
      var clusterSids = {};
      sessions.forEach(function (s) { clusterSids['session:' + (s.session_id || '')] = 1; });
      Object.keys(bySession).forEach(function (sid) { clusterSids[sid] = 1; });
      var clusters = Object.keys(clusterSids).map(function (sid) {
        var items = (bySession[sid] || []).slice();
        items.sort(function (p, q) {
          var pk = KIND_BAND[p.kind] != null ? KIND_BAND[p.kind] : 3;
          var qk = KIND_BAND[q.kind] != null ? KIND_BAND[q.kind] : 3;
          if (pk !== qk) return pk - qk;
          var ps = (p.seq != null ? p.seq : 1e9), qs = (q.seq != null ? q.seq : 1e9);
          return ps === qs ? String(p.id).localeCompare(String(q.id)) : ps - qs;
        });
        return { sid: sid, node: sessNodeBySid[sid] || null,
          items: items, rad: clusterRadius(items.length) };
      });
      // ── Collapse UNEXPANDED session hubs into ONE compact blob ──────────────
      // A domain holds dozens of sessions but only a few are expanded (chain
      // loaded). An unexpanded session is an items-less hub of identical tiny
      // radius; left as individual disks they all share the same ring and, being
      // small + numerous, smear into a single-file circle at a large radius (the
      // "circle mapping" that appears the moment a session is selected and the
      // re-mount re-runs this layout). Instead gather every empty hub into one
      // dense phyllotaxis blob — a single cluster whose ITEMS are the hub nodes —
      // so they pack as a tight satellite instead of a ring. Expanded sessions
      // keep their own content disks; selecting one no longer reshuffles the rest
      // into a circle.
      var contentClusters = [];
      var emptyHubNodes = [];
      clusters.forEach(function (c) {
        if (c.items.length === 0 && c.node) emptyHubNodes.push(c.node);
        else contentClusters.push(c);
      });
      if (emptyHubNodes.length) {
        emptyHubNodes.sort(function (a3, b3) { return String(a3.id).localeCompare(String(b3.id)); });
        contentClusters.push({
          sid: '', node: null, items: emptyHubNodes,
          rad: clusterRadius(emptyHubNodes.length),
        });
      }
      clusters = contentClusters;
      // Largest disks first → each ring's thickness is set by its biggest disk,
      // and big disks land in the inner rings (stable, dense packing).
      clusters.sort(function (a2, b2) {
        return b2.rad === a2.rad
          ? String(a2.sid).localeCompare(String(b2.sid)) : b2.rad - a2.rad;
      });

      // ── GRAVITY-PACK session disks tight around the domain hub ──────────────
      // Concentric rings (the prior fix) placed disks at INCREASING radii, so a
      // domain with many expanded sessions pushed the bulk into outer rings — a
      // big circle with a hollow centre, the domain hub stranded in the middle
      // (repeated report: "circle placement, should gravitate near the domain").
      // Instead pack like gravity: each disk (largest first) takes the position
      // CLOSEST to the hub that overlaps neither an already-placed disk nor the
      // hub's clearance. The cluster fills OUTWARD from the domain anchor — dense,
      // centred ON the domain, no ring and no hollow centre. The galaxy never
      // reaches this path (its `clusters` are empty), so this is trace/lens-only.
      var HUB_CLEARANCE = 48;                    // px, kept clear around the domain hub
      function placeDisk(c, scx, scy) {
        if (c.node) slotOf[c.node.id] = { x: scx, y: scy };  // session hub = disk center
        c.items.forEach(function (n, k) {
          // phyllotaxis: r = c·√k, angle = k·goldenAngle → even packing;
          // type-major order above turns k-bands into kind-bands.
          var rr = DOT * Math.sqrt(k + 0.5);
          var aa = (k + 1) * GOLDEN;
          slotOf[n.id] = { x: scx + rr * Math.cos(aa), y: scy + rr * Math.sin(aa) };
        });
      }
      // The hub itself is a central obstacle so disks ring it without burying it.
      var placed = [{ x: a.x, y: a.y, r: HUB_CLEARANCE }];
      function gravitySlot(rad) {
        // Archimedean spiral out from the anchor; the spiral grows ~one disk
        // width per turn and is sampled at a near-uniform arc step. The first
        // sample that clears every placed disk (by GAP) is the closest free spot.
        var theta = 0;
        for (var iter = 0; iter < 20000; iter++) {
          var rr = (rad + GAP) * theta / (2 * Math.PI);
          var x = a.x + rr * Math.cos(theta);
          var y = a.y + rr * Math.sin(theta);
          var ok = true;
          for (var p = 0; p < placed.length; p++) {
            var dx = x - placed[p].x, dy = y - placed[p].y;
            var minD = rad + placed[p].r + GAP;
            if (dx * dx + dy * dy < minD * minD) { ok = false; break; }
          }
          if (ok) return { x: x, y: y };
          // arc-length step ≈ 0.6·rad, clamped so we always sample ≥12 pts/turn.
          theta += Math.min(0.5, Math.max(0.08, (rad * 0.6) / Math.max(rr, 1)));
        }
        return { x: a.x, y: a.y };
      }
      var outerRingR = HUB_CLEARANCE;            // outermost extent — used by the orphan fallback
      clusters.forEach(function (c) {
        var pos = gravitySlot(c.rad);
        placed.push({ x: pos.x, y: pos.y, r: c.rad });
        placeDisk(c, pos.x, pos.y);
        var reach = Math.hypot(pos.x - a.x, pos.y - a.y) + c.rad;
        if (reach > outerRingR) outerRingR = reach;
      });

      // L2: tool_hubs at fixed per-tool angles within the setup sector.
      var hubAngle = {};
      (g.tool_hub || []).forEach(function (h) {
        if (slotOf[h.id]) return;
        var local = TOOL_LOCAL_ANGLE[h.tool];
        if (local == null) local = 0;
        var t = outward + local;
        hubAngle[h.id] = t;
        slotOf[h.id] = { x: a.x + TOOL_R * Math.cos(t),
                         y: a.y + TOOL_R * Math.sin(t) };
      });

      // L3: files orbit their primary tool_hub (same angle + small jitter).
      // bySession placement wins: a file already slotted into a session/lens
      // disk above is skipped here (mirrors the orphan-files guard) so the
      // galaxy hub-orbit lane never clobbers a trace file's session-disk slot.
      var filesByHub = {};
      (g.file || []).forEach(function (f) {
        if (slotOf[f.id]) return;
        var hid = primaryHub[f.id];
        if (!filesByHub[hid]) filesByHub[hid] = [];
        filesByHub[hid].push(f);
      });
      Object.keys(filesByHub).forEach(function (hid) {
        var theta = hubAngle[hid];
        if (theta == null) theta = outward;  // hub in another domain (cross-domain file)
        var arr = filesByHub[hid];
        var arc = Math.min(0.35, 0.08 + arr.length * 0.015);
        arr.forEach(function (f, i) {
          var t = theta + ((i + 0.5) / arr.length - 0.5) * arc;
          var r = FILE_R + ((i % 3) - 1) * 4;  // radial stagger to reduce overlap
          slotOf[f.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      });

      // True last-resort: a file in NO session disk (bySession) and NO tool-hub
      // orbit (L3) — e.g. a trace file whose action lacked a session_id. Runs
      // AFTER L3 so it never pre-empts the galaxy hub-orbit lane (that ordering
      // bug flung every galaxy file to this ring). Skips anything already
      // placed, so it only catches genuine orphans.
      var orphanI = 0;
      (g.file || []).forEach(function (n) {
        if (slotOf[n.id]) return;
        var t = outward + (orphanI++) * GOLDEN;
        var r = outerRingR + 30 + (orphanI % 5) * 12;
        slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
      });

      // L1: skills, hooks, commands, agents — fanned inner ring.
      var setup = [];
      setupKinds.forEach(function (k) { (g[k] || []).forEach(function (x) {
        if (!slotOf[x.id]) setup.push(x);
      }); });
      if (setup.length) {
        var arc1 = SECTOR_SETUP_HALF * 2;
        setup.forEach(function (n, i) {
          var t = outward + ((i + 0.5) / setup.length - 0.5) * arc1;
          var r = SETUP_R + (i % 2) * 8;
          slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      }

      // Discussions lane (opposite side from setup, one side). Galaxy-only in
      // practice: trace discussions are pre-placed into their session disk
      // above (clusterKinds includes 'discussion' when isTrace), so the guard
      // makes this lane a no-op for them — bySession placement wins.
      var disc = g.discussion || [];
      if (disc.length) {
        var center = outward + SECTOR_SIDE_ANGLE;
        var arc2 = SECTOR_SIDE_HALF * 2 + Math.min(Math.PI / 3, disc.length * 0.04);
        disc.forEach(function (n, i) {
          if (slotOf[n.id]) return;
          var t = center + ((i + 0.5) / disc.length - 0.5) * arc2;
          var r = DISC_R + (i % 3) * 6;
          slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      }

      // Memories lane (opposite side from setup, other side). Like discussions:
      // trace memory nodes carry a session_id and are pre-placed into their
      // session disk above, so the guard skips them here; galaxy memories have
      // no session_id and are placed by this lane as before.
      var mem = g.memory || [];
      if (mem.length) {
        var center2 = outward - SECTOR_SIDE_ANGLE;
        var arc3 = SECTOR_SIDE_HALF * 2 + Math.min(Math.PI / 2.5, mem.length * 0.03);
        mem.forEach(function (n, i) {
          if (slotOf[n.id]) return;
          var t = center2 + ((i + 0.5) / mem.length - 0.5) * arc3;
          var r = MEM_R + (i % 4) * 8;
          slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      }

      // MCPs sit INSIDE the domain (between the center of the graph and the
      // domain anchor), so their long INVOKED_MCP edges fan visibly between
      // domains that share the MCP.
      (g.mcp || []).forEach(function (n, i) {
        if (slotOf[n.id]) return;
        var t = outward + Math.PI;  // inward
        var jitter = (i - (g.mcp.length - 1) / 2) * 0.25;
        slotOf[n.id] = { x: a.x + MCP_R * Math.cos(t + jitter),
                         y: a.y + MCP_R * Math.sin(t + jitter) };
      });

      // L5+E entities: see ADR-0047. Slot = heat-weighted memory
      // centroid blended 15% to domain hub (Kekulé valence analysis).
      // Heat gate is OR-semantic by design: entity is kept if within
      // top-N OR above heat threshold. `ENTITY_TOPN` therefore acts as
      // a per-domain *floor* on visibility (cold domains still show
      // their top-40), not a ceiling on hot ones.
      var ents = (g.entity || []).slice();
      if (ents.length) {
        ents.sort(function (a, b) {
          return (b.heat != null ? b.heat : 0) - (a.heat != null ? a.heat : 0);
        });
        var kept = ents.filter(function (en, idx) {
          return idx < ENTITY_TOPN || (en.heat != null && en.heat >= ENTITY_HEAT_TAU);
        });
        var hubX = a.x, hubY = a.y;
        kept.forEach(function (en) {
          var memIds = entityMemLinks[en.id] || [];
          var cx2 = 0, cy2 = 0, wTotal = 0;
          for (var mi = 0; mi < memIds.length; mi++) {
            var mSlot = slotOf[memIds[mi]];
            if (!mSlot) continue;
            // Heat of the memory node itself (hotter memories pull harder).
            var mNode = byId ? byId[memIds[mi]] : null;
            var w = mNode && mNode.heat != null ? Math.max(0.05, mNode.heat) : 0.5;
            cx2 += mSlot.x * w; cy2 += mSlot.y * w; wTotal += w;
          }
          if (wTotal > 0) {
            // Kekulé centroid blended 15% toward the domain hub.
            var mcx = cx2 / wTotal, mcy = cy2 / wTotal;
            slotOf[en.id] = {
              x: (1 - ENTITY_DOMAIN_BLEND) * mcx + ENTITY_DOMAIN_BLEND * hubX,
              y: (1 - ENTITY_DOMAIN_BLEND) * mcy + ENTITY_DOMAIN_BLEND * hubY,
            };
          } else {
            // Orphan: hash-deterministic ring around the domain hub so
            // the same entity lands in the same place across runs.
            var h = 0;
            for (var ci = 0; ci < en.id.length; ci++) {
              h = ((h << 5) - h + en.id.charCodeAt(ci)) | 0;
            }
            var theta = (Math.abs(h) % 1000) / 1000 * Math.PI * 2;
            slotOf[en.id] = {
              x: hubX + ENTITY_ORPHAN_R * Math.cos(theta),
              y: hubY + ENTITY_ORPHAN_R * Math.sin(theta),
            };
          }
        });
        // Entities below the heat gate are intentionally slot-free —
        // they'll drift to default positions and can be filter-hidden
        // via the existing "kind:entity" toggle.
      }

      // L6 symbols intentionally have NO slot — their final position
      // is determined by the codebase-analysis edges the force
      // simulation operates on (`defined_in` pulls toward the parent
      // file, `calls` pulls toward callers/callees, `imports` bridges
      // files, `member_of` clusters methods with their class). The
      // initial x/y seeding happens in mount() from the parent file's
      // position, then the force simulation does the layout work.
    });
    // radiusOf is required, not optional: prepareTopology is the sole caller
    // and always supplies it. Guarding on it would make "collisions silently
    // unresolved" a reachable-looking degraded mode that no caller can enter.
    if (isTrace) {
      resolveTraceCollisions(nodes, domains, anchors, domainOf, slotOf, radiusOf);
    }
    return slotOf;
  }

  window.JUG = window.JUG || {};
  window.JUG._wfg = window.JUG._wfg || {};
  window.JUG._wfg.computeSlots = computeSlots;
  window.JUG._wfg.traceCausalContext = traceCausalContext;
})();
