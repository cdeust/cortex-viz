// Cortex — Workflow Graph bridge.
// Detects workflow-graph-shaped data (nodes with `kind` in the new schema)
// and takes over the "graph" tab — hides every child of #graph-container
// except our wrapper, disables the legacy force-graph animation, renders
// via JUG.renderWorkflowGraph. Falls back to the legacy renderer for old
// (type-based) payloads.
(function () {
  var LOG = '[wfg]';
  var WFG_KINDS = {
    domain: 1, skill: 1, command: 1, hook: 1, agent: 1,
    tool_hub: 1, file: 1, memory: 1, discussion: 1, entity: 1,
  };
  var _handle = null;
  var _wrapperId = 'wfg-container';
  var _lastPayload = null;
  // Graph and Trace share ONE wrapper/handle but hold DIFFERENT payloads.
  // Cache the last payload PER view so switching tabs re-renders the target
  // view's OWN graph instead of reflowing whatever was last drawn (which left
  // the other tab's content on screen). Keyed by view id ('graph' | 'trace').
  var _byView = {};
  function _viewOf(data) {
    var s = data && data.meta && data.meta.schema;
    return s === 'trace.v1' ? 'trace' : 'graph';
  }

  function isWorkflowGraph(data) {
    if (!data || !Array.isArray(data.nodes) || data.nodes.length === 0) return false;
    if (data.meta && data.meta.schema === 'trace.v1') return true;
    if (data.meta && data.meta.schema === 'workflow_graph.v1') return true;
    for (var i = 0; i < Math.min(data.nodes.length, 50); i++) {
      var k = data.nodes[i].kind;
      if (k && WFG_KINDS[k]) return true;
    }
    return false;
  }

  function ensureWrapper() {
    var host = document.getElementById('graph-container');
    if (!host) return null;
    var wrapper = document.getElementById(_wrapperId);
    if (!wrapper) {
      wrapper = document.createElement('div');
      wrapper.id = _wrapperId;
      wrapper.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:5;';
      host.appendChild(wrapper);
    }
    return wrapper;
  }

  function hideLegacyRenderer() {
    var host = document.getElementById('graph-container');
    if (!host) return;
    var kids = host.childNodes;
    for (var i = 0; i < kids.length; i++) {
      var node = kids[i];
      if (node.nodeType !== 1) continue;
      if (node.id === _wrapperId) continue;
      // display:none leaves the element in the DOM and (in Safari) can
      // still produce a compositing layer while it's detached-rendering.
      // remove() evicts the element entirely so there's no hidden box.
      if (node.parentNode) node.parentNode.removeChild(node);
      i--;
    }
    if (window.JUG && typeof JUG.getGraph === 'function') {
      var g = JUG.getGraph();
      if (g && typeof g.pauseAnimation === 'function') {
        try { g.pauseAnimation(); } catch (_) {}
      }
    }
  }

  // Continuously evict legacy children that re-materialise after first
  // render (the force-graph library and JUG.setGraphData both re-mount
  // canvases asynchronously). Run while the workflow graph is the
  // active renderer.
  var _observer = null;
  function watchLegacy() {
    var host = document.getElementById('graph-container');
    if (!host || _observer) return;
    _observer = new MutationObserver(function () { hideLegacyRenderer(); });
    _observer.observe(host, { childList: true });
  }

  function render(data) {
    try {
      var wrapper = ensureWrapper();
      if (!wrapper) { console.warn(LOG, 'no #graph-container'); return false; }
      // Mark the D3 workflow-graph as THE active renderer so graph.js's
      // appendGraphDelta stops also rebuilding the legacy force-graph on
      // every update. Without this both renderers ran on every delta
      // (double work) — the bug the audit surfaced. This is the single
      // point where D3 takes over the canvas.
      if (window.JUG) JUG.__wfgActive = true;
      hideLegacyRenderer();
      if (_handle && typeof _handle.destroy === 'function') {
        try { _handle.destroy(); } catch (_) {}
      }
      if (!window.JUG || typeof JUG.renderWorkflowGraph !== 'function') {
        console.warn(LOG, 'renderWorkflowGraph missing — retry in 80ms');
        setTimeout(function () { render(data); }, 80);
        return false;
      }
      _handle = JUG.renderWorkflowGraph(wrapper, data);
      _lastPayload = data;
      _byView[_viewOf(data)] = data;          // remember this view's payload
      watchLegacy();
      console.log(LOG, 'rendered', (data.nodes || []).length, 'nodes /',
                  (data.edges || data.links || []).length, 'edges');
      return true;
    } catch (err) {
      console.error(LOG, 'render failed', err);
      return false;
    }
  }

  function attach() {
    if (!window.JUG || !JUG.on) { setTimeout(attach, 50); return; }
    console.log(LOG, 'bridge attached');
    // Debounce so a burst of phase-appends collapse into ONE
    // destroy-and-recreate. With 10k+ symbol nodes a per-phase render
    // freezes the browser; we wait until the stream quiets for 1.2 s
    // before rebuilding the simulation. A safety deadline ensures a
    // first render happens even if data keeps streaming.
    var _pendingRender = null;
    var _renderTimer = null;
    var _firstRenderDone = false;
    var _firstDeadline = null;
    JUG.on('state:lastData', function (ev) {
      var data = ev && ev.value;
      if (!isWorkflowGraph(data)) return;
      // ── Incremental fast-path (the streaming hot path) ──
      // Every SSE batch and phase-load lands here via appendGraphDelta, which
      // emits a `delta` of ONLY the newly-added nodes/edges (graph.js). If the
      // live handle already renders THIS view, push that delta into the
      // existing simulation (handle.append → workflow_graph.js:append mutates
      // ctx.nodes/ctx.edges in place and gently reheats) instead of
      // destroy()+re-mount, which re-seeds and re-simulates all N nodes and is
      // what made the galaxy re-shuffle/freeze on every streamed action. A
      // wholesale reference swap (trace clear, wiki) emits via the state
      // setter with NO delta and falls through to the debounced remount below.
      // source: galaxy-lag audit (tasks/galaxy-lag-and-ap-aggregation-audit.md).
      var delta = ev && ev.delta;
      var haveDelta = !!(delta && ((delta.nodes && delta.nodes.length) ||
                                   (delta.edges && delta.edges.length)));
      if (_firstRenderDone && _handle && typeof _handle.append === 'function'
          && haveDelta && _lastPayload
          && _viewOf(_lastPayload) === _viewOf(data)) {
        try {
          _handle.append(delta.nodes || [], delta.edges || []);
          // The accumulated payload object is mutated in place (same ref), so
          // _lastPayload / _byView already point at it — a later view switch
          // re-renders the full accumulated graph, not a stale snapshot.
          _lastPayload = data;
          _byView[_viewOf(data)] = data;
          return;
        } catch (e) {
          console.warn(LOG, 'incremental append failed — full remount', e);
          // fall through to the debounced destroy+remount below
        }
      }
      _pendingRender = data;
      if (_renderTimer) clearTimeout(_renderTimer);
      // 400 ms first render, then adaptive. A re-render re-mounts and
      // re-lays-out the WHOLE graph, so on the large galaxy (≥15k) — where the
      // live activity stream appends a few nodes per captured Claude action —
      // coalesce those into an INFREQUENT rebuild (8 s) so the galaxy stays
      // fluid instead of re-shuffling on every streamed tool call. Small
      // graphs keep the snappy 500 ms cadence.
      var bigGraph = !!(data && data.nodes && data.nodes.length > 15000);
      var wait = !_firstRenderDone ? 400 : (bigGraph ? 8000 : 500);
      _renderTimer = setTimeout(function(){
        _renderTimer = null;
        var d = _pendingRender; _pendingRender = null;
        if (d) { render(d); _firstRenderDone = true; }
      }, wait);
      // Safety net: force a render if appends keep coming faster than the
      // debounce — longer on the big galaxy to match the rebuild cadence.
      if (!_firstDeadline) {
        _firstDeadline = setTimeout(function(){
          _firstDeadline = null;
          if (_pendingRender) {
            var d = _pendingRender; _pendingRender = null;
            if (_renderTimer) { clearTimeout(_renderTimer); _renderTimer = null; }
            render(d); _firstRenderDone = true;
          }
        }, bigGraph ? 12000 : 5000);
      }
    });
    if (JUG.state && isWorkflowGraph(JUG.state.lastData)) render(JUG.state.lastData);

    JUG.on('state:activeView', function (ev) {
      // The Trace view (default) renders through the SAME workflow-graph
      // wrapper as the legacy Graph view did, so it must show the wrapper
      // + reflow exactly the same way. Without this the bridge renders
      // into a hidden div and the user sees only the base node.
      var v = ev && ev.value;
      var isGraphic = (v === 'graph' || v === 'trace');
      // CRITICAL: #wfg-container carries z-index:5 but #graph-container is
      // z-index:auto, so it does NOT contain that stacking context — the wrapper
      // therefore paints OVER the Knowledge / Wiki / Board / Pipeline containers
      // (z-index:auto siblings), and they render but stay hidden behind the
      // galaxy ("not loading anything"). Hide the whole #graph-container on those
      // views so the selected view is visible; restore it for graph + trace,
      // which render INTO it.
      var host = document.getElementById('graph-container');
      if (host) host.style.display = isGraphic ? '' : 'none';
      if (isGraphic) {
        var w = document.getElementById(_wrapperId);
        if (w) w.style.display = 'block';
        hideLegacyRenderer();
        // Re-render the TARGET view's OWN payload so switching graph<->trace
        // refreshes to the right graph instead of leaving the other tab on
        // screen. Only reflow when the handle ALREADY holds this view's data;
        // if neither is true the view's own loader (the galaxy phase-poller for
        // graph, trace.js _boot for trace) will fetch + render it fresh.
        if (_byView[v]) {
          render(_byView[v]);
        } else if (_lastPayload && _viewOf(_lastPayload) === v
                   && _handle && typeof _handle.reflow === 'function') {
          setTimeout(function () { _handle.reflow(); }, 60);
        }
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attach);
  } else {
    attach();
  }

  window.JUG = window.JUG || {};
  window.JUG.renderWorkflowGraphIntoTab = render;
  window.JUG.isWorkflowGraph = isWorkflowGraph;
})();
