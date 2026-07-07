// Cortex Brain View — color palette + node sub-labels for the legend.
//
// LABELS RESOLVE FROM METADATA, NOT FROM COLOUR. The graph payload carries
// the canonical sub-kind the server bakes each colour FROM (entity nodes →
// `entityType`, AST symbols → `symbol_type`, memories → `stage`; baked in
// cortex_viz/core/workflow_graph_entity.py, workflow_graph_builder_relational
// .py and workflow_graph_builder_ingest.py — verified on the live
// /api/graph/full/stream 2026-07-04). The legend therefore labels a node from
// those fields first — the same canonical table the colours come from, exactly
// as the stage family resolves from the live CortexPalette token reader.
//
// The colour→label reverse maps below are kept ONLY as a documented fallback
// for payloads that predate the metadata fields, and their values now carry
// BOTH palette generations (pre-rebrand neon-on-black + the 2026-07-04
// paper-deep re-inking of cortex_viz/core/workflow_graph_palette.py). A hex
// reverse-lookup is the fragile path — the re-inked bakes silently fell
// through to "other" (user report 2026-07-04: 32,826 entities lumped into
// one "entity (other)" row) — which is why it is no longer the primary path.
//   source: unify + exhaustive-legend pass 2026-07-03; metadata-first root
//   fix + fallback-map realignment 2026-07-04.

window.BRAIN = window.BRAIN || {};

