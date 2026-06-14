// Cortex-viz — live session-activity consumer.
//
// Opens an EventSource on /api/activity/stream and feeds each captured Claude
// action (tool, MCP call, file read/edit/write, terminal command, skill,
// subagent, prompt) into the graph via JUG.appendGraphDelta as a directional
// fragment (session → action → target). Same wire format ('batch' events) and
// same dedup-by-id append path as the build's graph_event_stream.js, so the
// live activity spine merges into the same galaxy in real time.
//
// Public API:
//   window.JUG.startActivityStream()  — open (idempotent)
//   window.JUG.stopActivityStream()   — close
(function () {
  'use strict';
  var es = null;

  function _ensureLastData() {
    if (window.JUG && !JUG.state.lastData) {
      JUG.state.lastData = {
        nodes: [], edges: [], links: [],
        meta: { schema: 'workflow_graph.v1', source: 'activity-stream' },
      };
    }
  }

  function _onBatch(ev) {
    var data;
    try { data = JSON.parse(ev.data); } catch (e) { return; }
    var nodes = (data.nodes || []).map(function (n) {
      return (n && typeof n === 'object') ? n : { id: String(n) };
    });
    var edges = data.edges || [];
    if (!nodes.length && !edges.length) return;
    _ensureLastData();
    if (typeof JUG.appendGraphDelta === 'function') {
      JUG.appendGraphDelta(nodes, edges);
      console.log('[activity] +' + nodes.length + 'N +' + edges.length + 'E');
    }
  }

  // One-shot fetch of PRD document/section nodes (third bridge,
  // prd-spec-generator). Empty until a PRD is generated, then the nodes
  // appear in the graph. Best-effort; never blocks the activity stream.
  function _loadPrd() {
    try {
      fetch('/api/prd').then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (d) {
        if (!d || !d.nodes || !d.nodes.length) return;
        _ensureLastData();
        if (typeof JUG.appendGraphDelta === 'function') {
          JUG.appendGraphDelta(d.nodes, d.edges || []);
          console.log('[prd] +' + d.nodes.length + 'N +' + (d.edges || []).length + 'E');
        }
      }).catch(function () {});
    } catch (_) {}
  }

  function start() {
    if (es) return es;
    if (typeof EventSource === 'undefined') return null;
    _loadPrd();
    // since=0 replays the durable session_activity log, then tails live.
    es = new EventSource('/api/activity/stream?since=0');
    es.addEventListener('batch', _onBatch);
    es.addEventListener('error', function () { /* EventSource auto-reconnects */ });
    return es;
  }

  function stop() {
    if (es) { try { es.close(); } catch (_) {} es = null; }
  }

  window.JUG = window.JUG || {};
  window.JUG.startActivityStream = start;
  window.JUG.stopActivityStream = stop;
})();
