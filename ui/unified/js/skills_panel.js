// Procedural-skills panel (B1) — renders /api/skills into the shared
// #detail-panel. Procedural memory is the non-declarative counterpart to the
// episodic/semantic memories shown elsewhere: skills are recurring successful
// action sequences retrieved by SITUATION, each with a reinforced proficiency
// and a habitual flag. Opened by clicking the sidebar "Skills" stat row.
//
// Self-contained: renders its own markup into #detail-content rather than
// going through the graph-node detail renderer, since a skill is not a graph
// node. Read-only; no writes.
(function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function profColor(p) {
    if (p >= 0.75) return '#40D870';      // reliable
    if (p >= 0.5) return '#E8B840';       // usable
    return '#C0603C';                     // weak
  }

  function renderSkill(s) {
    var chain = (s.sequence || []).map(function (step) {
      return '<span class="skill-step">' + esc(step) + '</span>';
    }).join('<span class="skill-arrow">\u2192</span>');

    var badges = '';
    if (s.is_habitual) {
      badges += '<span class="skill-badge skill-badge--habit" ' +
        'title="Habitual: \u22655 successful repetitions (Graybiel chunking)">HABIT</span>';
    }
    var ctx = s.context_signature
      ? '<span class="skill-ctx" title="Situation this routine applies to">' +
        esc(s.context_signature) + '</span>'
      : '';

    return '' +
      '<div class="skill-row">' +
        '<div class="skill-chain">' + chain + ' ' + badges + '</div>' +
        '<div class="skill-meta">' +
          '<span class="skill-prof" style="color:' + profColor(s.proficiency) + '" ' +
            'title="Reinforced success rate">' +
            Math.round(s.proficiency * 100) + '%</span>' +
          '<span class="skill-uses" title="Times observed">' +
            esc(s.occurrences) + '\u00d7</span>' +
          ctx +
        '</div>' +
      '</div>';
  }

  function render(payload) {
    var panel = document.getElementById('detail-panel');
    var content = document.getElementById('detail-content');
    if (!panel || !content) return;

    var skills = (payload && payload.skills) || [];
    var head = '' +
      '<div class="skill-panel-head">' +
        '<div class="skill-panel-title">Procedural Skills</div>' +
        '<div class="skill-panel-sub">' + skills.length + ' learned \u00b7 ' +
          ((payload && payload.habitual_count) || 0) + ' habitual</div>' +
        '<div class="skill-panel-note">Recurring successful action sequences, ' +
          'retrieved by situation (basal-ganglia procedural memory, B1). ' +
          'Distinct from the episodic/semantic memory store.</div>' +
      '</div>';

    var body;
    if (!skills.length) {
      body = '<div class="skill-empty">No procedural skills learned yet. ' +
        'Skills are mined at session end once an action sequence recurs across ' +
        'at least three sessions.</div>';
    } else {
      body = skills.map(renderSkill).join('');
    }

    content.innerHTML = head + '<div class="skill-list">' + body + '</div>';
    panel.classList.add('open');
    panel.classList.remove('minimized');
  }

  function open() {
    fetch('/api/skills?min_proficiency=0', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (err) {
        console.warn('[skills] /api/skills fetch failed:', err);
        render({ skills: [], habitual_count: 0 });
      });
  }

  // Wire the sidebar "Skills" stat row to open the panel.
  document.addEventListener('DOMContentLoaded', function () {
    var row = document.getElementById('sv-skills');
    if (row) {
      var clickable = row.closest('.stat-row') || row;
      clickable.style.cursor = 'pointer';
      clickable.addEventListener('click', open);
    }
  });

  window.JUG = window.JUG || {};
  window.JUG.openSkillsPanel = open;
})();
