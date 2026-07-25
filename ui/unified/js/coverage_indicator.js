// Cortex — Coverage honesty indicator (issue #36): the on-screen surface.
//
// Composition root for the coverage feature. It gathers the live signals
// (rendered topology, store totals, stream outcome, build progress, engine
// parse-coverage), asks the pure model (coverage_model.js) for a verdict, and
// renders a NON-MODAL per-view indicator with a drill-down of specific
// omissions — CBM's MissedCallout, in this app's HUD idiom.
//
// Layer note (coding-standards §2): all decision logic lives in JUG.Coverage
// (pure, DOM-free). This file only READS globals other modules publish
// (JUG.__wfgRendered, JUG.__storeMeta, JUG.__streamResult, JUG.__progress),
// fetches the engine read-path, and writes the DOM. The render function and
// source-gatherer are exposed on JUG.CoverageIndicator as a read-only test
// seam so every emission — including the quiet "complete" affordance
// (criterion 5) — is asserted directly (criterion 6 / §13 A3).
(function () {
  'use strict';

  var JUG = (window.JUG = window.JUG || {});

  // ── View registry (criterion 1) ───────────────────────────────────────────
  // The views that render an INCOMPLETE graph model, each declaring how its
  // completeness is measured. In cortex-viz the three graphs the issue names
  // are fused/served by the workflow-graph renderer:
  //   * graph — the neural galaxy: the codebase graph AND the memory graph
  //     overlaid on one canvas, drawn from the /api/graph/full snapshot. Store
  //     totals are a meaningful denominator (nodes rendered vs nodes in store).
  //   * trace — the workflow graph: session→prompt→action→file chains that
  //     stream a subtree ON DEMAND (expand-on-select), so there is no
  //     store-wide denominator; its incompleteness is the named
  //     'on-demand-expansion' mode, not a missing-count.
  // List views (knowledge / wiki / board) are paginated complete-across-
  // continuation lists, not graph renders, and carry no coverage indicator.
  var VIEWS = {
    graph: { label: 'Galaxy', denominatorMeaningful: true },
    trace: { label: 'Trace', denominatorMeaningful: false },
  };

  var COVERAGE_URL = '/api/graph/coverage';
  // Engine coverage is polled at most this often (ms). The parse-coverage
  // index changes only when the engine re-indexes — a slow signal — so a
  // 30 s cadence (matching polling.js's stats poll) is ample and cheap.
  // source: matches _STATS_TTL / stats poll interval (polling.js setInterval 30000).
  var ENGINE_POLL_MS = 30000;
  var _lastEngineFetch = 0;
  var _engineInflight = false;

  function activeView() {
    return (JUG.state && JUG.state.activeView) || null;
  }

  // Gather the live sources for `view`. Pre: the publishing modules have run
  // (guarded — every field defaults to null when its producer has not yet
  // published). Post: the sources object computeCoverage consumes.
  function gatherSources(view) {
    var cfg = VIEWS[view] || { denominatorMeaningful: true };
    var meta = JUG.__storeMeta || null;
    var store = meta
      ? { node_count: meta.node_count, edge_count: meta.edge_count,
          memory_count: meta.memory_count,
          revision: meta.revision != null ? meta.revision : null }
      : null;
    return {
      rendered: JUG.__wfgRendered || null,
      store: store,
      stream: JUG.__streamResult || null,
      progress: JUG.__progress || null,
      engine: JUG.__engineCoverage || null,
      denominatorMeaningful: cfg.denominatorMeaningful,
      now: Date.now(),
    };
  }

  // ── Rendering ──────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // Humanise a millisecond age into a compact "5m" / "2h" / "3d" / "just now".
  // source: standard 60s/60m/24h rollover; no external lib.
  function fmtAge(ms) {
    if (ms == null) return null;
    var s = Math.floor(ms / 1000);
    if (s < 5) return 'just now';
    if (s < 60) return s + 's ago';
    var m = Math.floor(s / 60);
    if (m < 60) return m + 'm ago';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  var DEGRADED_LABELS = {
    'engine-coverage-unavailable':
      'index parse-coverage unavailable (engine has not reported it)',
    'on-demand-expansion': 'expands on demand; subtree not fully materialised',
    'build-in-progress': 'graph still building',
  };

  // render(container, report) → the container, mutated to reflect `report`.
  // Pure DOM writer (no fetch, no globals): tests call it with a synthetic
  // report and assert the emitted nodes. Idempotent — clears and rebuilds.
  function render(container, report) {
    if (!container) return container;
    container.innerHTML = '';
    if (!report) { container.style.display = 'none'; return container; }
    container.style.display = '';
    container.setAttribute('data-view', report.view);
    container.setAttribute('data-status', report.status);

    // ── Pill (always present): the compact, non-modal affordance ──
    var statusClass = report.status === 'complete' ? 'is-complete'
      : report.status === 'incomplete' ? 'is-incomplete' : 'is-unknown';
    var pill = el('button', 'coverage-pill ' + statusClass);
    pill.setAttribute('type', 'button');
    var dot = el('i', 'coverage-dot');
    pill.appendChild(dot);
    var label;
    if (report.status === 'complete') {
      // Criterion 5: a fully covered view shows a compact "complete"
      // affordance, NOT a warning. No omission rows are emitted below.
      label = 'Complete';
    } else if (report.status === 'incomplete') {
      var total = report.omissions.reduce(function (a, o) {
        return a + (o.count || 0);
      }, 0);
      label = total + ' omitted';
    } else {
      label = 'Coverage unknown';
    }
    pill.appendChild(el('span', 'coverage-pill-label', label));
    container.appendChild(pill);

    // ── Drill-down (criterion 2): the specific omissions + named modes ──
    var body = el('div', 'coverage-drill');
    body.hidden = true;

    if (report.omissions.length) {
      var ul = el('ul', 'coverage-omissions');
      report.omissions.forEach(function (o) {
        var li = el('li', 'coverage-omission');
        li.setAttribute('data-kind', o.kind);
        li.appendChild(el('span', 'coverage-omission-count', String(o.count)));
        li.appendChild(el('span', 'coverage-omission-label', o.label));
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }

    // Nodes / edges / files axes — the raw "rendered vs store" figures
    // (criterion 2), shown even when an axis is complete so the number is
    // legible on demand, not only when something is wrong.
    var axes = el('div', 'coverage-axes');
    axes.appendChild(axisRow('Nodes', report.nodes));
    axes.appendChild(axisRow('Edges', report.edges));
    if (report.files) {
      axes.appendChild(fileRow('Files', report.files));
    }
    body.appendChild(axes);

    // LOD collapse (criterion 3): name the collapsed magnitude explicitly.
    if (report.lod.active) {
      var lod = el('div', 'coverage-lod');
      lod.setAttribute('data-collapsed', String(report.lod.collapsedEdges));
      lod.textContent = 'LOD aggregation active: ' +
        report.lod.collapsedEdges + ' edges collapsed';
      body.appendChild(lod);
    }

    // Staleness (criterion 4): snapshot age + store/snapshot revision.
    var st = report.staleness;
    if (st.stale || st.ageMs != null || st.building) {
      var stale = el('div', 'coverage-staleness' + (st.stale ? ' is-stale' : ''));
      var parts = [];
      if (st.stale) parts.push('superseded snapshot');
      if (st.building) parts.push('building');
      var age = fmtAge(st.ageMs);
      if (age) parts.push('snapshot ' + age);
      if (st.storeRevision != null) parts.push('store rev ' + st.storeRevision);
      if (st.snapshotRevision != null) parts.push('snapshot rev ' + st.snapshotRevision);
      stale.textContent = parts.join(' · ');
      body.appendChild(stale);
    }

    // Named degraded modes (§13 F2) — explicit chips, never a silent default.
    if (report.degraded.length) {
      var deg = el('div', 'coverage-degraded-list');
      report.degraded.forEach(function (mode) {
        var chip = el('span', 'coverage-degraded');
        chip.setAttribute('data-mode', mode);
        chip.textContent = DEGRADED_LABELS[mode] || mode;
        deg.appendChild(chip);
      });
      body.appendChild(deg);
    }

    container.appendChild(body);

    pill.addEventListener('click', function () {
      body.hidden = !body.hidden;
      container.setAttribute('data-open', body.hidden ? '0' : '1');
    });

    return container;
  }

  function axisRow(label, ax) {
    var row = el('div', 'coverage-axis');
    row.setAttribute('data-complete',
      ax.complete === true ? '1' : ax.complete === false ? '0' : 'unknown');
    row.appendChild(el('span', 'coverage-axis-label', label));
    var val = ax.inStore == null
      ? String(ax.rendered) + ' shown'
      : ax.rendered + ' / ' + ax.inStore;
    row.appendChild(el('span', 'coverage-axis-val', val));
    return row;
  }

  function fileRow(label, files) {
    var row = el('div', 'coverage-axis coverage-files');
    row.setAttribute('data-complete', files.complete ? '1' : '0');
    row.appendChild(el('span', 'coverage-axis-label', label));
    row.appendChild(el('span', 'coverage-axis-val',
      files.indexed + ' / ' + files.present + ' indexed'));
    return row;
  }

  // ── Live update ─────────────────────────────────────────────────────────
  function ensureContainer() {
    var c = document.getElementById('coverage-indicator');
    if (!c) {
      c = el('div', 'coverage-indicator');
      c.id = 'coverage-indicator';
      c.style.display = 'none';
      document.body.appendChild(c);
    }
    return c;
  }

  // update() → recompute + repaint for the current view. Hidden entirely on
  // non-graph views (no coverage model to declare).
  function update() {
    var view = activeView();
    var c = ensureContainer();
    if (!VIEWS[view]) { c.style.display = 'none'; c.innerHTML = ''; return null; }
    var report = JUG.Coverage.computeCoverage(view, gatherSources(view));
    render(c, report);
    return report;
  }

  // Fetch the engine parse-coverage read-path (AP#57 shape). Absent endpoint
  // or {available:false} → JUG.__engineCoverage stays a null/unavailable marker
  // and the model emits the 'engine-coverage-unavailable' degraded mode. Never
  // throws into the caller; a failed fetch is a named degraded mode, not an
  // error dialog.
  function fetchEngineCoverage(force) {
    var now = Date.now();
    if (!force && (now - _lastEngineFetch) < ENGINE_POLL_MS) return Promise.resolve();
    if (_engineInflight) return Promise.resolve();
    _engineInflight = true;
    _lastEngineFetch = now;
    return fetch(COVERAGE_URL)
      .then(function (r) { return r.ok ? r.json() : { available: false, reason: 'HTTP ' + r.status }; })
      .catch(function (e) { return { available: false, reason: (e && e.message) || 'fetch failed' }; })
      .then(function (payload) {
        JUG.__engineCoverage = payload;
        _engineInflight = false;
        update();
      });
  }

  function boot() {
    ensureContainer();
    if (JUG.on) {
      JUG.on('state:activeView', function () { update(); fetchEngineCoverage(false); });
      JUG.on('state:lastData', function () { update(); });
      JUG.on('coverage:refresh', function () { update(); });
    }
    // First paint + first engine read once the page has settled.
    fetchEngineCoverage(true);
    update();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Read-only test seam (same pattern as JUG._rendererTest / JUG.__wfgCtx).
  JUG.CoverageIndicator = {
    VIEWS: VIEWS,
    gatherSources: gatherSources,
    render: render,
    update: update,
    fmtAge: fmtAge,
  };
})();
