// Cortex Brain View — memory-science vitals spec.
//
// ONE ordered descriptor for every science vital the backend
// (_compute_memory_vitals) emits in /api/stats → system_vitals. The brain
// renders its vitals panel FROM this spec, and — critically — also renders a
// fallback row for any payload key not listed here, so when Claude Science
// adds a new cognitive gap to the backend it appears in the brain view
// automatically, never silently stale. Keeping the display in step with the
// data is then a one-line spec addition (for a nice label/colour) instead of
// hand-mirroring hardcoded HTML rows across two views.
//   source: keep-brain-aligned pass 2026-07-03.
//
// Each entry: { key, label, title, color, fmt(sv) -> string }.
//   key   — the system_vitals field this row reads (also marks it "consumed"
//           so the fallback pass doesn't double-render it).
//   fmt   — returns the display string from the whole sv payload (so a row can
//           combine fields, e.g. inferred uses provenance totals).
// Stage rows (New/Growing/Strong/Stable/Updating) are handled separately in
// vitals.js from consolidation_pipeline + the canonical stage palette.

window.BRAIN = window.BRAIN || {};

(function () {
  function num(v) { return (v || 0).toLocaleString('en-US'); }

  // Fields consumed by dedicated rows or by the stage pipeline / store-truth —
  // excluded from the fallback pass so they aren't rendered twice or as noise.
  var STRUCTURAL_KEYS = {
    consolidation_pipeline: 1, mean_heat: 1, total_memories: 1,
    episodic: 1, semantic: 1, provenance: 1, habitual_skills: 1,
  };

  var SPEC = [
    {
      key: 'mean_heat', label: 'Mean heat', color: null,
      title: 'Mean effective heat across all memories (live A3 decay).',
      fmt: function (sv) { return sv.mean_heat != null ? sv.mean_heat.toFixed(3) : '--'; },
    },
    // ── consolidation pipeline stage rows injected here by vitals.js ──
    {
      key: 'procedural_skills', label: 'Skills', color: 'var(--vital-orange)',
      title: 'Procedural skills (B1): recurring successful action-sequence ' +
        'habits, retrieved by situation. Value in parentheses = habitual ' +
        '(≥5 successful reps). Click for the skill list.',
      fmt: function (sv) {
        var n = sv.procedural_skills || 0, h = sv.habitual_skills || 0;
        return h > 0 ? (n + ' (' + h + ')') : String(n);
      },
    },
    {
      key: 'inferred_memories', label: 'Inferred', color: 'var(--vital-red)',
      title: 'Source / reality monitoring (C1): epistemic origin of stored ' +
        'memories — perceived / told / inferred. The highlighted count is ' +
        'inferred memories, the confabulation-risk cohort (Johnson 1993). ' +
        'Shown as inferred / total.',
      fmt: function (sv) {
        var p = sv.provenance || {};
        var inf = sv.inferred_memories || p.inferred || 0;
        var tot = (p.perceived || 0) + (p.told || 0) + (p.inferred || 0) + (p.unknown || 0);
        return tot > 0 ? (num(inf) + ' / ' + num(tot)) : num(inf);
      },
    },
    {
      key: 'crystallized_confabulations', label: 'Confabulated', color: 'var(--vital-red)',
      title: 'Source / reality monitoring (C1) read-side enforcement: semantic ' +
        'memories the confabulation gate flagged at the episodic→semantic ' +
        'promotion point — an internally generated (inferred), ungrounded ' +
        'cluster crystallized as a semantic FACT (Johnson & Raye 1981). ' +
        'Distinct from Inferred (inferred memories at rest): this is the subset ' +
        'PROMOTED to knowledge. Non-fatal — flagged, not dropped.',
      fmt: function (sv) { return num(sv.crystallized_confabulations); },
    },
    {
      key: 'habituated_repeats', label: 'Habituated', color: 'var(--vital-purple)',
      title: 'Habituation & sensitization (E1): surplus repeated presentations ' +
        'of identical content the write gate’s response decrement is ' +
        'damping (Rankin 2009).',
      fmt: function (sv) { return num(sv.habituated_repeats); },
    },
    {
      key: 'extinguished', label: 'Extinguished', color: 'var(--vital-magenta)',
      title: 'Fear extinction / inhibitory learning (E2): memories carrying a ' +
        'reversible inhibitory extinction tag — suppressed WITHOUT ' +
        'deletion, so they can spontaneously recover or be reinstated (Bouton ' +
        '2004; Milad & Quirk 2012). Distinct from Stale/archived soft-delete.',
      fmt: function (sv) { return num(sv.extinguished); },
    },
    {
      key: 'conflicting_claim_pairs', label: 'Conflicts', color: 'var(--vital-amber)',
      title: 'Conflict monitoring / cognitive control (A2): pairs of persisted ' +
        'claims that disagree — shared entity, opposing claim types. The ' +
        'standing counterpart of the recall-time conflict monitor (Botvinick ' +
        '2001; Miller & Cohen 2001).',
      fmt: function (sv) { return num(sv.conflicting_claim_pairs); },
    },
    {
      key: 'familiarity_resolvable', label: 'Familiar', color: 'var(--vital-cyan)',
      title: 'Recollection vs. familiarity / dual-process retrieval (C2): share ' +
        'of a recent sample resolvable by FAMILIARITY ALONE (a near-duplicate ' +
        'neighbour above the familiarity threshold), the regime where a fast ' +
        'a-contextual similarity gate suffices (Yonelinas 2002; Diana et al ' +
        '2007). Shown as share% (resolvable/sampled).',
      fmt: function (sv) {
        var f = sv.familiarity_resolvable || {};
        if (!f.sampled) return '--';
        return Math.round((f.share || 0) * 100) + '% (' + (f.resolvable || 0) +
          '/' + f.sampled + ')';
      },
    },
    {
      key: 'sleep_phase_outputs', label: 'Sleep NREM/REM', color: 'var(--vital-blue-sleep)',
      title: 'Two-phase consolidation (F1): standing footprint of the NREM/REM ' +
        'split — NREM stores auto-narration semantic memories; REM forms ' +
        'abstract schemas (Diekelmann & Born 2010; van de Ven 2020). NREM / REM.',
      fmt: function (sv) {
        var s = sv.sleep_phase_outputs || {};
        return (s.nrem || 0) + ' / ' + (s.rem || 0);
      },
    },
    {
      key: 'targeted_reactivation', label: 'TMR cue', color: 'var(--vital-green)',
      title: 'Targeted memory reactivation (F2): the cue that biased the last ' +
        'offline consolidation’s NREM replay — cueing biases which ' +
        'memories reconsolidate (Rasch 2007; Oudiette & Paller 2013). Shows the ' +
        'cue + how many replayed memories matched it; –– when cue-free.',
      fmt: function (sv) {
        var t = sv.targeted_reactivation || {};
        return t.cue ? (t.cue + ' (' + (t.cued_replayed || 0) + ')') : '--';
      },
    },
    {
      key: 'stress_modulation', label: 'Stress gain', color: 'var(--vital-stress)',
      title: 'Stress / arousal modulation of encoding (Yerkes-Dodson 1908; ' +
        'Diamond 2007): the current encoding-gain multiplier set by an ' +
        'inverted-U of stress. is_impairing flags the over-arousal downslope ' +
        'where gain falls below baseline. Shown as ×gain (stress level).',
      fmt: function (sv) {
        var s = sv.stress_modulation || {};
        var g = '×' + (s.gain != null ? s.gain.toFixed(2) : '1.00') +
          ' · stress ' + (s.stress != null ? s.stress.toFixed(2) : '0.00');
        return s.is_impairing ? g + ' ⚠' : g;
      },
    },
    {
      key: 'active_goal', label: 'Active goal', color: 'var(--vital-blue-goal)',
      title: 'Goal maintenance / cognitive control (A1): the goal currently ' +
        'biasing retrieval — prefrontal goal-shielding (Miller & Cohen 2001; ' +
        'Braver 2012). Shown as on/idle + the trigger and keyword counts that ' +
        'define the active goal context.',
      fmt: function (sv) {
        var g = sv.active_goal || {};
        if (!g.active) return 'idle';
        return 'on · ' + (g.triggers || 0) + ' trig · ' + (g.keywords || 0) + ' kw';
      },
    },
    {
      key: 'forward_model', label: 'Forward model', color: 'var(--vital-teal)',
      title: 'Predictive coding / forward model (Wolpert 1998; Friston 2010): ' +
        'mean prediction error over a bounded sample of forward predictions — ' +
        'lower is a better-calibrated internal model. Shown as err (sample n).',
      fmt: function (sv) {
        var f = sv.forward_model || {};
        if (!f.sampled) return '--';
        return 'err ' + (f.mean_error != null ? f.mean_error.toFixed(3) : '?') +
          ' (n=' + f.sampled + ')';
      },
    },
    {
      key: 'attentional_salience', label: 'Attn focus', color: 'var(--vital-pink)',
      title: 'Attentional control / central executive (A1): concentration of ' +
        'bottom-up salience (0.5·importance + 0.5·|valence|) that feeds the ' +
        'recall-time attentional re-weight (Baddeley 2003; Posner & Petersen ' +
        '1990; Cowan 2001). Shown as the share of recent salience mass held by ' +
        'the ~4 (Cowan) most-salient memories — high = a few dominate ' +
        'stimulus-driven capture, low = salience spread evenly. Descriptive ' +
        'salience statistic, not the softmax spotlight (which needs a live ' +
        'query). Shown as share% (sample n).',
      fmt: function (sv) {
        var a = sv.attentional_salience || {};
        if (!a.sampled) return '--';
        return Math.round((a.focus_share || 0) * 100) + '% (n=' + a.sampled + ')';
      },
    },
  ];

  // Humanize an unknown payload key for a fallback row label:
  // "targeted_reactivation" -> "Targeted reactivation".
  function humanize(key) {
    var s = key.replace(/_/g, ' ');
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // Free-text fields that are internal identifiers, not display values — a
  // fallback object row must never dump these (the 'label' full of node ids
  // that bloated the panel, 2026-07-03).
  var _NOISE_KEY = /(^|_)(id|ids|label|labels|signature|hash|key|uuid)$/i;

  function _fmtScalar(v) {
    if (typeof v === 'number') {
      return Number.isInteger(v) ? num(v) : (Math.round(v * 1000) / 1000);
    }
    return String(v);
  }

  // Default rendering for an unspecced field (a vital Claude Science added to
  // the backend that has no spec entry yet). Kept CONCISE so it can't bloat the
  // panel before a proper spec entry lands: scalars shown directly; objects
  // summarised to their first few SALIENT scalar fields (numbers/bools),
  // skipping id/label-like noise, capped at 3.
  function fallbackFmt(v) {
    if (v == null) return '--';
    if (typeof v === 'number') return _fmtScalar(v);
    if (typeof v === 'string') return v.length > 40 ? v.slice(0, 39) + '…' : v;
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    if (typeof v === 'object') {
      var parts = [];
      var keys = Object.keys(v);
      for (var i = 0; i < keys.length && parts.length < 3; i++) {
        var k = keys[i], vv = v[k];
        if (_NOISE_KEY.test(k)) continue;
        if (vv == null || typeof vv === 'object') continue;
        parts.push(k + ' ' + _fmtScalar(vv));
      }
      return parts.join(' · ') || '(details)';
    }
    return String(v);
  }

  BRAIN.VITALS_SPEC = SPEC;
  BRAIN.VITALS_STRUCTURAL = STRUCTURAL_KEYS;
  BRAIN.vitalsHumanize = humanize;
  BRAIN.vitalsFallbackFmt = fallbackFmt;
})();
