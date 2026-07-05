// Cortex Neural Graph — Configuration
var JUG = window.JUG || {};
window.JUG = JUG;

JUG.API_URL = '/api/graph';

// ── Node colour — the DD-04 "point cloud at scale" table ───────────────────
// (cards/data-pointcloud.html, AI Architect design gate). This graph's
// ~119k nodes are TWO overlaid vocabularies: the raw memory-store data
// (memory/entity/topic/bridge-entity/discussion — what DD-04 plots) and a
// methodology skeleton laid on top (root -> category -> domain -> agent ->
// type-group, plus its entry-point/recurring-pattern/tool-preference/
// behavioral-feature leaves). DD-04 names exactly four hues for this view:
// episodic memory, semantic memory, entity/file, and one olive hub per
// domain — never a per-subtype rainbow. Structural levels above already
// carry hierarchy via SIZE (nodeRadius, draw.js), so they share the ONE
// hub hue rather than each getting a distinct chrome colour (backend's own
// graph_builder_edges.py _SKELETON_TYPES groups root/category/domain/agent/
// type-group identically, corroborating "one landmark family"). Leaf data
// with no DS-sanctioned hue of its own (methodology leaves, entity
// subtypes, topic, bridge-entity) shares DD-04's "entity/file" bucket
// rather than inventing an unsourced mapping.
//
// Literals below are the pre-hydration fallback only (module-load-order
// safety) — JUG._tok is re-hydrated from CortexPalette on load and on
// every cortex:surface-change, and getNodeColor() below reads JUG._tok,
// never these literals, once hydration has run.
JUG._tok = {
  hub: '#8D6D00',      // --warn-deep   — structural skeleton landmarks
  info: '#2F4A78',     // --info-deep   — entity/file + methodology leaves
  episodic: '#2F6B4A', // --stage-late  — episodic memory + discussion
  semantic: '#6B4A7A', // --stage-recon — semantic memory
  fieldPoint: '#B8AC98', // --field-point — ambient, opaque, never alpha-faded
  accentDeep: '#A53E00', // --accent-deep — selection only (G4)
};

JUG._hydrateGraphTokens = function () {
  if (!window.CortexPalette) return;
  var hex = window.CortexPalette.hex;
  JUG._tok.hub = hex('--warn-deep') || JUG._tok.hub;
  JUG._tok.info = hex('--info-deep') || JUG._tok.info;
  JUG._tok.episodic = hex('--stage-late') || JUG._tok.episodic;
  JUG._tok.semantic = hex('--stage-recon') || JUG._tok.semantic;
  JUG._tok.fieldPoint = hex('--field-point') || JUG._tok.fieldPoint;
  JUG._tok.accentDeep = hex('--accent-deep') || JUG._tok.accentDeep;
};
JUG._hydrateGraphTokens();
if (window.CortexSurface) {
  window.addEventListener(window.CortexSurface.EVENT, JUG._hydrateGraphTokens);
}

// Structural skeleton — landmarks, not raw data (see rationale above).
JUG._hubTypes = {
  'root': true, 'category': true, 'domain': true,
  'agent': true, 'type-group': true,
};

// Edge colour — G3 ("chrome is greyscale; colour comes only from data
// tokens or the single terracotta accent") applies here too: a link is
// graph chrome, not a data point, so every relationship type shares ONE
// neutral structural hairline (--border-strong) rather than an ad-hoc
// per-relationship-type rainbow (G9). Keys preserved 1:1 for
// detail_panel.js, which reads this table directly for its edge-type
// swatches; only the values move to the token. Literal below is the
// pre-hydration fallback only.
JUG.EDGE_COLORS = (function () {
  var keys = [
    'has-category', 'has-project', 'has-agent', 'has-group', 'groups',
    'bridge', 'persistent-feature', 'co_occurrence', 'imports', 'calls',
    'caused_by', 'resolved_by', 'decided_to_use', 'debugged_with',
    'memory-entity', 'domain-entity', 'domain-contains', 'topic-member',
    'co-entity', 'default', 'has-discussion',
  ];
  var table = {};
  keys.forEach(function (k) { table[k] = '#5A5A5A'; });
  return table;
})();

