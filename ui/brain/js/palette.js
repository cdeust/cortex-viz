// Cortex Brain View — color palette + reverse labels.
//
// MIRRORS the server source of truth so the legend is exhaustive AND the
// stage colors are unified with the nodes:
//   cortex_viz/core/workflow_graph_palette.py  (MEMORY_STAGE_COLORS, SYMBOL_COLORS)
//   cortex_viz/core/graph_builder_nodes.py      (ENTITY_COLORS)
// Node colours are baked server-side from these exact maps; here we invert
// them (colour → human label) so every distinct colour a kind renders with
// gets its own labelled legend row, and so the memory-science stage rows use
// the SAME greens the memory nodes use. Keep in sync with the Python maps.
//   source: unify + exhaustive-legend pass 2026-07-03.

window.BRAIN = window.BRAIN || {};

(function () {
  // Consolidation-stage → colour. Canonical: memory nodes are painted from
  // this map (ingest: MEMORY_STAGE_COLORS.get(stage)), and the vitals stage
  // rows (New/Growing/Strong/Stable/Updating) now use the SAME values.
  var STAGE_COLORS = {
    labile:          '#86EFAC',   // New
    early_ltp:       '#4ADE80',   // Growing (also the 'episodic' default)
    late_ltp:        '#16A34A',   // Strong
    consolidated:    '#166534',   // Stable
    reconsolidating: '#2DD4BF',   // Updating (server palette addition)
  };

  var ENTITY_TYPE_COLORS = {
    '#50D0E8': 'function', '#60A0E0': 'dependency', '#E07070': 'error',
    '#E0C050': 'decision', '#9080D0': 'technology', '#7088D0': 'file',
    '#50B8D0': 'variable', '#50B0C8': 'entity (other)',
  };
  var SYMBOL_TYPE_COLORS = {
    '#22D3EE': 'function', '#38BDF8': 'method', '#8B5CF6': 'class / type',
    '#FBBF24': 'module', '#94A3B8': 'constant / import', '#A1A1AA': 'symbol (other)',
  };
  var FILE_TOOL_COLORS = {
    '#10B981': 'authored (edit/write)', '#059669': 'authored (write)',
    '#06B6D4': 'read', '#D946EF': 'searched', '#C026D3': 'searched (glob)',
    '#F97316': 'shell (bash)', '#8AA0C0': 'untouched',
  };
  var TOOL_HUB_COLORS = {
    '#10B981': 'edit', '#059669': 'write', '#06B6D4': 'read', '#D946EF': 'grep',
    '#C026D3': 'glob', '#F97316': 'bash', '#EC4899': 'task',
  };

  // Invert the stage map (colour → "stage · friendly"). early_ltp shares its
  // colour with the episodic default, so #4ADE80 reads as "growing".
  var STAGE_BY_COLOR = {
    '#86EFAC': 'labile · new', '#4ADE80': 'early-LTP · growing',
    '#16A34A': 'late-LTP · strong', '#166534': 'consolidated · stable',
    '#2DD4BF': 'reconsolidating · updating',
  };

  // Per-kind colour→label table. A kind absent here is single-colour; its
  // legend row uses the kind name, no sub-label.
  var BY_KIND = {
    memory: STAGE_BY_COLOR,
    entity: ENTITY_TYPE_COLORS,
    symbol: SYMBOL_TYPE_COLORS,
    file: FILE_TOOL_COLORS,
    tool_hub: TOOL_HUB_COLORS,
  };

  function norm(hex) { return String(hex || '#8AA0C0').toUpperCase(); }

  BRAIN.PALETTE = {
    STAGE_COLORS: STAGE_COLORS,
    // Colour → sub-label for a kind, or null when the kind is single-colour
    // (or the colour is unmapped — caller then shows the raw kind name only).
    labelFor: function (kind, colorHex) {
      var table = BY_KIND[kind];
      if (!table) return null;
      return table[norm(colorHex)] || null;
    },
    // True when a kind renders with more than one semantic colour and so
    // warrants an exhaustive per-colour breakdown in the legend.
    isGraded: function (kind) { return !!BY_KIND[kind]; },
  };
})();
