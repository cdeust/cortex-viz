// Cortex Atom Memory Graph — Configuration & Constants
// Single source of truth for colors, sizes, and type definitions.
// Colour comes from /shared/palette.js (CortexPalette) reading the design-
// system's CSS custom properties (ui/shared/README.md "Colour only from
// data"). A hex-string fallback is kept per constant for the (unsupported)
// case CortexPalette hasn't loaded yet, so the scene never renders black.

(function() {
  window.JMD = window.JMD || {};

  function tokenHex(name, fallback) {
    if (!window.CortexPalette) return fallback;
    var v = window.CortexPalette.readVar(name);
    return v ? window.CortexPalette.hex(name) : fallback;
  }
  function hexToInt(hex) {
    return parseInt(hex.replace('#', ''), 16);
  }

  // ── Memory store_type → design-system data tokens (shared/README.md
  // "re-inking cortex-specific data families"). Reuses the canonical
  // lifecycle-stage tokens directly: episodic (raw experience) = labile
  // (new); semantic (extracted schema) = the reconsolidation hue (palette.js
  // maps semantic → --stage-recon itself); entity (stable knowledge-graph
  // fact) = consolidated. ──
  var TYPE_HEX = {
    episodic: tokenHex('--node-episodic', '#5a7a9a'),
    semantic: tokenHex('--node-semantic', '#5a7a9a'),
    entity:   tokenHex('--node-entity',   '#5a7a9a'),
  };
  JMD.TYPE_COLORS_HEX = TYPE_HEX;
  JMD.TYPE_COLORS = {
    episodic: hexToInt(TYPE_HEX.episodic),
    semantic: hexToInt(TYPE_HEX.semantic),
    entity:   hexToInt(TYPE_HEX.entity),
  };

  // ── Edge kinds ──
  var EDGE_HEX = {
    causal:        tokenHex('--edge-causal', '#8a5a5a'),
    co_occurrence: tokenHex('--edge-cooccurrence', '#5a6a8a'),
    'default':     tokenHex('--edge-default', '#8a8a8a'),
    virtual:       tokenHex('--edge-virtual', '#6a6a6a'),
    highlight:     tokenHex('--edge-highlight', '#8a5a45'),
  };
  JMD.EDGE_COLORS_HEX = EDGE_HEX;
  JMD.EDGE_COLORS = {
    causal:        hexToInt(EDGE_HEX.causal),
    co_occurrence: hexToInt(EDGE_HEX.co_occurrence),
    'default':     hexToInt(EDGE_HEX['default']),
    virtual:       hexToInt(EDGE_HEX.virtual),
    highlight:     hexToInt(EDGE_HEX.highlight),
  };

  // ── Category taxonomy (memory tagging, a chrome-adjacent data family).
  // Re-mapped onto existing canonical tokens rather than inventing new
  // hues — background is neutral chrome (--surface-chip in CSS); only the
  // icon/label colour carries data meaning, per "colour only from data". ──
  // Background stays chrome-neutral (a live CSS var, not a baked hex — this
  // is a DOM style attribute, so the browser re-resolves it on surface
  // change for free); only icon/label colour carries the data meaning.
  var CAT_BG = 'var(--surface-chip)';
  JMD.CATEGORY_DEFS = {
    decision:     { icon: '!', color: tokenHex('--accent-ink', '#a05a3a'), bg: CAT_BG, keywords: ['decision', 'decided'] },
    architecture: { icon: '#', color: tokenHex('--info-ink',   '#4a6a9a'), bg: CAT_BG, keywords: ['architecture'] },
    error:        { icon: 'x', color: tokenHex('--danger-ink', '#9a4a4a'), bg: CAT_BG, keywords: ['error', 'typeerror', 'fix'] },
    session:      { icon: '>', color: tokenHex('--emo-discov', '#5a5a9a'), bg: CAT_BG, keywords: ['session-summary'] },
    knowledge:    { icon: '*', color: tokenHex('--node-semantic', '#5a7a9a'), bg: CAT_BG, keywords: [] },
    other:        { icon: '.', color: tokenHex('--text-muted', '#7a7a7a'), bg: CAT_BG, keywords: [] },
  };

  // ── Agent/team identity — a genuinely separate data family (11 distinct
  // hues, no canonical token exists for "team member"). Values are CSS
  // custom properties defined in theme.css: evenly spaced 360/11 ≈ 32.7°
  // apart, ink L78%/C0.14, paper L50%/C0.13 — per shared/README.md's
  // deterministic re-inking rule. ──
  JMD.AGENT_COLORS = {
    engineer:     tokenHex('--agent-engineer',     '#8a7ab8'),
    tester:       tokenHex('--agent-tester',       '#7ab88a'),
    reviewer:     tokenHex('--agent-reviewer',     '#b8a87a'),
    architect:    tokenHex('--agent-architect',    '#7a9ab8'),
    dba:          tokenHex('--agent-dba',          '#7ab0b0'),
    researcher:   tokenHex('--agent-researcher',   '#b87a7a'),
    frontend:     tokenHex('--agent-frontend',     '#b87aa0'),
    security:     tokenHex('--agent-security',     '#b8957a'),
    devops:       tokenHex('--agent-devops',       '#7ab89a'),
    ux:           tokenHex('--agent-ux',           '#a07ab8'),
    orchestrator: tokenHex('--agent-orchestrator', '#7a8ab8'),
  };

  JMD.agentColor = function(agent) {
    return JMD.AGENT_COLORS[agent] || tokenHex('--text-muted', '#7a7a7a');
  };

  // ── Heat scale — reads the design system's own hot→cold data tokens. ──
  JMD.HEAT_COLORS = {
    hot:  tokenHex('--heat-hot',  '#b06a4a'),
    warm: tokenHex('--heat-warm', '#b0956a'),
    cool: tokenHex('--heat-cool', '#8aa0ac'),
    cold: tokenHex('--heat-cold', '#8a9aa2'),
  };

  // ── Consolidation lifecycle stages — used by the analytics charts
  // (labile / early-LTP / late-LTP / consolidated / reconsolidating maps
  // 1:1 onto the design system's own stage vocabulary). Refs: McClelland
  // et al. 1995; Foster & Wilson 2006 (see shared/palette.js). ──
  JMD.STAGE_COLORS = {
    labile:          tokenHex('--stage-labile', '#7a8ab8'),
    early_ltp:       tokenHex('--stage-early',  '#7aa8b8'),
    late_ltp:        tokenHex('--stage-late',   '#7ab894'),
    consolidated:    tokenHex('--stage-cons',   '#b8a87a'),
    reconsolidating: tokenHex('--stage-recon',  '#b87ab0'),
  };

  // ── Generic categorical palette for open-ended data keys (domains, free
  // tags) that have no canonical token — reuses the same 11 evenly-spaced
  // agent hues as a general discrete-category ramp. ──
  JMD.CATEGORICAL_PALETTE = [
    JMD.AGENT_COLORS.architect, JMD.AGENT_COLORS.tester, JMD.AGENT_COLORS.reviewer,
    JMD.AGENT_COLORS.security, JMD.AGENT_COLORS.researcher, JMD.AGENT_COLORS.frontend,
    JMD.AGENT_COLORS.ux, JMD.AGENT_COLORS.dba,
  ];

  // ── Canvas-baked chrome (bar-chart labels/values on an offscreen 2D
  // canvas — cannot read CSS, so resolve once here). ──
  JMD.CHART_TEXT = {
    label: tokenHex('--text-faint', '#8a8a8a'),
    value: tokenHex('--text-secondary', '#b0b0b0'),
  };

  JMD.heatColorCSS = function(h) {
    if (h > 0.7) return JMD.HEAT_COLORS.hot;
    if (h > 0.5) return JMD.HEAT_COLORS.warm;
    if (h > 0.3) return JMD.HEAT_COLORS.cool;
    return JMD.HEAT_COLORS.cold;
  };

  JMD.categorizeMemory = function(m) {
    var c = (m.content || '').toLowerCase();
    var tags = (m.tags || []).map(function(t) { return t.toLowerCase(); });

    for (var cat in JMD.CATEGORY_DEFS) {
      if (cat === 'knowledge' || cat === 'other') continue;
      var def = JMD.CATEGORY_DEFS[cat];
      for (var k = 0; k < def.keywords.length; k++) {
        if (tags.indexOf(def.keywords[k]) >= 0 || c.indexOf(def.keywords[k]) >= 0) return cat;
      }
    }
    if (m.store_type === 'semantic') return 'knowledge';
    return 'other';
  };

  JMD.timeAgo = function(iso) {
    if (!iso) return '—';
    var s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  };

  JMD.escHtml = function(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  };
})();
