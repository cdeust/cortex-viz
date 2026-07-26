// Cortex — Workflow Graph token/colour resolution + shared render utilities.
//
// Extracted from workflow_graph.js (issue #41: 500-line file cap, §4.1).
// Owns the ONE concern of "resolve every node's colour/radius/label LIVE
// against the current design-system surface" (§1.1 SRP): the kind→token
// map, the getComputedStyle reader, the surface-change repaint listener,
// and the nodeRadius/nodeColor/labelOf utilities the two renderers read.
//
// Consumes window.JUG._wfgConst.KIND_RADIUS (loaded first).
// Publishes on window.JUG._wfg:
//   nodeRadius / nodeColor / labelOf   (renderers-agree test seams)
//   KIND_COLOR                         (mount() puts it on ctx.KIND_COLOR)
//   setActiveRenderer / clearActiveRenderer (mount()/destroy() wiring)
(function () {
  var KIND_RADIUS = window.JUG._wfgConst.KIND_RADIUS;

  // G9 (design gate): never bake a hex table — resolve every fallback colour
  // LIVE against the design-system tokens on the CURRENT surface. KIND_TOKEN
  // maps each node kind to a CSS custom property name; resolveKindColor()
  // reads it via getComputedStyle (through window.CortexPalette, the shared
  // reader in ui/shared/palette.js — already loaded before this file, see
  // ui/unified-viz.html) so paper vs ink both render correctly with zero
  // per-surface literals here. n.color (server-baked) wins for every kind
  // EXCEPT tool_hub (see nodeColor below — hubs resolve --tool-<tool> live,
  // G7); this table is only ever the fallback, exactly as it was as a
  // static object.
  //
  // Mapping preserves the hue families verified in the prior pivot-restore
  // round (memory: cortex-viz-trace-pivot-restore.md) and the app's existing
  // per-surface --kind-*/--tool-* families (ui/unified/panels.css, already
  // authored for both surfaces): domain/session hubs share the DS hub token;
  // tool_hub/entity share the graphite "recedes" token (DD-04 — the ~100k
  // entity cloud recedes so hubs/memory read as signal); file/action default
  // to the read-tool family; web now has its own canonical DS token
  // (--tool-web) instead of borrowing --kind-mcp; skill/command/hook/agent/
  // mcp/discussion keep their existing per-surface --kind-* tokens; memory/
  // prompt/symbol take the nearest DATA-family hue (never a chrome grey —
  // G3 forbids colouring data with chrome tones).
  var KIND_TOKEN = {
    // domain/session hubs use the SURFACE-AWARE alias (--warn-ink), not the
    // raw --warn-deep primitive: --warn-deep is a single paper-only value
    // (tokens/colors.css :root, oklch(50% 0.11 80)) that never re-inks, so a
    // hub painted with it stayed paper-deep even on data-surface="ink" and
    // read flat/low-contrast against the night canvas (G7 regression fixed
    // 2026-07-05). --warn-ink (tokens/surfaces.css) resolves to --warn-deep
    // on paper and a lifted oklch(83% 0.12 80) on ink — same hue, correct
    // contrast on both.
    domain:     '--warn-ink',     // olive hub (DD-07), same token as domain hubs elsewhere
    session:    '--warn-ink',     // session hub — shares the hub token by design
    tool_hub:   '--kind-entity',  // graphite, deep (DD-04) — unreachable in practice: nodeColor resolves --tool-<tool> from the hub id first (G7)
    entity:     '--kind-entity',  // graphite, deep (DD-04) — the recede-so-hubs-read-as-signal token
    skill:      '--kind-skill',
    command:    '--kind-command',
    hook:       '--kind-hook',
    agent:      '--kind-agent',
    mcp:        '--kind-mcp',
    discussion: '--kind-discussion',
    memory:     '--ok-ink',       // emerald family — matches memory's established green hue
    file:       '--tool-read',    // per-tool color overrides; read is the neutral default
    action:     '--tool-read',    // per-tool color overrides (trace.js TOOL_COLOR) in almost all cases
    web:        '--tool-web',     // WebFetch/WebSearch target — canonical DD-07 tool-family token
    prompt:     '--stage-early',  // cyan family — nearest DATA hue to the prior prompt colour
    symbol:     '--info-ink',     // inherits parent-file color via node.color in practice
  };
  // G3/G7: last-resort colour is a TOKEN, never a raw hex literal — resolves
  // through the same _readToken() path as every other entry so it stays
  // surface-correct (DEEP on paper / lifted on ink) even in the fallback
  // case. --field-point (tokens/surfaces.css) is defined on both surfaces,
  // so this only fails to resolve if the design-system stylesheet itself
  // never loaded (in which case nothing on the page is styled anyway).
  var FALLBACK_TOKEN = '--field-point';

  // G2 (design gate): the currently-mounted canvas/SVG renderer handle, so
  // the 'cortex:surface-change' listener below can trigger a REPAINT (never
  // a re-simulation) of the settled galaxy. Only one workflow-graph instance
  // is ever mounted at a time — Graph and Trace share one wrapper/handle,
  // destroyed-then-recreated on tab switch (workflow_graph_bridge.js) — so a
  // single module-scope reference is sufficient; it is set in mount() and
  // cleared in handle.destroy() below.
  var _activeRenderer = null;
  function _repaintActiveRenderer() {
    if (_activeRenderer && typeof _activeRenderer.redraw === 'function') {
      try { _activeRenderer.redraw(); } catch (_e) { /* non-fatal: next tick/interaction repaints anyway */ }
    }
  }

  var _localTokenCache = {};
  function _readToken(token) {
    if (window.CortexPalette) return window.CortexPalette.hex(token);
    // Defensive direct path (palette script missing): same getComputedStyle
    // read, cached per (surface, token) so cache invalidates automatically
    // whenever data-surface changes.
    var surface = document.documentElement.getAttribute('data-surface') || 'paper';
    var key = surface + '|' + token;
    if (_localTokenCache[key]) return _localTokenCache[key];
    var v = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    if (v) _localTokenCache[key] = v;
    return v || null;
  }
  if (window.CortexSurface) {
    // Belt-and-suspenders: CortexPalette already flushes its own cache on
    // this event (registered earlier — palette.js loads before this file,
    // see ui/unified-viz.html — so it has already run by the time this
    // listener fires); this clears the defensive local cache above, THEN
    // repaints the settled galaxy on the CURRENT surface's re-inked tokens.
    // G2 fix (design gate, 2026-07-05): invalidating the cache alone left
    // the canvas showing the previous surface's baked pixels until the next
    // unrelated interaction (hover/zoom/click) forced a draw() — a toggle
    // to ink kept stale paper-deep hub fills/labels on screen. Repainting
    // here is drawing-only: _repaintActiveRenderer -> renderer.redraw() ->
    // draw(), which reads current n.x/n.y and re-resolves every colour; it
    // never calls sim.restart()/alpha(...).restart() and never writes a
    // node position, so positions stay bit-identical across the toggle.
    window.addEventListener(window.CortexSurface.EVENT, function () {
      _localTokenCache = {};
      _repaintActiveRenderer();
    });
  }
  function resolveKindColor(kind) {
    var token = KIND_TOKEN[kind];
    return (token && _readToken(token)) || null;
  }
  // Live-resolving object so `KIND_COLOR[n.kind]` (nodeColor, below) and any
  // external reader of ctx.KIND_COLOR keep working unchanged — each property
  // read re-resolves against the current surface instead of returning a
  // value baked in at file-load time.
  var KIND_COLOR = {};
  Object.keys(KIND_TOKEN).forEach(function (k) {
    Object.defineProperty(KIND_COLOR, k, {
      enumerable: true,
      get: function () { return resolveKindColor(k) || _readToken(FALLBACK_TOKEN); },
    });
  });

  // Exposed shared utilities for renderer modules.
  function nodeRadius(n) {
    var base = KIND_RADIUS[n.kind] != null ? KIND_RADIUS[n.kind] : 6;
    var bump = 0;
    if (n.size != null) bump = Math.max(-2, Math.min(6, n.size - base));
    else if (n.weight != null) bump = Math.min(4, n.weight * 2);
    return base + bump;
  }
  // G3/G7: last-resort branch (unmapped n.kind — not in KIND_TOKEN at all,
  // so KIND_COLOR[n.kind] has no getter and is undefined) resolves the same
  // FALLBACK_TOKEN, never a raw hex literal.
  //
  // G7 (design gate): tool_hub nodes resolve their per-tool token LIVE and
  // never trust the server-baked n.color. The bake (workflow_graph_palette.py
  // TOOL_HUB_COLORS) is a paper-only encoding that cannot re-ink on the
  // data-surface toggle (measured 2.94:1 on ink — BLOCKED, gate audit
  // 2026-07-06), and its hues had drifted from the glossary's shipped
  // contract (unified-viz.html "Tool hub" entry colours by
  // var(--tool-edit/-write/-read/-grep/-glob/-bash/-task)). Resolving the
  // same tokens here makes galaxy and legend agree on BOTH surfaces —
  // same doctrine as JUG.getNodeColor (config.js): presentation of
  // DS-governed data belongs to the client's token layer, not the wire
  // payload. The trailing id segment IS the ToolKind value
  // (NodeIdFactory.tool_hub_id → 'tool_hub:<domain_id>:<tool>',
  // schema-validated by _check_tool_hub_pairs).
  function resolveToolHubColor(n) {
    // ToolKind values are capitalised ('Task', 'Bash', …) while the CSS
    // custom properties are lowercase (--tool-task) and case-sensitive —
    // verified against live /api/graph/full/stream ids, 2026-07-06.
    var tool = String(n.id || '').split(':').pop().toLowerCase();
    return tool ? _readToken('--tool-' + tool) : null;
  }
  function nodeColor(n) {
    if (n.kind === 'tool_hub') {
      var hubColor = resolveToolHubColor(n);
      if (hubColor) return hubColor;
    }
    return n.color || KIND_COLOR[n.kind] || _readToken(FALLBACK_TOKEN);
  }
  function labelOf(n) { return n.label || n.name || n.title || n.path || n.id || ''; }

  window.JUG = window.JUG || {};
  window.JUG._wfg = window.JUG._wfg || {};
  window.JUG._wfg.nodeRadius = nodeRadius;
  window.JUG._wfg.nodeColor  = nodeColor;
  window.JUG._wfg.labelOf    = labelOf;
  window.JUG._wfg.KIND_COLOR = KIND_COLOR;
  window.JUG._wfg.setActiveRenderer = function (r) { _activeRenderer = r; };
  window.JUG._wfg.clearActiveRenderer = function (r) {
    if (_activeRenderer === r) _activeRenderer = null;
  };
})();