(function () {
  // Consolidation-stage → colour, read LIVE from the shared design-system
  // token reader (ui/shared/palette.js) so both surfaces (paper deep / ink
  // bright) resolve from the SAME canonical oklch table the README documents
  // — never a hand-picked hex. "reconsolidating" maps to the table's
  // "semantic" token (the recon/extracted-schema hue). Falls back to the
  // pre-rebrand ink-tuned hexes if /shared/palette.js failed to load, so the
  // legend/vitals never render with undefined colours.
  var FALLBACK_STAGE_COLORS = {
    labile:          '#86EFAC',   // New
    early_ltp:       '#4ADE80',   // Growing (also the 'episodic' default)
    late_ltp:        '#16A34A',   // Strong
    consolidated:    '#166534',   // Stable
    reconsolidating: '#2DD4BF',   // Updating (server palette addition)
  };

  function liveStageColors() {
    if (!window.CortexPalette) return FALLBACK_STAGE_COLORS;
    var t = window.CortexPalette.stages(); // { labile, early-ltp, late-ltp, consolidated, semantic }
    return {
      labile: t['labile'] || FALLBACK_STAGE_COLORS.labile,
      early_ltp: t['early-ltp'] || FALLBACK_STAGE_COLORS.early_ltp,
      late_ltp: t['late-ltp'] || FALLBACK_STAGE_COLORS.late_ltp,
      consolidated: t['consolidated'] || FALLBACK_STAGE_COLORS.consolidated,
      reconsolidating: t['semantic'] || FALLBACK_STAGE_COLORS.reconsolidating,
    };
  }

  // Recomputed on every read (CortexPalette itself caches per-surface and
  // flushes its cache on cortex:surface-change) so STAGE_COLORS always
  // reflects the CURRENT posture without this module needing its own
  // surface-change listener.
  var STAGE_COLORS_PROXY = {};
  ['labile', 'early_ltp', 'late_ltp', 'consolidated', 'reconsolidating'].forEach(function (key) {
    Object.defineProperty(STAGE_COLORS_PROXY, key, {
      enumerable: true,
      get: function () { return liveStageColors()[key]; },
    });
  });

  // ── Colour→label FALLBACK maps (old payloads without kind metadata) ──────
  // Entity colours: server still bakes from graph_builder_nodes.ENTITY_COLORS
  // (unchanged as of 2026-07-04) — this map mirrors it verbatim.
  //   source: cortex_viz/core/graph_builder_nodes.py ENTITY_COLORS +
  //   workflow_graph_entity.py default '#50B0C8'.
  var ENTITY_TYPE_COLORS = {
    '#50D0E8': 'function', '#60A0E0': 'dependency', '#E07070': 'error',
    '#E0C050': 'decision', '#9080D0': 'technology', '#7088D0': 'file',
    '#50B8D0': 'variable', '#50B0C8': 'entity (other)',
  };
  // Symbol colours: both generations of SYMBOL_COLORS.
  //   source: cortex_viz/core/workflow_graph_palette.py — pre-rebrand hexes
  //   (unify pass 2026-07-03) + paper-deep re-inking 2026-07-04 (each hex
  //   carries its oklch provenance at the definition site).
  var SYMBOL_TYPE_COLORS = {
    // pre-rebrand (neon-on-black) bakes
    '#22D3EE': 'function', '#38BDF8': 'method', '#8B5CF6': 'class / type',
    '#FBBF24': 'module', '#94A3B8': 'constant / import', '#A1A1AA': 'symbol (other)',
    // paper-deep re-inked bakes (2026-07-04)
    '#00738B': 'function', '#0F7BA7': 'method', '#5E41A2': 'class / type',
    '#8C6000': 'module', '#596475': 'constant / import', '#62626C': 'symbol (other)',
  };
  // File primary-tool colours: both generations of PRIMARY_TOOL_COLORS.
  //   source: cortex_viz/core/workflow_graph_palette.py PRIMARY_TOOL_COLORS
  //   (pre-rebrand + 2026-07-04 re-inking); '#8AA0C0' is the client-side
  //   untouched-file default (boot.js buildNodeColors fallback).
  var FILE_TOOL_COLORS = {
    // pre-rebrand bakes
    '#10B981': 'authored (edit/write)', '#059669': 'authored (write)',
    '#06B6D4': 'read', '#D946EF': 'searched', '#C026D3': 'searched (glob)',
    '#F97316': 'shell (bash)', '#8AA0C0': 'untouched',
    // paper-deep re-inked bakes (2026-07-04)
    '#00784F': 'authored (edit/write)', '#00673D': 'authored (write)',
    '#00728A': 'read', '#8B3C98': 'searched', '#7A2984': 'searched (glob)',
    '#A04400': 'shell (bash)',
  };
  // Tool-hub colours: both generations of TOOL_HUB_COLORS.
  //   source: cortex_viz/core/workflow_graph_palette.py TOOL_HUB_COLORS
  //   (pre-rebrand + 2026-07-04 re-inking).
  var TOOL_HUB_COLORS = {
    // pre-rebrand bakes
    '#10B981': 'edit', '#059669': 'write', '#06B6D4': 'read', '#D946EF': 'grep',
    '#C026D3': 'glob', '#F97316': 'bash', '#EC4899': 'task',
    // paper-deep re-inked bakes (2026-07-04); task shares agent pink by design
    '#00784F': 'edit', '#00673D': 'write', '#00728A': 'read', '#8B3C98': 'grep',
    '#7A2984': 'glob', '#A04400': 'bash', '#A33069': 'task',
  };

  // Stage-label friendly names, independent of colour, in canonical order.
  // 'episodic' is the PG default before LTP promotion — it reads as the
  // growing band, matching the server baking it with the early_ltp hue.
  // 'semantic' is the extracted-schema store (ui/shared/palette.js:
  // "semantic (extracted schema)").
  var STAGE_FRIENDLY = {
    labile: 'labile · new', early_ltp: 'early-LTP · growing',
    late_ltp: 'late-LTP · strong', consolidated: 'consolidated · stable',
    reconsolidating: 'reconsolidating · updating',
    episodic: 'early-LTP · growing', semantic: 'semantic · schema',
  };
  // Static reverse map — fallback lookup for a memory node WITHOUT a `stage`
  // field. Carries both generations of MEMORY_STAGE_COLORS.
  //   source: cortex_viz/core/workflow_graph_palette.py MEMORY_STAGE_COLORS
  //   (pre-rebrand + 2026-07-04 paper-deep re-inking).
  var STAGE_BY_COLOR_FALLBACK = {
    // pre-rebrand bakes
    '#86EFAC': STAGE_FRIENDLY.labile, '#4ADE80': STAGE_FRIENDLY.early_ltp,
    '#16A34A': STAGE_FRIENDLY.late_ltp, '#166534': STAGE_FRIENDLY.consolidated,
    '#2DD4BF': STAGE_FRIENDLY.reconsolidating, '#C070D0': STAGE_FRIENDLY.semantic,
    // paper-deep re-inked bakes (2026-07-04)
    '#006894': STAGE_FRIENDLY.labile, '#006A66': STAGE_FRIENDLY.early_ltp,
    '#0A693C': STAGE_FRIENDLY.late_ltp, '#7D6700': STAGE_FRIENDLY.consolidated,
    '#007760': STAGE_FRIENDLY.reconsolidating, '#753E81': STAGE_FRIENDLY.semantic,
  };

  // Per-kind colour→label FALLBACK table. A kind absent here is single-colour;
  // its legend row uses the kind name, no sub-label.
  var BY_KIND = {
    entity: ENTITY_TYPE_COLORS,
    symbol: SYMBOL_TYPE_COLORS,
    file: FILE_TOOL_COLORS,
    tool_hub: TOOL_HUB_COLORS,
  };

  // Wire field carrying each kind's canonical sub-kind (see header comment).
  var META_FIELD = { entity: 'entityType', symbol: 'symbol_type' };

  function norm(hex) { return String(hex || '#8AA0C0').toUpperCase(); }

  // Stage lookup checks the LIVE canonical colours first (matches a server
  // already aligned to the shared oklch table on the current surface), then
  // falls back to the static map (both palette generations) — so the legend
  // labels correctly either way.
  function stageLabelFor(colorHex) {
    var c = norm(colorHex);
    var live = liveStageColors();
    for (var key in live) {
      if (norm(live[key]) === c) return STAGE_FRIENDLY[key];
    }
    return STAGE_BY_COLOR_FALLBACK[c] || null;
  }

  // Colour → sub-label for a kind, or null when the kind is single-colour
  // (or the colour is unmapped — caller then shows the raw kind name only).
  // FALLBACK path: prefer subLabelFor(node), which reads the canonical
  // metadata the colour was baked from.
  function labelFor(kind, colorHex) {
    if (kind === 'memory') return stageLabelFor(colorHex);
    var table = BY_KIND[kind];
    if (!table) return null;
    return table[norm(colorHex)] || null;
  }

  // Canonical sub-label straight from the node's own metadata — the same
  // live table the server bakes the colour from. Lowercased so the two
  // entity emitters agree ('Function' from the AST bridge, 'function' from
  // knowledge-graph extraction). Unknown-but-present values pass through
  // verbatim, so new backend sub-kinds appear in the legend automatically
  // (same live-payload doctrine as the vitals rows).
  function metaLabel(node, kind) {
    if (kind === 'memory') {
      var stage = node.stage;
      if (!stage) return null;
      var key = String(stage).toLowerCase().replace(/-/g, '_');
      return STAGE_FRIENDLY[key] || key;
    }
    var field = META_FIELD[kind];
    var t = field ? node[field] : null;
    return t ? String(t).toLowerCase() : null;
  }

  // Categorical hue per associative community (communities.js), for Change A
  // memory-node colouring (BRAIN.COLOR_BY_COMMUNITY). Deterministic: hue
  // steps by the golden angle (360 * (1 - 1/phi), phi = golden ratio) so
  // consecutive community ids land maximally far apart on the wheel instead
  // of clustering near each other — same golden-angle spiral force_layout.js
  // uses to place the community attractors, applied to hue instead of a
  // sphere. Visual calibration (saturation/lightness), not a sourced
  // physical constant — same status as every other UI-legibility constant in
  // this module; only the hue-STEP angle traces to a source.
  //   source: 360 * (1 - 1/phi) golden-angle step, see force_layout.js
  //   GOLDEN_ANGLE_RAD comment (Saff & Kuijlaars 1997).
  var COMMUNITY_GOLDEN_ANGLE_DEG = 137.50776;

  function communityColor(communityId) {
    var hue = ((communityId * COMMUNITY_GOLDEN_ANGLE_DEG) % 360 + 360) % 360;
    return 'hsl(' + hue.toFixed(1) + ', 58%, 42%)';
  }

  BRAIN.PALETTE = {
    STAGE_COLORS: STAGE_COLORS_PROXY,
    communityColor: communityColor,
    // Sub-label for a NODE: canonical metadata first (entityType /
    // symbol_type / stage), colour reverse-lookup as the documented
    // fallback for payloads that predate the metadata fields.
    subLabelFor: function (node) {
      if (!node) return null;
      var kind = node.kind || node.type;
      if (!kind) return null;
      return metaLabel(node, kind) || labelFor(kind, node.color);
    },
    labelFor: labelFor,
    // True when a kind renders with more than one semantic colour and so
    // warrants an exhaustive per-sub-kind breakdown in the legend.
    isGraded: function (kind) { return kind === 'memory' || !!BY_KIND[kind]; },
  };
})();
