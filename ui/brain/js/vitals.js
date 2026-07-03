// Cortex Brain View — memory-science vitals for the #info HUD.
//
// Same data + source as the galaxy sidebar (/api/stats → system_vitals), but
// rendered DYNAMICALLY from BRAIN.VITALS_SPEC rather than hardcoded rows: the
// brain builds one row per spec entry, plus the consolidation-stage pipeline
// (canonical stage palette), plus a FALLBACK row for any payload field the
// spec doesn't cover yet. That last part is the alignment guarantee — when
// Claude Science adds a new cognitive gap to _compute_memory_vitals, it shows
// up in the brain automatically instead of going stale. Also fills the
// store-truth totals (Nodes/Synapses/Domains/Memories/Entities). One fetch at
// boot + a slow refresh; the server TTL-caches the aggregates.

window.BRAIN = window.BRAIN || {};

(function () {
  'use strict';

  var REFRESH_MS = 60000; // matches the server-side stats TTL

  // Stage pipeline rows: consolidation_pipeline field -> label, coloured from
  // the canonical stage palette (BRAIN.PALETTE.STAGE_COLORS) so they match the
  // memory NODE colours exactly.
  var STAGE_ROWS = [
    { field: 'labile', label: 'New', stage: 'labile' },
    { field: 'early_ltp', label: 'Growing', stage: 'early_ltp' },
    { field: 'late_ltp', label: 'Strong', stage: 'late_ltp' },
    { field: 'consolidated', label: 'Stable', stage: 'consolidated' },
    { field: 'reconsolidating', label: 'Updating', stage: 'reconsolidating' },
  ];

  function setText(id, v) {
    var el = document.getElementById(id);
    if (el) el.textContent = v;
  }

  function renderStoreTruth(s) {
    var fmt = function (n) { return (n || 0).toLocaleString('en-US'); };
    setText('s-nodes', fmt(s.node_count));
    setText('s-edges', fmt(s.edge_count));
    setText('s-dom', fmt(s.domain_count));
    setText('s-mem', fmt(s.memory_count));
    setText('s-ent', fmt(s.entity_count));
  }

  function statRow(key, label, valueHtml, title, color) {
    var t = title ? ' title="' + String(title).replace(/"/g, '&quot;') + '"' : '';
    var style = color ? ' style="color:' + color + '"' : '';
    var dv = key ? ' data-vital="' + key + '"' : '';
    return '<div class="stat"' + dv + t + '><span>' + label + '</span>' +
      '<span class="v"' + style + '>' + valueHtml + '</span></div>';
  }

  // Build the whole vitals list from the payload: mean heat, the stage
  // pipeline, every spec vital, then a fallback row for any unspecced field.
  function buildRows(sv) {
    var host = document.getElementById('brain-vitals-rows');
    if (!host) return;
    var spec = BRAIN.VITALS_SPEC || [];
    var structural = BRAIN.VITALS_STRUCTURAL || {};
    var stageColors = (BRAIN.PALETTE && BRAIN.PALETTE.STAGE_COLORS) || {};
    var covered = {};
    var html = '';

    // Mean heat first, then the stage pipeline, then the rest of the spec in
    // order. The spec's own mean_heat entry is emitted at its position (top).
    spec.forEach(function (row) {
      covered[row.key] = true;
      html += statRow(row.key, row.label, row.fmt(sv) || '--', row.title, row.color);
      // Inject the stage pipeline right after mean_heat.
      if (row.key === 'mean_heat') {
        STAGE_ROWS.forEach(function (st) {
          var cp = sv.consolidation_pipeline || {};
          html += statRow('stage_' + st.field, st.label,
            (cp[st.field] || 0).toLocaleString('en-US'),
            'Consolidation stage: ' + st.stage, stageColors[st.stage] || null);
        });
      }
    });

    // Fallback: any system_vitals field with no spec entry and not structural
    // — a vital added to the backend that the spec hasn't caught up with. It
    // still shows, so the brain never silently lags the data.
    Object.keys(sv).forEach(function (k) {
      if (covered[k] || structural[k]) return;
      html += statRow(k, BRAIN.vitalsHumanize(k), BRAIN.vitalsFallbackFmt(sv[k]),
        'New backend vital (no display spec yet): ' + k, '#9fb4c8');
    });

    host.innerHTML = html;
    // Re-wire the skills row to the shared skills panel (skills_panel.js wires
    // #sv-skills at DOMContentLoaded, before these dynamic rows exist).
    var skillRow = host.querySelector('[data-vital="procedural_skills"]');
    if (skillRow && window.JUG && JUG.openSkillsPanel) {
      skillRow.style.cursor = 'pointer';
      skillRow.addEventListener('click', JUG.openSkillsPanel);
    }
  }

  function render(sv) {
    if (!sv || sv.total_memories == null) return;
    var block = document.getElementById('brain-vitals');
    if (block) block.style.display = 'block';
    buildRows(sv);
  }

  function refresh() {
    fetch('/api/stats')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) return;
        renderStoreTruth(s);
        render(s.system_vitals);
      })
      .catch(function () { /* HUD extras — never block the brain on stats */ })
      .then(function () { setTimeout(refresh, REFRESH_MS); });
  }

  document.addEventListener('DOMContentLoaded', refresh);
})();
