// Cortex — canonical target selection + animation for incremental Trace expansion.
// Pure state/animation helpers: no DOM or camera ownership.
(function () {
  // Shared spatial-transition budget: ui/shared/tokens/elevation.css
  // defines --dur-panel as 300ms for a visible panel-scale movement.
  var TRACE_REFLOW_MS = 300;
  var TRACE_PARENT_EDGE = { step: 1, next: 1, did: 1, read: 1, edit: 1,
    write: 1, run: 1, use: 1, call: 1, spawn: 1, fetch: 1,
    discusses: 1, remembers: 1 };

  function edgeIds(edge) {
    return [
      (edge.source && edge.source.id) || edge.source,
      (edge.target && edge.target.id) || edge.target,
    ];
  }

  function sessionRoots(nodes, edges) {
    var roots = {};
    nodes.forEach(function (node) {
      if (node.kind === 'session') roots[node.id] = node.id;
      else if (node.session_id) roots[node.id] = 'session:' + node.session_id;
    });
    var changed = true;
    while (changed) {
      changed = false;
      edges.forEach(function (edge) {
        var pair = edgeIds(edge), left = roots[pair[0]];
        if (left && !roots[pair[1]]) { roots[pair[1]] = left; changed = true; }
      });
    }
    return roots;
  }

  function causalParents(nodes, edges) {
    var byId = {}, candidates = {};
    nodes.forEach(function (node) { byId[node.id] = node; });
    edges.forEach(function (edge) {
      if (!TRACE_PARENT_EDGE[edge.kind]) return;
      var pair = edgeIds(edge);
      if (!byId[pair[0]] || !byId[pair[1]]) return;
      (candidates[pair[1]] = candidates[pair[1]] || []).push(pair[0]);
    });
    var parentOf = {};
    Object.keys(candidates).forEach(function (target) {
      candidates[target].sort(function (left, right) {
        var ls = Number(byId[left].seq), rs = Number(byId[right].seq);
        var lf = isFinite(ls), rf = isFinite(rs);
        if (lf && rf && ls !== rs) return ls - rs;
        if (lf !== rf) return lf ? -1 : 1;
        return String(left).localeCompare(String(right));
      });
      parentOf[target] = candidates[target][0];
    });
    return parentOf;
  }

  function packTraceExpansion(ctx, edges, addedIds, changedEdges, oldSlots) {
    var affectedDomains = {}, added = {};
    addedIds.forEach(function (id) {
      added[id] = true;
      var domainId = ctx.domainOf[id];
      if (domainId) affectedDomains[domainId] = true;
    });
    // Edge-only topology changes can alter degrees/slots without adding a node.
    (changedEdges || []).forEach(function (edge) {
      edgeIds(edge).forEach(function (id) {
        var domainId = ctx.domainOf[id];
        if (domainId) affectedDomains[domainId] = true;
      });
    });
    var moving = {}, targets = {}, anchorOf = {}, roots = sessionRoots(ctx.nodes, edges);
    var parentOf = causalParents(ctx.nodes, edges);
    ctx.nodes.forEach(function (node) {
      var domainId = ctx.domainOf[node.id];
      var target = ctx.slotOf[node.id];
      var before = oldSlots && oldSlots[node.id];
      var changed = !!added[node.id] || !!(before && target
        && (before.x !== target.x || before.y !== target.y));
      if (node.kind === 'domain' || !changed || !target) return;
      // Collision resolution may make a neighbouring domain yield. Target the
      // exact nodes whose canonical slots changed, never the whole domain.
      if (domainId) affectedDomains[domainId] = true;
      moving[node.id] = true;
      targets[node.id] = { x: target.x, y: target.y };
      // A newly revealed effect emerges beside its immediate observed cause,
      // not from the session/domain hub. Existing nodes keep their current
      // coordinates and only interpolate when their compact canonical slot
      // actually changes.
      anchorOf[node.id] = parentOf[node.id] || roots[node.id] || domainId;
    });
    return { moving: moving, targets: targets, anchorOf: anchorOf,
      affectedDomains: affectedDomains };
  }

  function animateTraceExpansion(ctx, local, sim, renderer, requestFrame) {
    var targetIds = Object.keys(local.targets);
    var state = sim._traceAnimationState;
    if (!state && !targetIds.length) return 0;
    if (!state) {
      state = {
        token: (sim._traceAnimationToken || 0) + 1,
        clock: null,
        targets: {},
        entries: {},
      };
      sim._traceAnimationToken = state.token;
      sim._traceAnimationState = state;
    }
    targetIds.forEach(function (id) {
      var node = ctx.byId[id];
      state.targets[id] = { x: local.targets[id].x, y: local.targets[id].y };
      var entry = state.entries[id];
      if (entry) {
        // Retarget from the currently rendered point without extending this
        // node's deadline. Repeated stream deltas therefore cannot starve it.
        entry.start = { x: node.x, y: node.y };
        if (entry.startedAt != null) entry.startedAt = state.clock;
        entry.target = state.targets[id];
      } else {
        // A node arriving late gets one complete transition of its own rather
        // than inheriting an almost-expired global wave and snapping into place.
        state.entries[id] = {
          start: { x: node.x, y: node.y },
          startedAt: null,
          endsAt: null,
          target: state.targets[id],
        };
      }
    });
    sim._tracePendingTargets = state.targets;
    if (state.scheduled) return TRACE_REFLOW_MS;
    state.scheduled = true;

    function step(timestamp) {
      if (sim._traceAnimationToken !== state.token
          || sim._traceAnimationState !== state) return;
      state.clock = timestamp;
      Object.keys(state.entries).forEach(function (id) {
        var node = ctx.byId[id], entry = state.entries[id];
        if (entry.startedAt == null) {
          entry.startedAt = timestamp;
          entry.endsAt = timestamp + TRACE_REFLOW_MS;
        }
        var span = Math.max(Number.EPSILON, entry.endsAt - entry.startedAt);
        var progress = Math.min(1, Math.max(0, (timestamp - entry.startedAt) / span));
        node.x = progress === 1 ? entry.target.x
          : entry.start.x + (entry.target.x - entry.start.x) * progress;
        node.y = progress === 1 ? entry.target.y
          : entry.start.y + (entry.target.y - entry.start.y) * progress;
        node.vx = 0; node.vy = 0; node.fx = node.x; node.fy = node.y;
        if (progress === 1) {
          delete state.entries[id];
          delete state.targets[id];
        }
      });
      if (Object.keys(state.entries).length) {
        if (renderer.redraw) renderer.redraw();
        requestFrame(step);
      } else {
        sim._tracePendingTargets = {};
        sim._traceAnimationState = null;
        if (renderer.redrawNow) renderer.redrawNow();
      }
    }
    requestFrame(step);
    return TRACE_REFLOW_MS;
  }

  window.JUG = window.JUG || {};
  window.JUG._wfg = window.JUG._wfg || {};
  window.JUG._wfg.packTraceExpansion = packTraceExpansion;
  window.JUG._wfg.animateTraceExpansion = animateTraceExpansion;
})();
