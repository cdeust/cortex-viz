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

  // The activity spine belongs to the GALAXY (workflow_graph payload).
  // appendGraphDelta writes into JUG.state.lastData, which FOLLOWS the active
  // view (workflow_graph_bridge.js) — while the Trace view (the default)
  // owns lastData, appending here would inject the replayed log's tens of
  // thousands of session/memory fragments into the trace tree (observed:
  // episodic clouds rendered around the trace hubs). Buffer the batches and
  // flush them once the galaxy payload is the live one.
  var _buf = [];
  function _galaxyLive() {
    // activeView gate (not just the schema): at page start lastData is still
    // null while the default Trace view boots — schema-only would append the
    // first replayed batches into a payload trace.js immediately replaces.
    var st = window.JUG && JUG.state;
    if (!st || st.activeView !== 'graph') return false;
    var d = st.lastData;
    return !(d && d.meta && d.meta.schema === 'trace.v1');
  }
  function _append(nodes, edges) {
    _ensureLastData();
    if (typeof JUG.appendGraphDelta === 'function') {
      JUG.appendGraphDelta(nodes, edges);
      console.log('[activity] +' + nodes.length + 'N +' + edges.length + 'E');
    }
  }
  // Re-entrance guard: _append emits state:lastData, which re-fires the
  // drain listener below while the while-loop is already draining.
  var _flushing = false;
  function _flush() {
    if (_flushing) return;
    _flushing = true;
    try {
      while (_buf.length && _galaxyLive()) {
        var b = _buf.shift();
        _append(b.nodes, b.edges);
      }
    } finally { _flushing = false; }
  }

  function _onBatch(ev) {
    var data;
    try { data = JSON.parse(ev.data); } catch (e) { return; }
    var nodes = (data.nodes || []).map(function (n) {
      return (n && typeof n === 'object') ? n : { id: String(n) };
    });
    var edges = data.edges || [];
    if (!nodes.length && !edges.length) return;
    if (!_galaxyLive()) { _buf.push({ nodes: nodes, edges: edges }); return; }
    if (_buf.length) _flush();
    _append(nodes, edges);
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
        // Same galaxy routing as the batches — buffer while trace owns lastData.
        if (!_galaxyLive()) { _buf.push({ nodes: d.nodes, edges: d.edges || [] }); return; }
        _append(d.nodes, d.edges || []);
      }).catch(function () {});
    } catch (_) {}
  }

  function start() {
    if (es) return es;
    if (typeof EventSource === 'undefined') return null;
    // No-DB mode: the durable session_activity log is PG-backed and the
    // stream answers 503 — an EventSource would 503-retry forever.
    // capabilities.js also calls stopActivityStream() when its probe
    // resolves after this boot call already opened the stream.
    if (window.JUG && JUG.capabilities && JUG.capabilities.db === false) return null;
    // Drain the deferred batches the moment the galaxy payload takes over
    // lastData (view switch to Graph re-emits state:lastData via the bridge).
    if (window.JUG && typeof JUG.on === 'function') {
      JUG.on('state:lastData', function () { if (_buf.length) _flush(); });
    }
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
