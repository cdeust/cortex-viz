// Cortex Neural Graph — throttled full-snapshot stream loader.
//
// Consumes /api/graph/full/stream (NDJSON frames — see
// http_standalone_fullstream.py for the wire shape) through a bounded
// queue so the SAME total graph that used to arrive as one ~1.17 GB JSON
// document (which response.json() cannot hold — V8 string cap) lands as
// many small frames the page ingests without ever blocking a frame for
// long.
//
// Flow control, both directions:
//   * BACKPRESSURE (network → queue): reader.read() is not called while
//     the queue holds >= HIGH_WATER frames; it resumes below LOW_WATER.
//     The TCP window then throttles the server — the browser never
//     buffers the whole body.
//   * THROTTLE (queue → scene): a requestAnimationFrame pump drains
//     frames only while the elapsed time this frame is under
//     FRAME_BUDGET_MS, so ingest self-paces to what the machine can
//     apply — fast machines drain more per frame, slow ones fewer.
//     Frames drained in one pass are coalesced into ONE apply call.
//
// FRAME_BUDGET_MS = 25: half the 50 ms long-task threshold (RAIL model —
// web.dev/rail; tasks over 50 ms are what users perceive as jank), leaving
// the other half for the page's own rendering work in the same frame.
// HIGH_WATER 8 / LOW_WATER 2 frames of ≤ ~1 MB bound queue memory at
// ~8 MB — frame sizes are set server-side (snapshot_pg_store frames /
// _MIN_LINE_BYTES coalescing).

window.JUG = window.JUG || {};

(function () {
  'use strict';

  var HIGH_WATER = 8;
  var LOW_WATER = 2;
  var FRAME_BUDGET_MS = 25;

  // Split a network chunk into complete NDJSON lines; carry the partial
  // tail until the next chunk. Returns parsed frame objects.
  function LineParser() {
    this.tail = '';
  }
  LineParser.prototype.push = function (text) {
    var lines = (this.tail + text).split('\n');
    this.tail = lines.pop();
    var frames = [];
    for (var i = 0; i < lines.length; i++) {
      if (lines[i]) frames.push(JSON.parse(lines[i]));
    }
    return frames;
  };

  // JUG.streamFullGraph({apply, onProgress}) → Promise<result>
  //   apply(nodes, edges)      — called from the pump with coalesced batches
  //   onProgress({nodes, edges, node_total, edge_total}) — after each pump pass
  // Resolves {ok:true, nodes, edges} after the server's done frame and a
  // fully drained queue; {ok:false, status} when the endpoint is absent
  // (older server) or the snapshot is still warming — callers fall back.
  JUG.streamFullGraph = function (opts) {
    var apply = opts.apply;
    var onProgress = opts.onProgress || function () {};

    return fetch('/api/graph/full/stream').then(function (r) {
      if (!r.ok || !r.body) return { ok: false, status: r.status };

      var reader = r.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var parser = new LineParser();
      var queue = [];
      var readerDone = false;
      var readerIdle = false;
      var sawDone = false;
      var counts = { nodes: 0, edges: 0, node_total: 0, edge_total: 0 };

      return new Promise(function (resolve, reject) {
        var settled = false;
        function fail(err) {
          if (!settled) { settled = true; reject(err); }
        }

        function readMore() {
          if (readerDone || settled) return;
          if (queue.length >= HIGH_WATER) { readerIdle = true; return; }
          readerIdle = false;
          reader.read().then(function (res) {
            if (res.done) {
              readerDone = true;
              var last = parser.push(decoder.decode());
              for (var i = 0; i < last.length; i++) queue.push(last[i]);
              return;
            }
            var frames = parser.push(decoder.decode(res.value, { stream: true }));
            for (var i = 0; i < frames.length; i++) {
              var f = frames[i];
              if (f.node_total != null) {
                counts.node_total = f.node_total;
                counts.edge_total = f.edge_total || 0;
              }
              queue.push(f);
            }
            readMore();
          }).catch(fail);
        }

        function pump() {
          if (settled) return;
          var t0 = performance.now();
          var nodes = [];
          var edges = [];
          while (queue.length && (performance.now() - t0) < FRAME_BUDGET_MS) {
            var f = queue.shift();
            if (f.nodes) nodes.push.apply(nodes, f.nodes);
            if (f.edges) edges.push.apply(edges, f.edges);
            if (f.meta) counts.meta = f.meta;
            if (f.done) sawDone = true;
          }
          if (nodes.length || edges.length) {
            apply(nodes, edges);
            counts.nodes += nodes.length;
            counts.edges += edges.length;
            onProgress(counts);
          }
          if (readerIdle && queue.length <= LOW_WATER) readMore();
          if (readerDone && !queue.length) {
            settled = true;
            // COMPLETENESS GATE — the response is close-delimited (no
            // Content-Length), so a dropped connection mid-stream is
            // transport-indistinguishable from completion. Only the
            // server's terminal {"done":true} frame plus counts equal to
            // the header totals prove the full graph arrived; anything
            // less must never latch as "full" (no lossy load — user
            // direction 2026-06-12).
            var complete = sawDone
              && counts.nodes === counts.node_total
              && counts.edges === counts.edge_total;
            if (!complete) {
              console.warn('[stream] incomplete full-graph stream:',
                counts.nodes + '/' + counts.node_total + ' nodes,',
                counts.edges + '/' + counts.edge_total + ' edges,',
                'done=' + sawDone);
            }
            resolve({ ok: complete, truncated: !complete,
                      nodes: counts.nodes, edges: counts.edges,
                      meta: counts.meta || {} });
            return;
          }
          requestAnimationFrame(pump);
        }

        readMore();
        requestAnimationFrame(pump);
      });
    });
  };
})();
