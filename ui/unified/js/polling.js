// Cortex Neural Graph — Sidebar counters + status from /api/graph/progress.
//
// Original implementation downloaded the full /api/graph payload
// (80+ MB once L6 lands) on every page load just to read meta.* and
// drive the stats panel. The phase loader (phase_loader.js) now owns
// node/edge population via /api/graph/phase deltas, so this file's
// only remaining job is to keep the stats card and clock current —
// done with a tiny /api/graph/progress poll instead of the full graph.
(function() {
  // Last server-side stats meta (/api/stats). Cached so the live legend
  // recount on state:lastData can keep the store-total tooltip (Memories
  // hover shows "N in store") current between the 30 s stats polls. The
  // headline Memories number is the in-galaxy rendered count.
  var _lastServerMeta = null;

  function fetchGraph() {
    // Lazy-load: only poll while the user is actually on the Graph
    // tab. The poll is now a tiny /api/graph/progress hit (not the
    // multi-MB /api/graph it used to be), but the activeView listener
    // below still re-triggers it on tab switch, so gating here keeps
    // idle tabs quiet. No abortController needed — the progress poll
    // is a few hundred bytes and re-polls itself via setTimeout.
    if (window.JUG && JUG.state && JUG.state.activeView !== 'graph') {
      updateStatus('Online — graph standby');
      hideLoading();
      return;
    }

    fetch('/api/graph/progress')
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(p) {
        // Counts come from the phase loader's cumulative store
        // (JUG.state.lastData). progress.node_count reflects the
        // server-side cache, but we want the panel to mirror what
        // the user can actually see in the graph — so fall back to
        // server counts only until the first phase lands.
        var d = JUG.state && JUG.state.lastData;
        var localCount = d && d.nodes ? d.nodes.length : 0;
        if (localCount > 0) {
          // The phase loader's appendGraphDelta already updates the
          // sidebar counters from lastData.nodes (see graph.js line
          // ~250). Nothing more to do here for counts.
        } else {
          updateStats({
            node_count: p.node_count,
            edge_count: p.edge_count,
          });
        }
        var phase = p.phase || '';
        var pct = Math.round((p.pct || 0) * 100);
        if (p.full_ready) {
          updateStatus('Ready · ' + (localCount || p.node_count) + ' nodes');
        } else if (p.baseline_ready) {
          updateStatus('Baseline ready · loading L6 (' + pct + '%)');
        } else {
          updateStatus('Building · ' + phase + ' (' + pct + '%)');
        }
        hideLoading();
        // Keep polling progress while the build is still active.
        if (!p.full_ready) setTimeout(fetchGraph, 2000);
      })
      .catch(function(err) {
        console.warn('[cortex] progress poll error:', err.message);
        setTimeout(fetchGraph, 4000);
      });
  }

  function updateStats(meta) {
    // Legend counts MUST reflect the rendered canvas, not server totals.
    // The build cache over-counts vs the progressive render and (post
    // memory-pruning) reports memory_count=0 while memories stream in, so
    // server meta gave "36 domains / 0 discussions" that didn't match what's
    // drawn. Count the actual rendered graph (JUG.state.lastData) when it
    // exists; fall back to meta only before the first paint. (lod.js is not
    // loaded by unified-viz.html, so polling.js owns the legend here.)
    // PREFER the renderer's own count of what it actually drew (set by
    // workflow_graph.js mount as JUG.__wfgRendered). JUG.state.lastData
    // accumulates every node ever appended and never prunes, so on the
    // graph/trace canvas it goes stale across view switches and over-counts
    // filtered edges — the legend then disagrees with the visible node count.
    var rc = (window.JUG && JUG.__wfgRendered) || null;
    var _view = (window.JUG && JUG.state && JUG.state.activeView) || '';
    var d = (window.JUG && JUG.state && JUG.state.lastData) || null;
    // Store size: the whole-table raw count, independent of what's rendered
    // in any view. Only present once /api/stats has answered.
    if (meta && meta.memory_count_raw != null) {
      setText('s-mem-raw', meta.memory_count_raw);
    }
    if (rc && (_view === 'graph' || _view === 'trace')) {
      setText('s-dom', rc.domain);
      setText('s-mem', rc.memory);
      if (meta && meta.memory_count != null && meta.memory_count > rc.memory) {
        setTitle('s-mem', meta.memory_count + ' in store (' + rc.memory + ' shown)');
      }
      setText('s-ent', rc.nodes - rc.domain - rc.memory - rc.discussion);
      setText('s-disc', rc.discussion);
      setText('s-nodes', rc.nodes);
      setText('s-edge', rc.edges);
      // Galaxy streams toward the store total; the trace streams on expand
      // (no meaningful denominator), so its total clamps to what is shown.
      var rcTotalN = _view === 'graph' && meta ? meta.node_count : null;
      var rcTotalE = _view === 'graph' && meta ? meta.edge_count : null;
      setText('sb-nodes', fmtStream(rc.nodes, rcTotalN));
      setText('sb-edges', fmtStream(rc.edges, rcTotalE));
    } else if (d && d.nodes && d.nodes.length) {
      var c = { domain: 0, memory: 0, discussion: 0 };
      for (var i = 0; i < d.nodes.length; i++) {
        var k = d.nodes[i].kind || d.nodes[i].type || '';
        if (c[k] !== undefined) c[k]++;
      }
      var total = d.nodes.length;
      setText('s-dom', c.domain);
      // Memories stat = the IN-GALAXY count (rendered `memory`-kind nodes).
      // The bounded build now RETAINS the top-N hottest memories (cap =
      // CORTEX_VIZ_MEMORY_LIMIT, default 25000), so the L5 layer renders
      // them and `c.memory` is the true on-screen mass — what the user is
      // looking at. The full store total (e.g. 537k) is informational, not
      // what the galaxy shows; surface it as a hover tooltip so the headline
      // number matches the canvas. source: bounded retention (workflow_graph.py).
      setText('s-mem', c.memory);
      if (meta && meta.memory_count != null && meta.memory_count > c.memory) {
        setTitle('s-mem', meta.memory_count + ' in store (' + c.memory + ' shown)');
      }
      // "Entities" = every knowledge node that isn't a domain/memory/
      // discussion (files, symbols, tools, commands, agents, skills, hooks,
      // MCPs) — the sum, matching the server's entity_count semantics.
      setText('s-ent', total - c.domain - c.memory - c.discussion);
      setText('s-disc', c.discussion);
      setText('s-nodes', total);
      setText('s-edge', (d.edges || d.links || []).length);
      setText('sb-nodes', fmtStream(total, meta ? meta.node_count : null));
      setText('sb-edges', fmtStream((d.edges || d.links || []).length, meta ? meta.edge_count : null));
    } else {
      // Pre-render: show server totals as a placeholder until nodes arrive.
      setText('s-dom', meta.domain_count || 0);
      setText('s-mem', meta.memory_count || 0);
      setText('s-ent', meta.entity_count || 0);
      setText('s-disc', meta.discussion_count || 0);
      setText('s-nodes', meta.node_count || 0);
      setText('s-edge', meta.edge_count || 0);
      // Pre-render: nothing streamed yet — honest "0/total".
      setText('sb-nodes', fmtStream(0, meta.node_count));
      setText('sb-edges', fmtStream(0, meta.edge_count));
    }

    // System vitals
    var sv = meta.system_vitals;
    if (sv) {
      // Vitals are opt-in: reveal the discrete "+ System Vitals" toggle when
      // data exists, but keep the panel itself collapsed so the default rail
      // stays clean (stats + benchmarks). The toggle opens the panel on demand.
      var svToggle = document.getElementById('vitals-toggle');
      if (svToggle) svToggle.style.display = '';
      setText('sv-heat', sv.mean_heat ? sv.mean_heat.toFixed(3) : '--');
      var cp = sv.consolidation_pipeline || {};
      setText('sv-labile', cp.labile || 0);
      setText('sv-eltp', cp.early_ltp || 0);
      setText('sv-lltp', cp.late_ltp || 0);
      setText('sv-cons', cp.consolidated || 0);
      setText('sv-recon', cp.reconsolidating || 0);
      // Procedural skills (B1): total, with habitual count in parentheses.
      var skillN = sv.procedural_skills || 0;
      var habitN = sv.habitual_skills || 0;
      setText('sv-skills', habitN > 0 ? (skillN + ' (' + habitN + ')') : skillN);
      // Source monitoring (C1): inferred count out of total — the
      // confabulation-risk cohort. Shows "N / total" so it's read as a ratio.
      var prov = sv.provenance || {};
      var inferN = sv.inferred_memories || prov.inferred || 0;
      var provTotal = (prov.perceived || 0) + (prov.told || 0) +
                      (prov.inferred || 0) + (prov.unknown || 0);
      setText('sv-inferred', provTotal > 0 ? (inferN + ' / ' + provTotal) : inferN);
      // Source monitoring (C1) read-side enforcement: semantic memories flagged
      // at the promotion point as a confabulation crystallized as fact (an
      // inferred, ungrounded cluster promoted to knowledge). Distinct from the
      // Inferred cohort above (inferred memories at rest) — this is the subset
      // that crossed the crystallization gate (Johnson & Raye 1981).
      setText('sv-confab', sv.crystallized_confabulations || 0);
      // Habituation (E1): surplus repeated presentations the write gate's
      // response decrement is damping (Rankin 2009) — the memory-bloat signal.
      setText('sv-habituated', sv.habituated_repeats || 0);
      // Extinction (E2): memories carrying a reversible inhibitory tag —
      // deprecated-but-retained (suppressed WITHOUT deletion), so they can
      // spontaneously recover or be reinstated (Bouton 2004; Milad & Quirk
      // 2012). Distinct from the Stale/archived soft-delete count.
      setText('sv-extinguished', sv.extinguished || 0);
      // Conflict monitoring (A2): pairs of persisted claims that disagree
      // (shared entity, opposing claim_type) — the standing counterpart of the
      // recall-time conflict monitor that routes to the claim resolver
      // (Botvinick 2001; Miller & Cohen 2001).
      setText('sv-conflict', sv.conflicting_claim_pairs || 0);
      // Dual-process retrieval (C2): share of a recent sample resolvable by
      // familiarity alone (a near-duplicate neighbour above the familiarity
      // threshold), the standing counterpart of the recall-time familiarity
      // triage (Yonelinas 2002; Diana et al 2007). Shown as a percentage with
      // the resolvable/sampled counts in parentheses.
      var fam = sv.familiarity_resolvable || {};
      if (fam.sampled) {
        var pct = Math.round((fam.share || 0) * 100);
        setText('sv-familiarity', pct + '% (' + (fam.resolvable || 0) +
                '/' + fam.sampled + ')');
      } else {
        setText('sv-familiarity', '--');
      }
      // Two-phase consolidation (F1): standing footprint of the NREM/REM split
      // (mcp_server/core/sleep_phases.py). NREM = auto-narration semantic
      // memories the exact-replay phase stored; REM = abstract schemas the
      // recombination/abstraction phase formed — the abstraction the single
      // pre-split pass never produced (Diekelmann & Born 2010; van de Ven 2020).
      // Shown as "NREM / REM".
      var slp = sv.sleep_phase_outputs || {};
      setText('sv-sleepphase', (slp.nrem || 0) + ' / ' + (slp.rem || 0));
      // Targeted memory reactivation (F2): the cue that biased the LAST offline
      // consolidation's NREM replay (mcp_server/core/targeted_reactivation.py).
      // Cueing biases which memories preferentially reconsolidate (Rasch et al
      // 2007; Oudiette & Paller 2013). Shows the cue string + how many replayed
      // memories matched it; '--' when the last cycle ran cue-free / TMR ablated
      // / the store predates cue logging. A deterministic lexical re-weight of
      // the replay set, not a spindle model.
      var tmr = sv.targeted_reactivation || {};
      if (tmr.cue) {
        setText('sv-tmr', tmr.cue + ' (' + (tmr.cued_replayed || 0) + ')');
      } else {
        setText('sv-tmr', '--');
      }
      // Stress-hormone (glucocorticoid) modulation (D1): the inverted-U
      // consolidation gain of the LAST offline cycle, with the session-stress
      // scalar in parentheses. Moderate stress enhances (gain>1), extreme
      // impairs (gain<1) consolidation (Roozendaal & McGaugh 2011; McGaugh
      // 2000). A red-tinted down-arrow marks the impairing arm; a neutral
      // 1.00x means the last cycle was calm / the mechanism was ablated / the
      // store predates stress logging. A lexical+valence proxy and a
      // deterministic inverted-U (Hebb 1955 shape), not a glucocorticoid model.
      var smod = sv.stress_modulation || {};
      var smEl = document.getElementById('sv-stress');
      if (smEl) {
        var gain = (typeof smod.gain === 'number') ? smod.gain : 1.0;
        var stress = (typeof smod.stress === 'number') ? smod.stress : 0.0;
        var arrow = smod.is_impairing ? '\u2193' : '';
        setText('sv-stress', arrow + gain.toFixed(2) + 'x (' + stress.toFixed(2) + ')');
        smEl.style.color = smod.is_impairing ? '#E05050' : '#D08050';
      }
      // Goal / task-set maintenance (A3): the sustained goal vector promoted
      // from the store's active prospective triggers (mcp_server/core/
      // goal_maintenance.py) — the held task-set that biases the write gate and
      // recall fusion toward goal-relevant information while active (Miller &
      // Cohen 2001). Shows the goal's top keywords as a task label with the
      // count of active triggers forming it in parentheses; '--' when no goal is
      // active (an inactive goal is the write+recall identity — the no-goal
      // case), TMR/goal ablated, or the store predates goal maintenance. A
      // keyword/entity goal-match promoted from the trigger surface, NOT a
      // learned PFC task-set controller.
      var goal = sv.active_goal || {};
      if (goal.active && goal.label) {
        setText('sv-goal', goal.label + ' (' + (goal.triggers || 0) + ')');
      } else {
        setText('sv-goal', '--');
      }
      // Cerebellar forward model (B3): mean absolute one-step forward-model
      // prediction error of the recent heat trajectory (mcp_server/core/
      // forward_model.py) — the cerebellum predicts the next value of a signal
      // and corrects its estimate from the residual (Wolpert, Miall & Kawato
      // 1998; Ito 2008). High = jumpy activation the smooth dynamics fail to
      // anticipate; ~0 = predictable. Shows the mean error with the sampled
      // count in parentheses; '--' when no trajectory is available (< 2 rows or
      // the store predates heat_base). A minimal deterministic predict→error→
      // correct EMA (LOW AI PRIORITY), NOT a learned cerebellar circuit.
      var fm = sv.forward_model || {};
      if (fm.sampled && fm.sampled >= 2) {
        setText('sv-prederr', (fm.mean_error || 0).toFixed(4) + ' (' + fm.sampled + ')');
      } else {
        setText('sv-prederr', '--');
      }
      // Attentional control / central executive (A1): concentration of the
      // bottom-up salience (0.5*importance + 0.5*|valence|) that feeds the
      // recall-time attentional re-weight (mcp_server/core/attentional_control
      // .py, wired via recall_pipeline.attentional_focus_rerank). The top-down
      // half is query-dependent and in-flight; the bottom-up half is persisted,
      // so we show how CONCENTRATED it is — the share of recent salience mass
      // held by the Cowan ~4 most-salient memories (Baddeley 2003; Posner &
      // Petersen 1990; Cowan 2001). High = a few dominate stimulus-driven
      // capture; low = spread evenly. Shows share% with the sample size; '--'
      // when the store predates the affect columns. A descriptive concentration
      // ratio, NOT the softmax spotlight (which needs a live query).
      var attn = sv.attentional_salience || {};
      if (attn.sampled) {
        setText('sv-attn',
          Math.round((attn.focus_share || 0) * 100) + '% (' + attn.sampled + ')');
      } else {
        setText('sv-attn', '--');
      }
    }

    // Benchmark summary — R@10 + MRR side by side
    var bm = meta.benchmarks;
    if (bm) {
      var el = document.getElementById('benchmark-summary');
      if (el) el.style.display = 'block';
      if (bm.LongMemEval) setText('b-lme', fmtBench(bm.LongMemEval));
      if (bm.LoCoMo) setText('b-loc', fmtBench(bm.LoCoMo));
      if (bm.BEAM) setText('b-beam', fmtBench(bm.BEAM));
    }
  }

  function fmtBench(bm) {
    var parts = [];
    if (bm.recall_10 !== undefined) parts.push('R@10 ' + Math.round(bm.recall_10) + '%');
    if (bm.mrr !== undefined) parts.push('MRR .' + Math.round(bm.mrr * 1000));
    return parts.join(' | ') || '--';
  }

  function setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  // DD-04 status line: exact streamed counts, never rounded — "119 304/119 304".
  // Thousands grouped with U+202F (narrow no-break space) so the mono strip
  // reads like the exhibit. source: DS cards/data-pointcloud.html (Spec DD-04).
  function fmtExact(n) {
    return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }
  // "shown/total": total is the server store count when it is ahead of the
  // stream, else the shown count itself (never display a total smaller than
  // what is already on canvas).
  function fmtStream(shown, total) {
    var t = (total != null && total > shown) ? total : shown;
    return fmtExact(shown) + '/' + fmtExact(t);
  }

  function setTitle(id, val) {
    var el = document.getElementById(id);
    if (el) el.title = val;
  }

  function updateStatus(text) {
    var el = document.getElementById('status-text');
    if (el) el.textContent = text;
  }

  function hideLoading() {
    var el = document.getElementById('loading');
    if (el && !el.classList.contains('done')) {
      el.classList.add('done');
      setTimeout(function() { if (el.parentNode) el.remove(); }, 1100);
    }
  }

  function useFallback() {
    var fallback = {
      nodes: [
        { id: 'dom_1', type: 'domain', label: 'Sample Domain', domain: 'sample', color: '#6366f1', size: 8, group: 'sample', sessionCount: 10, confidence: 0.8 },
        { id: 'entry_1', type: 'entry-point', label: 'system design', domain: 'sample', color: '#00d4ff', size: 5, group: 'sample', frequency: 4 },
      ],
      edges: [
        { source: 'dom_1', target: 'entry_1', type: 'has-entry', weight: 0.7 },
      ],
      clusters: [],
      meta: { domain_count: 1, node_count: 2, edge_count: 1, total_batches: 1 },
    };
    JUG.state.lastData = fallback;
    JUG.buildGraph(fallback);
    updateStats(fallback.meta);
    hideLoading();
    updateStatus('Offline (sample)');
  }

  // Clock
  setInterval(function() {
    var d = new Date();
    var el = document.getElementById('status-time');
    if (el) el.textContent = [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map(function(v) { return String(v).padStart(2, '0'); }).join(':');
  }, 1000);

  // TRUE store counts for the HUD — independent of the rendered view.
  // The sidebar must show the whole memory system (e.g. 475k memories),
  // not the node kinds the current view happens to draw. The trace view
  // renders zero memory nodes, so without this the HUD read "Memories 0"
  // against a full store. /api/stats is a handful of COUNT(*) queries.
  function fetchStats() {
    // No-DB mode (capabilities.js): /api/stats is PG-backed and answers
    // 503 — skip the poll instead of warning every 30 s.
    if (window.JUG && JUG.capabilities && JUG.capabilities.db === false) return;
    fetch('/api/stats')
      .then(function(res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function(s) { _lastServerMeta = s; updateStats(s); })
      .catch(function(err) { console.warn('[cortex] stats poll error:', err.message); });
  }

  // Boot — delay initial fetch. fetchGraph() short-circuits unless
  // activeView === 'graph', so this is cheap on Knowledge / Board /
  // Wiki landings. fetchStats() always runs so the HUD shows the true
  // store size on every view, and refreshes periodically.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      setTimeout(fetchGraph, 500);
      setTimeout(fetchStats, 300);
    });
  } else {
    setTimeout(fetchGraph, 500);
    setTimeout(fetchStats, 300);
  }
  setInterval(fetchStats, 30000);

  // Trigger the graph fetch when the user actually switches to the
  // Graph tab (lazy-load semantics).
  if (window.JUG && JUG.on) {
    JUG.on('state:activeView', function(ev) {
      if (ev && ev.value === 'graph') setTimeout(fetchGraph, 50);
    });
    // Recount the legend from the rendered graph on every data change
    // (phase loads + SSE deltas), so it tracks the canvas live instead of
    // only refreshing on the 30 s stats poll. Reuse the last server meta
    // so the Memories store-total tooltip (see updateStats) stays current
    // between stats polls; the headline count is the rendered tally.
    JUG.on('state:lastData', function() { updateStats(_lastServerMeta || {}); });
  }

  function _loadDiscussionBatch(batch) {
    var batchSize = 500;
    fetch(JUG.API_URL.replace('/api/graph', '/api/discussions') + '?batch=' + batch + '&batch_size=' + batchSize)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (!data.nodes || !data.nodes.length) return;
        JUG.addBatchToGraph(data);
        var discEl = document.getElementById('s-disc');
        if (discEl && JUG.state.lastData) {
          var count = JUG.state.lastData.nodes.filter(function(n) { return n.type === 'discussion'; }).length;
          discEl.textContent = count;
        }
        if (data.meta && batch < (data.meta.total_batches || 1) - 1) {
          setTimeout(function() { _loadDiscussionBatch(batch + 1); }, 200);
        }
      })
      .catch(function(err) {
        console.warn('[cortex] Discussion batch error:', err.message);
      });
  }

  // No auto-refresh — user triggers manually via Reset button or page reload
})();
