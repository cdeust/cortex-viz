// Cortex Brain View — impact-diagram shim.
//
// The shared detail panel (trace_detail.js) opens a file's dependency /
// blast-radius diagram via window.TraceView.showImpact(path). In the galaxy
// that lives in trace.js (the trace view), which the brain doesn't load — so
// the "Open the impact diagram" link did nothing here. This provides a
// self-contained TraceView.showImpact that fetches the SAME /api/trace/impact
// endpoint and renders into the SAME #flow-panel with the SAME .impact-*
// classes panels.css styles — no trace.js dependency.

window.TraceView = window.TraceView || {};

(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function shortName(s, n) {
    s = String(s || ''); n = n || 32;
    return s.length > n ? '…' + s.slice(s.length - n + 1) : s;
  }

  // One impact group (a titled list of edges), mirroring trace.js's
  // _impactGroup/_impactFiles output so panels.css styles it identically.
  function group(title, items, dir) {
    items = items || [];
    if (!items.length) return '';
    var arrow = dir === 'up' ? '←' : dir === 'down' ? '→' : '·';
    var h = '<div class="impact-group"><div class="impact-group-title">' +
      esc(title) + ' <span class="impact-count">' + items.length + '</span></div>';
    items.slice(0, 60).forEach(function (it) {
      var conf = typeof it.confidence === 'number'
        ? ' <span class="impact-conf">' + Math.round(it.confidence * 100) + '%</span>' : '';
      h += '<div class="impact-box" data-file="' + esc(it.file || '') + '">' +
        '<span class="impact-arrow">' + arrow + '</span>' +
        '<span class="impact-name">' + esc(it.label || it.name || it.file || '?') + '</span>' +
        '<span class="impact-edge">' + esc(it.kind || it.edge || '') + conf + '</span></div>';
    });
    if (items.length > 60) {
      h += '<div class="impact-loading">… ' + (items.length - 60) + ' more</div>';
    }
    return h;
  }

  function buildHtml(d) {
    var center = d.center || {};
    var h = '<div class="impact-center">' +
      esc(center.label || center.file || 'this file') + '</div>';
    h += group('Depends on (files)', d.depends_on, 'down');
    h += group('Depended on by (files)', d.depended_on_by, 'up');
    h += group('References (docs → files)', d.references, 'down');
    h += group('Referenced by (docs)', d.referenced_by, 'up');
    h += group('Calls / imports (symbols)', d.downstream, 'down');
    h += group('Called / imported by (symbols)', d.upstream, 'up');
    h += group('Defines', d.members, 'flat');
    var empty = ['depends_on', 'depended_on_by', 'references', 'referenced_by',
      'downstream', 'upstream', 'members'].every(function (k) {
      return !(d[k] || []).length;
    });
    if (empty) {
      h += '<div class="impact-loading">No dependencies found in the code-graph.</div>';
    }
    return h;
  }

  function closeFlow() {
    var p = document.getElementById('flow-panel');
    if (p) p.classList.remove('open');
  }

  function basename(p) {
    p = String(p || '').replace(/[:#].*$/, '');   // drop symbol::qualifier tails
    var i = p.lastIndexOf('/');
    return i >= 0 ? p.slice(i + 1) : p;
  }

  // Impact items carry a repo-RELATIVE file (or a qualified-name prefix); the
  // brain's file nodes carry an ABSOLUTE path. Resolve by exact path, then by
  // path suffix (node.path endsWith "/<relfile>"), then by basename — returning
  // the brain node id so the camera can fly to it. Lazily indexes the file
  // nodes once from JUG._nodeIndex (set by detail_bridge.js).
  function fileIndex() {
    if (BRAIN._fileNodes) return BRAIN._fileNodes;
    var arr = [];
    var idx = window.JUG && JUG._nodeIndex;
    if (idx && idx.forEach) {
      idx.forEach(function (n, id) {
        if ((n.kind || n.type) === 'file' && n.path) {
          arr.push({ id: id, path: String(n.path), base: basename(n.path) });
        }
      });
    }
    BRAIN._fileNodes = arr;
    return arr;
  }

  function resolveNodeId(dataFile) {
    if (!dataFile) return null;
    var rel = String(dataFile).replace(/[:#].*$/, '').replace(/^\.\//, '');
    if (!rel) return null;
    var nodes = fileIndex();
    var i;
    // 1. exact absolute path.
    for (i = 0; i < nodes.length; i++) if (nodes[i].path === rel) return nodes[i].id;
    // 2. path suffix — ONLY when rel has a directory component. A bare
    //    basename ("README.md") would suffix-match every same-named file
    //    ("/a/README.md", "/b/README.md"), so it must fall through to the
    //    unique-only basename check instead of picking one arbitrarily.
    if (rel.indexOf('/') >= 0) {
      var best = null;
      for (i = 0; i < nodes.length; i++) {
        if (nodes[i].path.endsWith('/' + rel) &&
            (!best || nodes[i].path.length < best.path.length)) best = nodes[i];
      }
      if (best) return best.id;
    }
    // 3. basename fallback — unique match only, to avoid flying to the wrong
    //    file when several share a name.
    var base = basename(rel), hit = null, many = false;
    for (i = 0; i < nodes.length; i++) {
      if (nodes[i].base === base) { if (hit) { many = true; break; } hit = nodes[i]; }
    }
    return (hit && !many) ? hit.id : null;
  }

  // Wire every impact box: clicking flies the camera to that file node (and
  // opens its detail card) via JUG.selectNodeById (detail_bridge.js). Boxes
  // whose target isn't in the graph are marked non-navigable.
  function wireBoxes(container) {
    container.querySelectorAll('.impact-box[data-file]').forEach(function (el) {
      var id = resolveNodeId(el.getAttribute('data-file'));
      if (!id) { el.classList.add('impact-box--dead'); return; }
      el.style.cursor = 'pointer';
      el.addEventListener('click', function () {
        if (window.JUG && JUG.selectNodeById) JUG.selectNodeById(id);
      });
    });
  }

  TraceView.showImpact = function (path) {
    var panel = document.getElementById('flow-panel');
    var content = document.getElementById('flow-content');
    var title = document.getElementById('flow-title');
    if (!panel || !content) return;
    if (title) title.textContent = 'Impact · ' + shortName(String(path).split('/').pop(), 32);
    content.innerHTML = '<div class="impact-loading">analyzing dependencies…</div>';
    panel.classList.add('open');
    // Sit beside the detail panel when it's open (both dock right).
    var detail = document.getElementById('detail-panel');
    panel.classList.toggle('with-detail', !!(detail && detail.classList.contains('open')));

    fetch('/api/trace/impact?path=' + encodeURIComponent(path))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { content.innerHTML = '<div class="impact-loading">No dependency data.</div>'; return; }
        content.innerHTML = buildHtml(d);
        wireBoxes(content);   // click a box → fly to that file node in the brain
      })
      .catch(function (e) {
        content.innerHTML = '<div class="impact-loading">Impact failed: ' + esc(e && e.message) + '</div>';
      });
  };

  document.addEventListener('DOMContentLoaded', function () {
    var close = document.getElementById('flow-close');
    if (close) close.addEventListener('click', closeFlow);
    window.addEventListener('keydown', function (e) {
      // Guard form controls (matches detail_panel.js:492) — otherwise Escape
      // typed inside the brain-view search box bubbles here and closes the
      // impact flow panel (losing the fetched diagram) instead of just
      // closing the search dropdown.
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'Escape') closeFlow();
    });
  });

  // Exposed for tests: repo-relative impact file → brain node id.
  TraceView._resolveNodeId = resolveNodeId;
})();