JUG._hydrateEdgeColors = function () {
  if (!window.CortexPalette) return;
  var borderStrong = window.CortexPalette.hex('--border-strong');
  if (!borderStrong) return;
  Object.keys(JUG.EDGE_COLORS).forEach(function (k) {
    JUG.EDGE_COLORS[k] = borderStrong;
  });
};
JUG._hydrateEdgeColors();
if (window.CortexSurface) {
  window.addEventListener(window.CortexSurface.EVENT, JUG._hydrateEdgeColors);
}

JUG.NODE_LABELS = {
  'root': 'Cortex',
  'category': 'Category',
  'domain': 'Project',
  'agent': 'Agent',
  'type-group': 'Group',
  'entry-point': 'Entry Point',
  'recurring-pattern': 'Pattern',
  'tool-preference': 'Tool',
  'behavioral-feature': 'Feature',
  'memory': 'Memory',
  'entity': 'Entity',
  'topic': 'Topic',
  'bridge-entity': 'Bridge',
  'discussion': 'Discussion',
};

JUG.CONSOLIDATION_COLORS = {
  'labile': '#00D2FF',
  'early_ltp': '#60A0E0',
  'late_ltp': '#40D870',
  'consolidated': '#E8B840',
  // 'reconsolidating' has no dedicated design-system token (README's
  // canonical stage table only names labile/early/late/cons/semantic) —
  // kept as a literal fallback, not hydrated below.
  'reconsolidating': '#C070D0',
};

// Hydrate labile/early/late/consolidated from the design-system stage
// tokens (same source as JUG._hydrateStageColors above) so the
// consolidation-stage ring drawn in draw.js re-inks per surface.
JUG._hydrateConsolidationColors = function () {
  if (!window.CortexPalette) return;
  var stages = window.CortexPalette.stages();
  JUG.CONSOLIDATION_COLORS.labile = stages['labile'] || JUG.CONSOLIDATION_COLORS.labile;
  JUG.CONSOLIDATION_COLORS.early_ltp = stages['early-ltp'] || JUG.CONSOLIDATION_COLORS.early_ltp;
  JUG.CONSOLIDATION_COLORS.late_ltp = stages['late-ltp'] || JUG.CONSOLIDATION_COLORS.late_ltp;
  JUG.CONSOLIDATION_COLORS.consolidated = stages['consolidated'] || JUG.CONSOLIDATION_COLORS.consolidated;
};
JUG._hydrateConsolidationColors();
if (window.CortexSurface) {
  window.addEventListener(window.CortexSurface.EVENT, JUG._hydrateConsolidationColors);
}

JUG.CONSOLIDATION_LABELS = {
  'labile': 'Labile',
  'early_ltp': 'Early LTP',
  'late_ltp': 'Late LTP',
  'consolidated': 'Consolidated',
  'reconsolidating': 'Reconsolidating',
};

JUG.ZOOM_LEVELS = {
  L3: { minDist: 1200, label: 'Universe' },
  L2: { minDist: 600, label: 'Galaxy' },
  L1: { minDist: 200, label: 'Constellation' },
  L0: { minDist: 0, label: 'Neural' },
};

// Structural types that form the tree skeleton
JUG.STRUCTURAL_TYPES = { 'root': true, 'category': true, 'domain': true, 'agent': true, 'type-group': true, 'topic': true, 'bridge-entity': true };

