// Cortex Neural Graph — server-capability degradation (no-DB mode).
//
// Fetches /api/capabilities once at boot. When the server runs without
// Cortex's PostgreSQL (explicit --no-db / CORTEX_VIZ_NO_DB=1, or the
// startup probe found the DB down), `db: false` arrives here and this
// module:
//   * disables the five DB-backed view tabs (Graph, Brain, Knowledge,
//     Wiki, Board) — native `disabled` so no click handler ever fires;
//   * forces the landing view to Trace if a DB view was active;
//   * shows a small dismissible panel explaining the mode with an
//     install link — instead of letting those views error.
// Trace needs no database (session JSONL + git), so it stays fully live.
(function () {
  // Views whose data lives in Cortex's PostgreSQL. Trace is absent by
  // design — it must keep working. Mirrors the server's route guard
  // (cortex_viz/server/http_standalone_nodb.py).
  var DB_VIEWS = { graph: 1, knowledge: 1, wiki: 1, timeline: 1 };

  function disableDbTabs() {
    document.querySelectorAll('.view-toggle .view-btn').forEach(function (btn) {
      var isDbView = btn.dataset.view && DB_VIEWS[btn.dataset.view];
      var isBrainNav = btn.dataset.nav === '/brain';
      if (!isDbView && !isBrainNav) return;
      btn.disabled = true;
      btn.classList.remove('active');
      btn.classList.add('view-btn--nodb');
      btn.title = 'Requires the Cortex memory engine (PostgreSQL) — not connected';
    });
  }

  function forceTraceView() {
    if (!window.JUG || !JUG.state) return;
    if (JUG.state.activeView && !DB_VIEWS[JUG.state.activeView]) return;
    JUG.state.activeView = 'trace';
    var traceBtn = document.querySelector('.view-toggle .view-btn[data-view="trace"]');
    if (traceBtn) traceBtn.classList.add('active');
  }

  function showPanel(caps) {
    var url = caps.cortex_install_url || 'https://github.com/cdeust/Cortex';
    var panel = document.createElement('div');
    panel.id = 'nodb-panel';
    panel.innerHTML =
      '<button id="nodb-panel-close" title="Dismiss">&times;</button>' +
      '<div class="nodb-title">Running without Cortex &mdash; Trace only</div>' +
      '<div class="nodb-body">The Trace view is fully live from your ' +
      '<code>~/.claude</code> session logs and git. Graph, Brain, Knowledge, ' +
      'Wiki and Board read the Cortex memory engine (PostgreSQL), which ' +
      'isn’t connected.</div>' +
      '<a class="nodb-link" href="' + url + '" target="_blank" rel="noopener">' +
      'Install Cortex to light these up &rarr;</a>';
    document.body.appendChild(panel);
    var close = document.getElementById('nodb-panel-close');
    if (close) close.addEventListener('click', function () { panel.remove(); });
  }

  function apply(caps) {
    if (!caps || caps.db !== false) return;
    window.JUG = window.JUG || {};
    JUG.capabilities = caps; // read by polling.js to skip the stats poll
    // The boot script may have opened the PG-backed activity stream
    // before this probe resolved — close it (503-retry loop otherwise).
    if (typeof JUG.stopActivityStream === 'function') JUG.stopActivityStream();
    disableDbTabs();
    forceTraceView();
    showPanel(caps);
  }

  function boot() {
    fetch('/api/capabilities')
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(apply)
      .catch(function () { /* capability probe failed — assume full mode */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
