// Cortex Brain View — node search worker.
//
// Runs the trigram scan off the main thread: at ~300k nodes a full scan costs
// tens of ms, well past the 16.7 ms/frame budget of the Three.js render loop
// at 60 Hz (RAIL model, animation budget) — see tasks/todo.md §2.
//
// Frozen message contract (main.js / search.js side owns the other half):
//   main -> worker  {type:'index', nodes:[{id,label,path,kind}]}   (once)
//   main -> worker  {type:'query', q, seq, limit}
//   worker -> main  {type:'ready', count, elapsed_ms}
//   worker -> main  {type:'results', seq, total, elapsed_ms, items}
// items = top `limit` by score desc/label asc/id asc; only score >=
// TRGM.SIMILARITY_THRESHOLD counts toward `total` or appears in `items`.
// Stale-seq discarding is the main thread's responsibility, not the worker's.

importScripts('/brain/js/trigram.js');

(function () {
  'use strict';

  var TRGM = self.TRGM;
  var THRESHOLD = TRGM.SIMILARITY_THRESHOLD;

  var index = []; // [{id, label, kind, path, wordTriLists: BigInt[][]}]

  // Words from the last path segment(s): the filename ('a/b/file.py' ->
  // 'file.py') and, for qualified symbol names ('file.py::handle'), the
  // symbol segment too — so file and symbol nodes both match by their
  // human-readable tail, not their full path.
  function pathWords(path) {
    if (!path) return [];
    var bySlash = String(path).split('/');
    var last = bySlash[bySlash.length - 1] || '';
    var byQual = last.split('::');
    var symbol = byQual[byQual.length - 1] || '';
    var words = TRGM.indexWords(last);
    if (symbol !== last) words = words.concat(TRGM.indexWords(symbol));
    return words;
  }

  function uniqueWords(words) {
    var seen = Object.create(null);
    var out = [];
    for (var i = 0; i < words.length; i++) {
      if (!seen[words[i]]) {
        seen[words[i]] = true;
        out.push(words[i]);
      }
    }
    return out;
  }

  function buildIndex(nodes) {
    var out = new Array(nodes.length);
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var haystack = TRGM.indexWords(n.label || n.id || '');
      var words = uniqueWords(haystack.concat(pathWords(n.path)));
      var tri = new Array(words.length);
      for (var w = 0; w < words.length; w++) tri[w] = TRGM.wordTrigrams(words[w]);
      out[i] = {
        id: n.id,
        label: n.label || n.id,
        kind: n.kind,
        path: n.path || null,
        wordTriLists: tri,
      };
    }
    return out;
  }

  function compareResults(a, b) {
    if (b.score !== a.score) return b.score - a.score;
    if (a.label !== b.label) return a.label < b.label ? -1 : 1;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  }

  function runQuery(q, limit) {
    var tokens = TRGM.indexWords(q || '');
    if (tokens.length === 0) return { total: 0, items: [] };
    var queryTri = tokens.map(TRGM.wordTrigrams);
    var scored = [];
    for (var i = 0; i < index.length; i++) {
      var node = index[i];
      var score = TRGM.scoreNode(queryTri, node.wordTriLists);
      if (score >= THRESHOLD) {
        scored.push({ id: node.id, label: node.label, kind: node.kind, path: node.path, score: score });
      }
    }
    scored.sort(compareResults);
    return { total: scored.length, items: scored.slice(0, limit) };
  }

  self.onmessage = function (ev) {
    var msg = ev.data || {};
    if (msg.type === 'index') {
      var t0 = performance.now();
      index = buildIndex(msg.nodes || []);
      self.postMessage({ type: 'ready', count: index.length, elapsed_ms: performance.now() - t0 });
    } else if (msg.type === 'query') {
      var t1 = performance.now();
      var res = runQuery(msg.q, msg.limit || 20);
      self.postMessage({
        type: 'results',
        seq: msg.seq,
        total: res.total,
        elapsed_ms: performance.now() - t1,
        items: res.items,
      });
    }
  };
})();