// DD-04 point-cloud lookup — TYPE decides colour, always, never a
// server-baked node.color. The backend (graph_builder_nodes.py et al.)
// bakes a static hex per node at build time; trusting it here would
// permanently defeat G2 (survive data-surface="ink" unchanged) since a
// baked hex can never re-resolve on toggle. This is the root-cause fix,
// not a workaround: presentation of DS-governed data belongs to the
// client's token layer, not the wire payload (Trace's session/prompt/
// action nodes never reach this function — they render through their own
// self-contained LOD canvas, workflow_graph.js — so there is no live
// caller left that needs the old node.color short-circuit).
JUG.getNodeColor = function(node) {
  if (!node) return JUG._tok.info;
  var t = node.type;
  if (t === 'memory') return node.storeType === 'semantic' ? JUG._tok.semantic : JUG._tok.episodic;
  if (t === 'discussion') return JUG._tok.episodic;
  if (JUG._hubTypes[t]) return JUG._tok.hub;
  // entity (any entityType) / topic / bridge-entity / entry-point /
  // recurring-pattern / tool-preference / behavioral-feature — the
  // informational leaf family (DD-04 "entity/file").
  return JUG._tok.info;
};

JUG.getEdgeColor = function(edge) {
  return JUG.EDGE_COLORS[edge.type] || JUG.EDGE_COLORS['default'];
};

// Clean markdown + tool captures from labels for display
JUG.cleanText = function(raw) {
  if (!raw) return '';
  var s = raw.replace(/^#+\s*/g, '').replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1').replace(/\s+/g, ' ').trim();
  return JUG._cleanToolLabel(s);
};

JUG._cleanToolLabel = function(s) {
  // Extract file path — stop before Output:/Command: or JSON
  var fp = JUG._extractFilePath(s);
  // "Tool: Edit File: /long/path/to/file.py ..." → "Edit file.py"
  if (/^Tool:\s*Edit/i.test(s) && fp) return 'Edit ' + JUG._shortFile(fp);
  // "Tool: Write File: /path ..." → "Write file.py"
  if (/^Tool:\s*Write/i.test(s) && fp) return 'Write ' + JUG._shortFile(fp);
  // "Tool: Read ..." → "Read file.py"
  if (/^Tool:\s*Read/i.test(s) && fp) return 'Read ' + JUG._shortFile(fp);
  // "Tool: Bash Command: cmd" → just the command (short)
  var bashMatch = s.match(/^Tool:\s*Bash\s+Command:\s*(.+?)(?:\s+Output:|$)/i);
  if (bashMatch) { var c = bashMatch[1]; return c.length > 36 ? c.substring(0, 33) + '...' : c; }
  // "Tool: WebSearch ..." → "Web search"
  if (/^Tool:\s*Web/i.test(s)) return 'Web search';
  // "Tool: Grep ..." → "Search files"
  if (/^Tool:\s*Grep/i.test(s)) return 'Search files';
  // "File: path" → short filename
  if (/^File:\s/i.test(s) && fp) return JUG._shortFile(fp);
  return s;
};

JUG._extractFilePath = function(s) {
  // Match absolute path: /foo/bar or C:\foo\bar
  var m = s.match(/["']?(\/[^\s"'{}]+|[A-Za-z]:\\[^\s"'{}]+)/);
  if (m) return m[1].replace(/["']$/g, '');
  // Match relative path with extension: foo/bar.py, src/thing.ts
  var rel = s.match(/(?:File:\s*)?([\w./-]+\/[\w./-]+\.\w{1,10})/);
  if (rel) return rel[1];
  return '';
};

// Best label for canvas — uses content for tool captures (label is often truncated)
JUG._bestNodeLabel = function(node) {
  var label = JUG.cleanText(node.label || '');
  // If cleanText produced a good short label, use it
  if (label && !label.endsWith('...') && label.length > 3) return label;
  // Try content field (has full text for tool captures)
  if (node.content) {
    var fromContent = JUG.cleanText(node.content);
    if (fromContent && fromContent.length > 3) return fromContent;
  }
  return label || node.id || '';
};

JUG._shortFile = function(path) {
  if (!path) return '';
  var clean = path.replace(/^["']|["']$/g, '').trim();
  var parts = clean.split('/').filter(function(p) { return p; });
  if (parts.length <= 1) return clean;
  return parts[parts.length - 1];
};
