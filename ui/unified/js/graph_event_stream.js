// Live-build SSE subscriber — watches the graph grow on first visit.
//
// Counterpart to mcp_server/server/graph_event_stream.py. Opens an
// EventSource on /api/graph/events, parses each `batch` event, and
// calls JUG.appendGraphDelta with the nodes/edges. appendGraphDelta
// dedupes by id, so a reconnect-with-Last-Event-ID replay is safe.
//
// Public API:
//   GraphEventStream.start({onProgress?, onDone?})
//     Opens the EventSource (or returns the existing one if already
//     open). Idempotent.
//
//   GraphEventStream.stop()
//     Closes the EventSource and stops appending. Used when the user
//     switches away from the Graph tab, or when the build emits
//     `done` (we close ourselves there too).
(function () {
  var es = null;
  var stats = { batches: 0, nodes: 0, edges: 0, lastLabel: '', startedAt: 0, doneAt: 0 };
  var callbacks = { onProgress: null, onDone: null };

  function _ensureLastData() {
    if (!window.JUG) return false;
    if (!JUG.state.lastData) {
      JUG.state.lastData = {
        nodes: [], edges: [], links: [],
        meta: { schema: 'workflow_graph.v1', source: 'live-stream' },
      };
    }
    return true;
  }

  function _onBatchEvent(ev) {
    var data;
    try { data = JSON.parse(ev.data); } catch (e) {
      console.warn('[stream] bad batch event:', e);
      return;
    }
    if (!_ensureLastData()) return;
    // Decode the SLIM positional wire format at the browser boundary.
    // The server streams nodes as `[id, kind, x, y]` lists, but
    // appendGraphDelta (and every downstream consumer) is a DICT
    // consumer — it tests `!n.id`, so a positional list (whose `.id`
    // is undefined) is silently dropped and never renders. Decode here,
    // the single entry point where SSE nodes enter the browser, so no
    // slim list ever reaches a dict consumer. (Liskov: keep the
    // consumer contract; convert at the boundary.)
    var rawNodes = data.nodes || data.n || [];
    var nodes = rawNodes.map(function (n) {
      return Array.isArray(n) ? { id: n[0], kind: n[1], x: n[2], y: n[3] } : n;
    });
    var edges = data.edges || [];
    if (typeof JUG.appendGraphDelta === 'function') {
      JUG.appendGraphDelta(nodes, edges);
    }
    stats.batches += 1;
    stats.nodes += nodes.length;
    stats.edges += edges.length;
    stats.lastLabel = data.label || stats.lastLabel;
    if (callbacks.onProgress) {
      try {
        callbacks.onProgress({
          batches: stats.batches,
          nodes: stats.nodes,
          edges: stats.edges,
          label: stats.lastLabel,
          off: data.off, n_total: data.n_total, e_total: data.e_total,
        });
      } catch (_) {}
    }
  }

  function _onDoneEvent(ev) {
    stats.doneAt = Date.now();
    var elapsed = stats.doneAt - stats.startedAt;
    console.log(
      '[stream] done — ' + stats.batches + ' batches, ' +
      stats.nodes + ' nodes, ' + stats.edges + ' edges in ' +
      Math.round(elapsed) + ' ms'
    );
    if (callbacks.onDone) {
      try { callbacks.onDone({ ...stats, elapsed_ms: elapsed }); } catch (_) {}
    }
    stop();
  }

  function _onError(ev) {
    // EventSource auto-reconnects on transient errors using the last
    // received id, so we don't need to do anything except log. If the
    // server is genuinely down, repeated reconnects will keep failing —
    // bounded by the browser, not by us.
    console.warn('[stream] error (will retry):', ev);
  }

  function start(opts) {
    opts = opts || {};
    callbacks.onProgress = opts.onProgress || null;
    callbacks.onDone = opts.onDone || null;
    if (es) {
      // Already open — just update callbacks.
      return es;
    }
    stats.startedAt = Date.now();
    es = new EventSource('/api/graph/events');
    es.addEventListener('batch', _onBatchEvent);
    es.addEventListener('done', _onDoneEvent);
    es.addEventListener('error', _onError);
    console.log('[stream] EventSource opened on /api/graph/events');
    return es;
  }

  function stop() {
    if (es) {
      try { es.close(); } catch (_) {}
      es = null;
      console.log('[stream] EventSource closed');
    }
  }

  function isOpen() {
    return es !== null && es.readyState !== EventSource.CLOSED;
  }

  function getStats() {
    return Object.assign({}, stats, { open: isOpen() });
  }

  window.GraphEventStream = { start: start, stop: stop, isOpen: isOpen, stats: getStats };
})();
