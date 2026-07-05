// Cortex — Workflow Graph (D3 v7 force layout): orchestration + forces.
// Target: many small brain-region clouds, each internally structured,
// with thin long-range threads between clouds where files/entities are shared.
// Schema: mcp_server/core/workflow_graph_schema.py
//   node kinds: domain, skill, command, hook, agent, tool_hub, file, memory, discussion, entity
//   edge kinds: in_domain, tool_used_file, command_in_hub, invoked_skill, triggered_hook, spawned_agent, about_entity
// Public API: window.JUG.renderWorkflowGraph(container, data) -> { destroy, select, data }.
// Renderers are provided by workflow_graph_render_svg.js / _canvas.js on JUG._wfg.
(function () {
  var D3_URL = 'https://cdn.jsdelivr.net/npm/d3@7.8.5/dist/d3.min.js';
  // Always render via canvas. SVG path (mountSVG) cannot grow
  // incrementally — its d3-enter/exit selections are bound once at
  // mount time, so calling handle.append() later would never paint
  // new circles. Canvas reads ctx.nodes/ctx.edges every frame, so
  // pushing into those arrays is enough for the new nodes to show.
  // The visual difference at small N is negligible; ergonomics of
  // a unified renderer path are worth the trade.
  var CANVAS_THRESHOLD = 0;

  // Tokens — kind-driven radii, colors, edge distances, strengths.
  var KIND_RADIUS = {
    domain: 26, tool_hub: 14, agent: 10, skill: 10, command: 8,
    hook: 9, memory: 7, discussion: 8, entity: 6, file: 5, mcp: 12,
    symbol: 2,
    session: 16, prompt: 9, action: 6,
  };
  // G9 (design gate): never bake a hex table — resolve every fallback colour
  // LIVE against the design-system tokens on the CURRENT surface. KIND_TOKEN
  // maps each node kind to a CSS custom property name; resolveKindColor()
  // reads it via getComputedStyle (through window.CortexPalette, the shared
  // reader in ui/shared/palette.js — already loaded before this file, see
  // ui/unified-viz.html) so paper vs ink both render correctly with zero
  // per-surface literals here. n.color (server-baked) always wins — this
  // table is only ever the fallback, exactly as it was as a static object.
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
    tool_hub:   '--kind-entity',  // graphite, deep (DD-04) — per-tool colors override in node.color
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
  // Radial hierarchy inside each domain cloud — FIVE concentric/sector levels:
  //   L1 setup  (skills/hooks/commands/agents)   @ r = SETUP_R   front sector
  //   L2 tools  (tool_hub)                        @ r = TOOL_R    front sector
  //   L3 files  (primary-tool colored)            @ r = FILE_R    front sector
  //   L4 discussions                              @ r = DISC_R    side sector A
  //   L5 memories                                 @ r = MEM_R     side sector B
  //   MCPs sit INWARD (between domains) and bridge out.
  // Radii are sized so the rings are visually separated — each shell has
  // a band of at least 40px between it and the next. Large enough that
  // even dense domains keep their structure legible when zoomed out.
  var SETUP_R = 70;
  var TOOL_R  = 140;
  var FILE_R  = 220;
  var DISC_R  = 150;
  var MEM_R   = 150;
  var MCP_R   = 50;
  // Symbols form a dense cloud JUST outside the file ring — this is the
  // "petal" shell that gives the graph the screenshot look.  The cloud
  // is anchored per-file so each file becomes a small satellite clump.
  var SYM_R_OUTER = 290;    // outer edge of the symbol shell
  var SYM_R_SPREAD = 32;    // radial jitter per-file-group
  var SYM_CLUMP_R = 18;     // tight clumping distance around parent file
  // L5+E entity layer — see docs/adr/ADR-0047-entity-positioning-gap10.md
  // for the full provenance of every constant below (Kekulé centroid +
  // Alexander heat gate + Thompson physics retune, each tied to a
  // specific live-data measurement on 2026-04-23).
  var ENTITY_DOMAIN_BLEND = 0.15;          // ADR-0047: α in (1−α)·mem_centroid + α·domain_hub
  var ENTITY_ORPHAN_R = FILE_R + 40;       // ADR-0047: orphan-ring radius (just past L3 files)
  var ENTITY_HEAT_TAU = 0.25;              // ADR-0047: heat threshold below which entities are slot-free
  var ENTITY_TOPN = 40;                    // ADR-0047: per-domain visible-entity floor (NOT a ceiling — OR-gated with TAU)
  var SECTOR_SETUP_HALF = Math.PI / 2.6;   // ~69°
  var SECTOR_SIDE_HALF  = Math.PI / 6.5;   // ~28°
  var SECTOR_SIDE_ANGLE = Math.PI * 0.72;  // ~130° from outward axis
  // Shells drawn as faint guide arcs behind the nodes (one per L1/L2/L3
  // per domain, plus disc/mem arcs). Level tokens consumed by the SVG
  // renderer to paint ring outlines + labels.
  var SHELL_LEVELS = [
    { key: 'L1', r: SETUP_R,     label: 'L1 setup' },
    { key: 'L2', r: TOOL_R,      label: 'L2 tools' },
    { key: 'L3', r: FILE_R,      label: 'L3 files' },
    { key: 'L6', r: SYM_R_OUTER, label: 'L6 symbols' },
  ];
  // Per-tool angles (local to the domain's outward axis), in radians.
  var TOOL_LOCAL_ANGLE = {
    Edit:  0,
    Write: -Math.PI / 12,
    Read:   Math.PI / 12,
    Grep:  -Math.PI /  6,
    Glob:   Math.PI /  6,
    Bash:  -Math.PI / 3.6,
    Task:   Math.PI / 3.6,
  };
  var EDGE_DISTANCE = {
    in_domain: 0,                        // satisfied by slot-anchoring, keep slack
    tool_used_file: 0,
    command_in_hub: 0,                   // bash_hub → command containment
    invoked_skill: 0,
    triggered_hook: 0,
    spawned_agent: 0,
    about_entity: 20,
    discussion_touched_file: 80,
    command_touched_file: 60,
    invoked_mcp: 90,
    defined_in: 22,                      // symbol sits close to its file
    calls: 24,                           // caller ↔ callee tight
    imports: 60,                         // short effective length — gain-bounded
    member_of: 10,                       // method ↔ class tight
    // Trace neural cloud: session sits out from the hub; a session's
    // events cluster tight around it; files sit a short hop from their
    // action so shared files visibly bridge multiple actions.
    has_session: 90,
    step: 34,
    next: 28,
    read: 30, edit: 30, write: 30, run: 30,
  };
  var EDGE_STRENGTH = {
    in_domain: 0.0,                      // layout is slot-anchored; links = slack
    tool_used_file: 0.0,
    command_in_hub: 0.0,                 // containment — zero extra pull
    invoked_skill: 0.0,
    triggered_hook: 0.0,
    spawned_agent: 0.0,
    about_entity: 0.2,
    discussion_touched_file: 0.08,
    command_touched_file: 0.08,
    invoked_mcp: 0.04,                   // long springs — MCPs bridge domains
    defined_in: 0.95,                    // dominant anchor
    calls: 0.12,                         // halved
    imports: 0.04,                       // 4.5× gain cut — no runaway resonance
    member_of: 0.60,
    // Trace: layout is SLOT-DRIVEN (per-session sectors in computeSlots).
    // Link strengths are ~0 so the slot force is uncontested — exactly how
    // the galaxy keeps structural edges (in_domain/tool_used_file) at 0 so
    // dots don't collapse into a ball. The edges still DRAW as lines.
    has_session: 0.0,
    step: 0.0,
    next: 0.0,
    read: 0.0, edit: 0.0, write: 0.0, run: 0.0,
  };
  var CROSS_DOMAIN_DISTANCE = 260;
  var CROSS_DOMAIN_STRENGTH = 0.02;

  function ensureD3(cb) {
    if (window.d3 && window.d3.forceSimulation) return cb();
    var existing = document.querySelector('script[data-cortex-d3]');
    if (existing) { existing.addEventListener('load', cb); return; }
    var s = document.createElement('script');
    s.src = D3_URL; s.async = true; s.defer = true;
    s.setAttribute('data-cortex-d3', '1');
    s.onload = cb;
    s.onerror = function () { console.error('[cortex] failed to load d3 from ' + D3_URL); };
    document.head.appendChild(s);
  }

  function renderWorkflowGraph(container, data) {
    if (!container) throw new Error('renderWorkflowGraph: container required');
    container.innerHTML = '';
    var handle = { destroy: function () {}, select: function () {},
                   data: data, append: function () { return { addedNodes: 0, addedEdges: 0 }; } };
    // Renderer selection. The D3 force-graph is the DEFAULT: it shows the
    // information that makes the view useful — node labels, edges, and the
    // methodology structure — and it is the renderer users actually read.
    // The deck.gl GPU scatterplot (``?viz=tilemap``) is an EXPLICIT opt-in
    // for raw-scale point-cloud inspection: it renders 1M+ pickable points
    // but carries no labels/edges, so it is never auto-selected — a slightly
    // truncated-but-legible force graph beats a complete-but-unreadable dot
    // field. The scatterplot module fetches its own data (/api/quadtree).
    // T2/T3 scale note: a genuinely huge corpus (≫200k) needs a hierarchical
    // multilevel force layout (coarsen via fractal_clustering, per-community
    // layout, persist by zoom band) so the force-graph stays both legible
    // AND complete — that is the real path to the full ecosystem view, not
    // the label-less scatterplot. Not implemented yet.
    var qs = (window.location && window.location.search) || '';
    var wantForce = qs.indexOf('viz=force') !== -1;
    var wantTilemap = qs.indexOf('viz=tilemap') !== -1;
    // Force-graph by default; scatterplot ONLY on explicit ?viz=tilemap
    // (and ?viz=force always wins if both are somehow present).
    var useTilemap = wantTilemap && !wantForce;
    if (useTilemap
        && window.JUG && typeof window.JUG.mountTilemap === 'function') {
      var p = window.JUG.mountTilemap(container);
      Promise.resolve(p).then(function (impl) {
        if (impl && impl.destroy) handle.destroy = impl.destroy;
      });
      return handle;
    }
    ensureD3(function () {
      var impl = mount(container, data || { nodes: [], edges: [] });
      handle.destroy = impl.destroy;
      handle.select = impl.select;
      handle.append = impl.append;
    });
    return handle;
  }

  // Deterministic string → [0,1) hash (FNV-1a). Used for stable per-node
  // jitter so re-mounts reproduce the identical layout (no re-shuffle).
  function _hash01(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return (h >>> 0) / 4294967296;
  }

  function mount(container, data) {
    var d3 = window.d3;
    var wfg = window.JUG._wfg;
    var nodes = (data.nodes || []).map(function (n) {
      var c = Object.assign({}, n);
      // Strip server-provided world coords. /api/graph/full ships x/y in
      // [-1,1] world space (domain hubs at 0,0), which clones EVERY node onto
      // a ~2px pile at the screen origin — the force sim then cannot fan 64k+
      // nodes out and collapses into an unreadable ball. v3.14.1 had NO server
      // coords: d3 seeds a phyllotaxis spread and slotForce fans nodes to
      // their deterministic radial-shell slots, which scaled cleanly to 338k+
      // nodes. The client slot layout owns positioning, not the server.
      delete c.x; delete c.y; delete c.vx; delete c.vy; delete c.fx; delete c.fy;
      return c;
    });
    // For very large graphs (>15k nodes) skip the simulation-visible
    // edges entirely — symbol→file/symbol→symbol edges number in the
    // tens of thousands and d3.forceLink on that many pairs freezes
    // the main thread. The slot layout already encodes containment
    // geometrically, so the visual edge of every symbol→file pair is
    // redundant. Keep only structural edges (domain hubs, tools,
    // files ↔ tools, discussions ↔ files, memories) for rendering.
    var HEAVY = nodes.length > 8000;
    var _nidSet = {};
    for (var _ni = 0; _ni < nodes.length; _ni++) _nidSet[nodes[_ni].id] = 1;
    // Keep AST edges in the simulation — they carry real semantic
    // meaning (symbol contained in file, symbol calls another symbol,
    // file imports symbol, method belongs to class). Layout should
    // REFLECT this connectivity, not randomize it. Only drop the
    // really dense symbol↔symbol edges (`calls`) under extreme load
    // to keep tick-rate manageable.
    var EXTREME = nodes.length > 25000;
    var renderedEdges;
    if (EXTREME) {
      renderedEdges = (data.edges || []).filter(function (e) {
        return e.kind !== 'calls';
      });
    } else {
      renderedEdges = data.edges || [];
    }
    // Drop dangling edges — endpoints must exist in the nodes array.
    renderedEdges = renderedEdges.filter(function (e) {
      var s = typeof e.source === 'object' ? e.source.id : e.source;
      var t = typeof e.target === 'object' ? e.target.id : e.target;
      return _nidSet[s] && _nidSet[t];
    });
    var edges = renderedEdges.map(function (e) {
      return Object.assign({}, e, {
        source: typeof e.source === 'object' ? e.source.id : e.source,
        target: typeof e.target === 'object' ? e.target.id : e.target,
      });
    });
    var width  = container.clientWidth  || window.innerWidth;
    var height = container.clientHeight || window.innerHeight;

    // Report the ACTUAL rendered topology so the HUD legend matches the canvas.
    // The legend used to read JUG.state.lastData, which accumulates every node
    // ever appended and is never pruned — so after a view switch it shows the
    // prior view's count (galaxy canvas, trace count) and over-reports edges the
    // renderer filtered (EXTREME `calls` + dangling). These are the exact node
    // and edge arrays this render draws, with the same kind breakdown polling.js
    // needs (entities = nodes − domain − memory − discussion).
    var _rc = { nodes: nodes.length, edges: edges.length,
                domain: 0, memory: 0, discussion: 0 };
    for (var _rci = 0; _rci < nodes.length; _rci++) {
      var _rk = nodes[_rci].kind || nodes[_rci].type || '';
      if (_rk === 'domain') _rc.domain++;
      else if (_rk === 'memory') _rc.memory++;
      else if (_rk === 'discussion') _rc.discussion++;
    }
    if (window.JUG) window.JUG.__wfgRendered = _rc;

    // Topology prep uses the FULL edge set (parent-file map needs
    // `defined_in` edges) but the simulation only sees the rendered set.
    var ctx = prepareTopology(nodes, data.edges || [], width, height);
    ctx.edges = edges;                // simulation edges (possibly filtered)
    ctx.KIND_RADIUS = KIND_RADIUS;
    ctx.KIND_COLOR  = KIND_COLOR;
    // HEAVY: pin symbols at their slot positions so d3 treats them as
    // immovable anchors (skip charge, skip link, skip collide for
    // pinned nodes). The layout is already deterministic via slotOf;
    // simulating 10k+ symbols adds no visual value, only CPU cost.
    // Seed symbols ALONG THE OUTWARD RAY from the domain hub through
    // their parent file, at a random distance past the file. This is
    // the starting configuration that lets symbols flow naturally
    // into the inter-domain gap space rather than orbiting the hub.
    for (var pi = 0; pi < nodes.length; pi++) {
      var pn = nodes[pi];
      if (pn.kind !== 'symbol') continue;
      var dId = ctx.domainOf[pn.id] || 'domain:__global__';
      var anc = ctx.anchors[dId] || ctx.anchors['domain:__global__'];
      var pfId = ctx.parentFile[pn.id];
      var fileSlot = pfId ? ctx.slotOf[pfId] : null;
      if (!anc) continue;
      var origin = fileSlot || anc;
      // Outward unit vector from domain anchor → origin.
      var dx = origin.x - anc.x, dy = origin.y - anc.y;
      var d = Math.hypot(dx, dy);
      var ox, oy;
      if (d < 1) {
        // Fallback: pseudo-random outward ray.
        var t = (pi * 0.37) % (Math.PI * 2);
        ox = Math.cos(t); oy = Math.sin(t);
      } else {
        ox = dx / d; oy = dy / d;
      }
      // DETERMINISTIC jitter keyed on the symbol id (not Math.random): a
      // re-mount (every live activity append re-runs mount()) must reproduce
      // the IDENTICAL layout, or the whole galaxy re-shuffles on every
      // streamed action. Two independent hashes → distance + angle.
      var _h1 = _hash01(pn.id), _h2 = _hash01(pn.id + '~a');
      var pastFile = 30 + _h1 * 120;  // 30..150 px past file
      var angJitter = (_h2 - 0.5) * 0.15;  // ±4° lateral spread
      var cs = Math.cos(angJitter), sn = Math.sin(angJitter);
      var rx = ox * cs - oy * sn;
      var ry = ox * sn + oy * cs;
      pn.x = origin.x + rx * pastFile;
      pn.y = origin.y + ry * pastFile;
    }
    // Node-detail panel is owned exclusively by #detail-panel
    // (detail_panel.js), driven via the graph:selectNode bus event the
    // renderers emit. No second side panel is built here.

    // Maxwell-damped config: see ADR-0047 for the full tuning rationale
    // (Thompson scaling audit on the Gap 10 N≈17k → N≈27k jump).
    //  * alphaDecay HEAVY: 0.028 → 0.018  (repulsive energy ∝ N²)
    //  * velocityDecay: 0.72 → 0.78       (ζ recovered to ~0.65)
    // Other force constants unchanged — slots from computeSlots carry
    // the positioning burden; physics just needs time to converge.
    var slotK    = HEAVY ? 1.2  : 0.85;
    var chargeEn = true;
    var collideI = HEAVY ? 2    : 3;
    var alphaDK  = HEAVY ? 0.018 : 0.022;
    var velDecay = 0.78;

    var sim = d3.forceSimulation(nodes)
      .alpha(1.0).alphaDecay(alphaDK).velocityDecay(velDecay)
      .force('link', d3.forceLink(edges).id(function (n) { return n.id; })
        .distance(linkDistance).strength(linkStrength))
      .force('slot',        slotForce(ctx, slotK))
      .force('interdomain', interDomainRepelForce(ctx, 0.08))
      .force('symmulti', symbolMultiCenterForce(ctx))
      .force('collide', d3.forceCollide()
        .radius(function (n) { return collisionRadius(n, ctx); })
        .strength(0.92).iterations(collideI));
    if (chargeEn) {
      // Local charge (distanceMax 180) so symbol-symbol repulsion
      // doesn't create long-range feedback with the multi-centroid
      // attraction; domains still repel each other via interdomain.
      sim.force('charge', d3.forceManyBody().strength(chargeStrength).distanceMax(180));
    }

    // LARGE galaxy: place every node AT its deterministic slot before the sim
    // runs, so it STARTS at the target radial-shell layout (the v3.14.1 look)
    // instead of cold-starting from a phyllotaxis spread and easing 65k nodes
    // in over ~3.5 s while redrawing all of them each tick. Then decay fast —
    // the sim only does a brief collide de-overlap and stops. Net effect: the
    // initial paint AND every live-activity re-mount render the structured
    // galaxy almost immediately, so the graph stays FLUID. Symbols keep the
    // outward-ray pre-seed assigned above; everything else snaps to slotOf.
    if (nodes.length > 15000) {
      for (var sp = 0; sp < nodes.length; sp++) {
        var spn = nodes[sp];
        var sps = ctx.slotOf[spn.id];
        if (sps) { spn.x = sps.x; spn.y = sps.y; }
      }
      sim.alphaDecay(0.12);  // ~50 ticks to a brief de-overlap, then halt
    }

    var useCanvas = nodes.length > CANVAS_THRESHOLD;
    var renderer = useCanvas
      ? wfg.mountCanvas(container, ctx, sim, width, height)
      : wfg.mountSVG(container, ctx, sim, width, height);
    // G2: register this instance as the repaint target for the surface
    // toggle (see the module-level 'cortex:surface-change' listener above).
    _activeRenderer = renderer;
    // Debug/verification hook (read-only reference, same pattern as the
    // existing __wfgRendered above) — lets an external probe read live node
    // positions (ctx.nodes[i].x/y) to confirm a surface-change repaint moved
    // zero pixels. No behavior depends on this; it is never read internally.
    if (window.JUG) window.JUG.__wfgCtx = ctx;

    function onResize() {
      var w = container.clientWidth || window.innerWidth;
      var h = container.clientHeight || window.innerHeight;
      renderer.resize(w, h);
      sim.alpha(0.3).restart();
    }
    window.addEventListener('resize', onResize);

    // Incremental append: mutate the live ``nodes`` and ``edges``
    // arrays (== ctx.nodes / ctx.edges) and gently restart the
    // simulation. Existing nodes stay where they are; new nodes are
    // seeded near their domain anchor and drift into place under
    // the force constraints. The canvas renderer reads ctx.nodes /
    // ctx.edges every frame, so new nodes appear on the next paint
    // without any DOM rebind. Edges to nodes that aren't yet in the
    // graph are skipped (caller must re-feed them on a later batch
    // when both endpoints exist).
    function append(newNodes, newEdges) {
      newNodes = newNodes || [];
      newEdges = newEdges || [];
      var addedN = 0, addedE = 0;
      // Canvas centre — guaranteed-numeric fallback chain. The video
      // recording showed memories piling on a Fibonacci spiral around
      // world (0, 0), which is the EXACT default that d3-force's
      // initializeNodes() places nodes with NaN x/y on. So somewhere
      // anc.x was NaN/undefined and d3 silently overrode our position.
      // Belt-and-braces: ctx.cx → ctx.width/2 → window.innerWidth/2 →
      // a hard-coded value, whichever first yields a finite positive
      // number.
      function _finite(v, fallback) {
        return (typeof v === 'number' && isFinite(v)) ? v : fallback;
      }
      var cx = _finite(ctx.cx, _finite(ctx.width / 2,
                _finite(window.innerWidth / 2, 600)));
      var cy = _finite(ctx.cy, _finite(ctx.height / 2,
                _finite(window.innerHeight / 2, 400)));
      // Build a list of ALL valid anchor coords once, so unknown-
      // domain memories pick a random EXISTING anchor instead of
      // falling back to (cx,cy) where they'd pile up on the same
      // pixel and trigger d3's NaN→spiral re-initialisation through
      // collision overflow.
      var anchorList = [];
      for (var dk in ctx.anchors) {
        var av = ctx.anchors[dk];
        if (av && isFinite(av.x) && isFinite(av.y)) anchorList.push(av);
      }
      if (anchorList.length === 0) anchorList.push({ x: cx, y: cy });

      for (var i = 0; i < newNodes.length; i++) {
        var n = newNodes[i];
        if (!n || n.id == null || ctx.byId[n.id]) continue;
        var n2 = Object.assign({}, n);
        var didCandidates = [
          n2.domain_id,
          n2.domain && ctx.byId[n2.domain] && ctx.byId[n2.domain].kind === 'domain' ? n2.domain : null,
          n2.domain ? 'domain:' + n2.domain : null,
          n2.domain ? 'domain:' + String(n2.domain).toLowerCase() : null,
        ];
        var did = null;
        var anc = null;
        for (var c = 0; c < didCandidates.length; c++) {
          var cand = didCandidates[c];
          if (cand && ctx.anchors[cand]
              && isFinite(ctx.anchors[cand].x)
              && isFinite(ctx.anchors[cand].y)) {
            did = cand;
            anc = ctx.anchors[cand];
            break;
          }
        }
        if (!anc) {
          // No specific domain match. Pick a random valid anchor so
          // memories with mismatched domain labels still cluster
          // somewhere meaningful (and definitely NOT at world origin).
          anc = anchorList[(Math.random() * anchorList.length) | 0];
          did = 'domain:__global__';
        }
        ctx.domainOf[n2.id] = did;

        var angle = Math.random() * Math.PI * 2;
        var rr = 30 + Math.random() * 100;
        var nx = anc.x + Math.cos(angle) * rr;
        var ny = anc.y + Math.sin(angle) * rr;
        // Final guard: if anything's NaN here it'd trigger d3's
        // spiral default. Replace with cx/cy + small jitter.
        if (!isFinite(nx) || !isFinite(ny)) {
          nx = cx + (Math.random() - 0.5) * 60;
          ny = cy + (Math.random() - 0.5) * 60;
        }
        n2.x = nx;
        n2.y = ny;
        nodes.push(n2);
        ctx.byId[n2.id] = n2;
        addedN++;
      }
      for (var j = 0; j < newEdges.length; j++) {
        var e = newEdges[j];
        if (!e) continue;
        var s = (e.source && e.source.id) || e.source;
        var t = (e.target && e.target.id) || e.target;
        if (!ctx.byId[s] || !ctx.byId[t]) continue;
        var e2 = Object.assign({}, e, { source: s, target: t });
        // Crosslink classification used by the link force.
        var sd = ctx.domainOf[s], td = ctx.domainOf[t];
        e2._crossDomain = !!(sd && td && sd !== td);
        edges.push(e2);
        addedE++;
      }
      if (addedN || addedE) {
        sim.nodes(nodes);
        sim.force('link').links(edges);
        // ── Reheat throttling ──
        // The bridge drains at 60 rAF/sec during streaming. Calling
        // sim.alpha(0.15).restart() per drain pegged alpha at 0.15
        // forever — alphaDecay (~0.022 / tick) can never pull alpha
        // down between drains, so forces fire continuously and the
        // whole graph drifts every frame. User saw this as
        // 'refreshing the whole graph every sec'.
        //
        // Two-tier bump based on elapsed time since the previous
        // reheat:
        //   < 250 ms  → α = 0.03  (gentle nudge; new nodes drift to
        //              their links, existing nodes barely shift)
        //   ≥ 250 ms  → α = 0.15  (settle a fresh wave)
        // Only bump if the current alpha is BELOW the target — so a
        // long ongoing settle from a previous wave isn't stomped on.
        var now = (window.performance && performance.now()) || Date.now();
        var sinceLast = now - (sim._lastReheatAt || 0);
        var bump = sinceLast < 250 ? 0.03 : 0.15;
        if (sim.alpha() < bump) sim.alpha(bump);
        sim.restart();
        sim._lastReheatAt = now;
        if (sim._idleTimer) clearTimeout(sim._idleTimer);
        sim._idleTimer = setTimeout(function () {
          sim._idleTimer = null;
          sim.stop();
        }, 3000);
      }
      return { addedNodes: addedN, addedEdges: addedE,
               totalNodes: nodes.length, totalEdges: edges.length };
    }

    // Pin every node at its current position once the seed's force
    // simulation has settled. New nodes added later via handle.append
    // stay unpinned so they can drift to a sensible position under
    // force; the already-settled nodes are locked so the incoming
    // mass (memories at 100 k+, symbols at 600 k+) can't push them
    // off-screen via manyBody repulsion. That was the user-visible
    // 'nodes already there should not be removed' bug.
    //
    // Pinning fires when alpha first drops below 0.08 (visually
    // settled — see Maxwell-damped ADR-0047) OR after 3.5 s wall-
    // clock, whichever comes first. The alpha condition cannot use
    // sim.alphaMin() because the throttled appends keep nudging
    // alpha above the floor; we need a higher threshold that's
    // reached during the seed's natural decay.
    var _pinStartedAt = (window.performance && performance.now()) || Date.now();
    function _pinSettledNodes() {
      if (sim._pinDone) return;
      var now = (window.performance && performance.now()) || Date.now();
      var elapsed = now - _pinStartedAt;
      if (sim.alpha() > 0.08 && elapsed < 3500) {
        setTimeout(_pinSettledNodes, 200);
        return;
      }
      sim._pinDone = true;
      var pinned = 0;
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (n.fx == null) { n.fx = n.x; n.fy = n.y; pinned++; }
      }
      console.log('[wfg] seed layout settled at α=' + sim.alpha().toFixed(3)
                  + ' — pinned ' + pinned + ' nodes');
    }
    setTimeout(_pinSettledNodes, 600);

    var handle = {
      destroy: function () {
        window.removeEventListener('resize', onResize);
        sim.stop();
        renderer.destroy();
        if (_activeRenderer === renderer) _activeRenderer = null;
      },
      select: function (id) { renderer.selectId(id); },
      reflow: function () { onResize(); },
      applyFilter: function (pred) {
        if (typeof renderer.applyFilter === 'function') renderer.applyFilter(pred, ctx);
      },
      append: append,
    };
    // Expose a stable hook so the filter-bar driver can reach us.
    window.JUG.wfgApplyFilter = function (pred) { handle.applyFilter(pred); };
    return handle;
  }

  // ── Topology: Fibonacci-spiral domain anchors; domainOf; primary tool_hub;
  //    degree; adjacency; per-node slot (radial hierarchy).
  function prepareTopology(nodes, edges, width, height) {
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var domains = nodes.filter(function (n) { return n.kind === 'domain'; });
    // Trace graphs (domain → session → chain → file) are a tree, not the
    // galaxy's L1–L6 radial shells. Detect via the active view OR the
    // data schema OR trace-only kinds — on a fresh load only domains are
    // present (no session/action yet), so kind-sniffing alone would
    // wrongly draw the galaxy rings on the default trace screen.
    var _view = (window.JUG && JUG.state && JUG.state.activeView) || '';
    var _schema = (window.JUG && JUG.state && JUG.state.lastData &&
                   JUG.state.lastData.meta && JUG.state.lastData.meta.schema) || '';
    var isTrace = _view === 'trace' || _schema === 'trace.v1' || nodes.some(function (n) {
      var k = n.kind || n.type;
      return k === 'session' || k === 'action' || k === 'prompt';
    });

    var cx = width / 2, cy = height / 2;
    // Each domain's outer shell is roughly FILE_R + cushion; Fibonacci
    // spiral average spacing is R·√(π/N). Pick baseR so the spacing
    // exceeds the shell diameter — rings never collide.
    var N = Math.max(domains.length, 1);
    var shellDiameter = 2 * FILE_R + 60;
    var baseR = Math.max(
      Math.min(width, height) * 0.42,
      shellDiameter * Math.sqrt(N / Math.PI) * 0.65,
    );
    var phi = Math.PI * (3 - Math.sqrt(5));  // golden angle
    var anchors = {};
    domains.forEach(function (d, i) {
      var r = baseR * Math.sqrt((i + 0.5) / N);
      var theta = i * phi;
      anchors[d.id] = { x: cx + r * Math.cos(theta), y: cy + r * Math.sin(theta) };
      d.x = anchors[d.id].x; d.y = anchors[d.id].y;
      d.fx = d.x; d.fy = d.y;                // pin domain anchors — L1/L2/L3 rings orbit them.
    });

    var domainOf = {};
    nodes.forEach(function (n) {
      if (n.kind === 'domain') { domainOf[n.id] = n.id; return; }
      if (n.domain && byId[n.domain] && byId[n.domain].kind === 'domain') domainOf[n.id] = n.domain;
      else if (n.domain_id && byId[n.domain_id] && byId[n.domain_id].kind === 'domain') {
        domainOf[n.id] = n.domain_id;
      }
    });
    edges.forEach(function (e) {
      if (e.kind !== 'in_domain') return;
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      if (s.kind === 'domain' && !domainOf[t.id]) domainOf[t.id] = s.id;
      if (t.kind === 'domain' && !domainOf[s.id]) domainOf[s.id] = t.id;
    });
    // Trace edges carry the domain DOWN the chain: domain→session
    // (has_session), session→event + event→event (step / next), and
    // action→file (read/edit/write/run). Iterate to a fixed point so a
    // file reached only via action→file still resolves to its domain.
    // `discusses`/`remembers` attach discussion + memory BRANCH nodes to the
    // spine (they hang off a prompt/action without advancing it); they MUST be
    // here too, or those branch nodes never inherit a domain, fail the
    // anchored-domain gate in computeSlots, and pile at the origin as a fan.
    var _traceEdgeKinds = { has_session: 1, step: 1, next: 1,
      read: 1, edit: 1, write: 1, run: 1, discusses: 1, remembers: 1 };
    for (var _pass = 0; _pass < 6; _pass++) {
      var _changed = false;
      for (var _ei = 0; _ei < edges.length; _ei++) {
        var te = edges[_ei];
        if (!_traceEdgeKinds[te.kind]) continue;
        var ss = typeof te.source === 'object' ? te.source.id : te.source;
        var tt = typeof te.target === 'object' ? te.target.id : te.target;
        if (domainOf[ss] && !domainOf[tt]) { domainOf[tt] = domainOf[ss]; _changed = true; }
        else if (domainOf[tt] && !domainOf[ss]) { domainOf[ss] = domainOf[tt]; _changed = true; }
      }
      if (!_changed) break;
    }

    // Parent file per symbol — drives the symbol-petal clustering.
    // Prefer `defined_in` edges; fall back to `path` string match.
    var parentFile = {};
    edges.forEach(function (e) {
      if (e.kind !== 'defined_in') return;
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      if (s.kind === 'symbol' && t.kind === 'file') parentFile[s.id] = t.id;
      else if (t.kind === 'symbol' && s.kind === 'file') parentFile[t.id] = s.id;
    });
    var filesByPath = {};
    nodes.forEach(function (n) {
      if (n.kind === 'file' && n.path) filesByPath[n.path] = n.id;
    });
    nodes.forEach(function (n) {
      if (n.kind !== 'symbol' || parentFile[n.id]) return;
      if (n.path && filesByPath[n.path]) parentFile[n.id] = filesByPath[n.path];
    });
    // Every symbol MUST have a domain or the containment force can't
    // constrain it. Priority:
    //   1. Parent file's domain (derived from `defined_in` edge)
    //   2. node.domain_id / node.domain (server already tags each
    //      symbol with its project's domain id)
    //   3. GLOBAL fallback if somehow neither resolves.
    nodes.forEach(function (n) {
      if (n.kind !== 'symbol') return;
      var pf = parentFile[n.id];
      if (pf && domainOf[pf]) { domainOf[n.id] = domainOf[pf]; return; }
      var did = n.domain_id || (n.domain ? 'domain:' + n.domain : '');
      if (did && byId[did]) { domainOf[n.id] = did; return; }
      if (!domainOf[n.id]) domainOf[n.id] = 'domain:__global__';
    });

    var primaryHub = {}, hubWeight = {};
    edges.forEach(function (e) {
      if (e.kind !== 'tool_used_file') return;
      var s = byId[e.source], t = byId[e.target];
      if (!s || !t) return;
      var hub = s.kind === 'tool_hub' ? s : (t.kind === 'tool_hub' ? t : null);
      var f = s.kind === 'file' ? s : (t.kind === 'file' ? t : null);
      if (!hub || !f) return;
      if (domainOf[hub.id] && domainOf[hub.id] === domainOf[f.id]) {
        var w = e.weight != null ? e.weight : 1;
        if (!(f.id in hubWeight) || w > hubWeight[f.id]) { hubWeight[f.id] = w; primaryHub[f.id] = hub.id; }
      }
    });

    var degree = {}, adj = {};
    edges.forEach(function (e) {
      degree[e.source] = (degree[e.source] || 0) + 1;
      degree[e.target] = (degree[e.target] || 0) + 1;
      var sd = domainOf[e.source], td = domainOf[e.target];
      e._crossDomain = !!(sd && td && sd !== td);
      if (!adj[e.source]) adj[e.source] = {};
      if (!adj[e.target]) adj[e.target] = {};
      adj[e.source][e.target] = true; adj[e.target][e.source] = true;
    });

    var slotOf = computeSlots(nodes, domains, anchors, domainOf, primaryHub, parentFile, cx, cy, edges, byId, isTrace);

    return { byId: byId, nodes: nodes, edges: edges, domains: domains,
      anchors: anchors, domainOf: domainOf, primaryHub: primaryHub,
      parentFile: parentFile,
      degree: degree, adj: adj, slotOf: slotOf,
      isTrace: isTrace,
      // Trace has no L1–L6 shells or discussion/memory side lanes —
      // suppress them so the canvas draws a clean tree.
      shells: isTrace ? [] : SHELL_LEVELS,
      sideShells: isTrace ? [] : [
        { key: 'L4', r: DISC_R, label: 'L4 discussions', angle: SECTOR_SIDE_ANGLE },
        { key: 'L5', r: MEM_R,  label: 'L5 memories',    angle: -SECTOR_SIDE_ANGLE },
      ], cx: cx, cy: cy, baseR: baseR,
      width: width, height: height };
  }

  // Assign each non-domain node a target (x,y) slot expressing the hierarchy:
  //   domain → L1 (setup) → L2 (tools) → L3 (files);  discussions lane;  memories lane.
  function computeSlots(nodes, domains, anchors, domainOf, primaryHub, parentFile, cx, cy, edges, byId, isTrace) {
    // Group non-domain nodes by (domain, kind).
    var groups = {};
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.kind === 'domain') continue;
      var dom = domainOf[n.id];
      if (!dom || !anchors[dom]) continue;
      if (!groups[dom]) groups[dom] = {};
      if (!groups[dom][n.kind]) groups[dom][n.kind] = [];
      groups[dom][n.kind].push(n);
    }
    var slotOf = {};
    var setupKinds = ['skill', 'hook', 'command', 'agent'];

    // ── Entity → linked-memory index (Gap 10 / Kekulé positioning).
    //    One pass over the about_entity edge set builds, per entity,
    //    the list of MEMORY node ids it sits on. Memories without slots
    //    yet (slotOf[memId] absent at this point — memories are slotted
    //    later in the per-domain loop) are resolved lazily in the
    //    second pass below by stashing entity centroids for deferred
    //    computation after memory slots exist.
    var entityMemLinks = {};
    if (edges && edges.length) {
      for (var ei = 0; ei < edges.length; ei++) {
        var e = edges[ei];
        if (e.kind !== 'about_entity') continue;
        var sId = typeof e.source === 'object' ? e.source.id : e.source;
        var tId = typeof e.target === 'object' ? e.target.id : e.target;
        var sKind = byId && byId[sId] ? byId[sId].kind : null;
        var tKind = byId && byId[tId] ? byId[tId].kind : null;
        var memId, entId;
        if (sKind === 'memory' && tKind === 'entity') { memId = sId; entId = tId; }
        else if (tKind === 'memory' && sKind === 'entity') { memId = tId; entId = sId; }
        else continue;
        if (!entityMemLinks[entId]) entityMemLinks[entId] = [];
        entityMemLinks[entId].push(memId);
      }
    }

    // ── Trace file → session map ──────────────────────────────────────────
    // Trace file nodes carry no session_id (session_trace._file_node emits only
    // id/kind/label/path), so they can't self-identify a session disk. But each
    // file is linked to the ACTION that touched it via a read/edit/write/run
    // verb edge, and actions DO carry session_id. Resolve each file to the
    // session of the first action that touched it, so the bySession grouping
    // below can pack the file into that session's disk (outer band) instead of
    // flinging it to the orphan ring. Galaxy files use tool_used_file edges (not
    // these verbs) so this map stays empty for them — they fall through to L3.
    var fileSession = {};
    if (edges && edges.length) {
      var _verbKinds = { read: 1, edit: 1, write: 1, run: 1 };
      for (var vfi = 0; vfi < edges.length; vfi++) {
        var ve = edges[vfi];
        if (!_verbKinds[ve.kind]) continue;
        var vaId = typeof ve.source === 'object' ? ve.source.id : ve.source;
        var vbId = typeof ve.target === 'object' ? ve.target.id : ve.target;
        var vaN = byId[vaId], vbN = byId[vbId];
        var vAct = vaN && vaN.kind === 'action' ? vaN : (vbN && vbN.kind === 'action' ? vbN : null);
        var vFil = vaN && vaN.kind === 'file' ? vaN : (vbN && vbN.kind === 'file' ? vbN : null);
        if (!vAct || !vFil || !vAct.session_id) continue;
        if (!fileSession[vFil.id]) fileSession[vFil.id] = 'session:' + vAct.session_id;
      }
    }

    Object.keys(groups).forEach(function (domId) {
      var a = anchors[domId];
      var outward = Math.atan2(a.y - cy, a.x - cx);  // radially outward from graph center
      // For domains near the center the outward axis is unstable — bias upward.
      if (Math.hypot(a.x - cx, a.y - cy) < 5) outward = -Math.PI / 2;
      var g = groups[domId];

      // ── Trace layout: domain → per-session COMPACT SUB-CLUSTERS ──
      // Each session becomes a tight disk of its own work, placed on a
      // ring around the domain hub. ALL of a session's events (prompt /
      // action / file) pack around that session's sub-center in a
      // phyllotaxis (sunflower) spiral — a dense, even, NON-overlapping
      // disk, exactly the compactness the galaxy got from orbiting a hub.
      // No marching-outward rows: cluster radius grows with sqrt(count),
      // so even a 600-event session stays a bounded blob you can read as
      // "this session's work", clearly separated from other sessions.
      var sessions = g.session || [];
      // Group every event under its CLUSTER KEY. Trace events key off
      // session_id; other lenses (e.g. the wiki cross-lens graph) tag
      // each node with a generic `cluster` (scope name, or "_xlens"),
      // so one disk forms per scope with ZERO trace-path changes —
      // trace nodes have no `cluster` and fall back to session:<id>.
      var bySession = {};   // clusterKey -> [nodes]
      // Kinds eligible for a session/lens disk. The guard below admits a node
      // ONLY if it carries a `cluster` (cross-lens) or `session_id` (trace) —
      // galaxy entity/symbol/file/memory have neither, so they're skipped here
      // and placed by their dedicated kind-lane (L3/L5/memories) downstream.
      //
      // ``discussion`` is TRACE-ONLY: trace discussions (assistant turns) carry
      // a session_id and MUST pack into their session disk (otherwise they fall
      // to the origin as a radiating fan — the "trace breaks as we add nodes"
      // report). But galaxy discussion-SUMMARY nodes ALSO carry a session_id,
      // and in the galaxy they belong in the discussions LANE, not a disklet —
      // so only admit discussions when this is a trace layout.
      var clusterKinds = ['prompt', 'action', 'file',
                          'wiki_page', 'entity', 'symbol', 'memory', 'prd'];
      if (isTrace) clusterKinds.push('discussion');
      clusterKinds.forEach(function (kind) {
        (g[kind] || []).forEach(function (n) {
          // cluster (cross-lens) → session_id (trace prompt/action/discussion/
          // memory) → fileSession (trace files, resolved via their action's
          // verb edge). A node matching none belongs to no disk (galaxy
          // entity/symbol/file) and is left to its kind-lane downstream.
          var sid = n.cluster
            || (n.session_id ? 'session:' + n.session_id : null)
            || fileSession[n.id];
          if (!sid) return;
          (bySession[sid] = bySession[sid] || []).push(n);
        });
      });
      // ── Per-session disks, packed by ACTUAL radius so they never merge ──
      // The galaxy stays readable at 29k nodes because each group is one
      // coherent disk AND the disks are separated by hard gaps. Slots are
      // absolute (slotForce pulls each node to its computed x,y), so the
      // separation must be solved HERE — a runtime disk-collision would
      // fight slotForce and reproduce the blob. We size every session's
      // disk by its event count, then walk a ring placing each disk at a
      // cumulative angle equal to its own angular width (+gap), growing the
      // ring radius until the whole run fits in 2π. Even, non-overlapping,
      // exactly the galaxy's "tight disks with gaps".
      var DOT = 13;                       // ~node spacing in the spiral
      var GOLDEN = Math.PI * (3 - Math.sqrt(5));
      var GAP = 16;                       // hard gap between adjacent disks
      function clusterRadius(count) { return DOT * Math.sqrt(Math.max(count, 1)) + 14; }

      // Build one cluster per session (union of session nodes + any events
      // whose session node hasn't loaded yet), each with a type-major item
      // order so the phyllotaxis lays prompts inner, actions mid, files
      // outer — kind reads as concentric bands within one clean disk.
      var KIND_BAND = { prompt: 0, action: 1, file: 2 };
      var sessNodeBySid = {};
      sessions.forEach(function (s) { sessNodeBySid['session:' + (s.session_id || '')] = s; });
      var clusterSids = {};
      sessions.forEach(function (s) { clusterSids['session:' + (s.session_id || '')] = 1; });
      Object.keys(bySession).forEach(function (sid) { clusterSids[sid] = 1; });
      var clusters = Object.keys(clusterSids).map(function (sid) {
        var items = (bySession[sid] || []).slice();
        items.sort(function (p, q) {
          var pk = KIND_BAND[p.kind] != null ? KIND_BAND[p.kind] : 3;
          var qk = KIND_BAND[q.kind] != null ? KIND_BAND[q.kind] : 3;
          if (pk !== qk) return pk - qk;
          var ps = (p.seq != null ? p.seq : 1e9), qs = (q.seq != null ? q.seq : 1e9);
          return ps - qs;
        });
        return { node: sessNodeBySid[sid] || null, items: items, rad: clusterRadius(items.length) };
      });
      // ── Collapse UNEXPANDED session hubs into ONE compact blob ──────────────
      // A domain holds dozens of sessions but only a few are expanded (chain
      // loaded). An unexpanded session is an items-less hub of identical tiny
      // radius; left as individual disks they all share the same ring and, being
      // small + numerous, smear into a single-file circle at a large radius (the
      // "circle mapping" that appears the moment a session is selected and the
      // re-mount re-runs this layout). Instead gather every empty hub into one
      // dense phyllotaxis blob — a single cluster whose ITEMS are the hub nodes —
      // so they pack as a tight satellite instead of a ring. Expanded sessions
      // keep their own content disks; selecting one no longer reshuffles the rest
      // into a circle.
      var contentClusters = [];
      var emptyHubNodes = [];
      clusters.forEach(function (c) {
        if (c.items.length === 0 && c.node) emptyHubNodes.push(c.node);
        else contentClusters.push(c);
      });
      if (emptyHubNodes.length) {
        contentClusters.push({
          node: null, items: emptyHubNodes, rad: clusterRadius(emptyHubNodes.length),
        });
      }
      clusters = contentClusters;
      // Largest disks first → each ring's thickness is set by its biggest disk,
      // and big disks land in the inner rings (stable, dense packing).
      clusters.sort(function (a2, b2) { return b2.rad - a2.rad; });

      // ── GRAVITY-PACK session disks tight around the domain hub ──────────────
      // Concentric rings (the prior fix) placed disks at INCREASING radii, so a
      // domain with many expanded sessions pushed the bulk into outer rings — a
      // big circle with a hollow centre, the domain hub stranded in the middle
      // (repeated report: "circle placement, should gravitate near the domain").
      // Instead pack like gravity: each disk (largest first) takes the position
      // CLOSEST to the hub that overlaps neither an already-placed disk nor the
      // hub's clearance. The cluster fills OUTWARD from the domain anchor — dense,
      // centred ON the domain, no ring and no hollow centre. The galaxy never
      // reaches this path (its `clusters` are empty), so this is trace/lens-only.
      var HUB_CLEARANCE = 48;                    // px, kept clear around the domain hub
      function placeDisk(c, scx, scy) {
        if (c.node) slotOf[c.node.id] = { x: scx, y: scy };  // session hub = disk center
        c.items.forEach(function (n, k) {
          // phyllotaxis: r = c·√k, angle = k·goldenAngle → even packing;
          // type-major order above turns k-bands into kind-bands.
          var rr = DOT * Math.sqrt(k + 0.5);
          var aa = (k + 1) * GOLDEN;
          slotOf[n.id] = { x: scx + rr * Math.cos(aa), y: scy + rr * Math.sin(aa) };
        });
      }
      // The hub itself is a central obstacle so disks ring it without burying it.
      var placed = [{ x: a.x, y: a.y, r: HUB_CLEARANCE }];
      function gravitySlot(rad) {
        // Archimedean spiral out from the anchor; the spiral grows ~one disk
        // width per turn and is sampled at a near-uniform arc step. The first
        // sample that clears every placed disk (by GAP) is the closest free spot.
        var theta = 0;
        for (var iter = 0; iter < 20000; iter++) {
          var rr = (rad + GAP) * theta / (2 * Math.PI);
          var x = a.x + rr * Math.cos(theta);
          var y = a.y + rr * Math.sin(theta);
          var ok = true;
          for (var p = 0; p < placed.length; p++) {
            var dx = x - placed[p].x, dy = y - placed[p].y;
            var minD = rad + placed[p].r + GAP;
            if (dx * dx + dy * dy < minD * minD) { ok = false; break; }
          }
          if (ok) return { x: x, y: y };
          // arc-length step ≈ 0.6·rad, clamped so we always sample ≥12 pts/turn.
          theta += Math.min(0.5, Math.max(0.08, (rad * 0.6) / Math.max(rr, 1)));
        }
        return { x: a.x, y: a.y };
      }
      var outerRingR = HUB_CLEARANCE;            // outermost extent — used by the orphan fallback
      clusters.forEach(function (c) {
        var pos = gravitySlot(c.rad);
        placed.push({ x: pos.x, y: pos.y, r: c.rad });
        placeDisk(c, pos.x, pos.y);
        var reach = Math.hypot(pos.x - a.x, pos.y - a.y) + c.rad;
        if (reach > outerRingR) outerRingR = reach;
      });

      // L2: tool_hubs at fixed per-tool angles within the setup sector.
      var hubAngle = {};
      (g.tool_hub || []).forEach(function (h) {
        var local = TOOL_LOCAL_ANGLE[h.tool];
        if (local == null) local = 0;
        var t = outward + local;
        hubAngle[h.id] = t;
        slotOf[h.id] = { x: a.x + TOOL_R * Math.cos(t),
                         y: a.y + TOOL_R * Math.sin(t) };
      });

      // L3: files orbit their primary tool_hub (same angle + small jitter).
      // bySession placement wins: a file already slotted into a session/lens
      // disk above is skipped here (mirrors the orphan-files guard) so the
      // galaxy hub-orbit lane never clobbers a trace file's session-disk slot.
      var filesByHub = {};
      (g.file || []).forEach(function (f) {
        if (slotOf[f.id]) return;
        var hid = primaryHub[f.id];
        if (!filesByHub[hid]) filesByHub[hid] = [];
        filesByHub[hid].push(f);
      });
      Object.keys(filesByHub).forEach(function (hid) {
        var theta = hubAngle[hid];
        if (theta == null) theta = outward;  // hub in another domain (cross-domain file)
        var arr = filesByHub[hid];
        var arc = Math.min(0.35, 0.08 + arr.length * 0.015);
        arr.forEach(function (f, i) {
          var t = theta + ((i + 0.5) / arr.length - 0.5) * arc;
          var r = FILE_R + ((i % 3) - 1) * 4;  // radial stagger to reduce overlap
          slotOf[f.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      });

      // True last-resort: a file in NO session disk (bySession) and NO tool-hub
      // orbit (L3) — e.g. a trace file whose action lacked a session_id. Runs
      // AFTER L3 so it never pre-empts the galaxy hub-orbit lane (that ordering
      // bug flung every galaxy file to this ring). Skips anything already
      // placed, so it only catches genuine orphans.
      var orphanI = 0;
      (g.file || []).forEach(function (n) {
        if (slotOf[n.id]) return;
        var t = outward + (orphanI++) * GOLDEN;
        var r = outerRingR + 30 + (orphanI % 5) * 12;
        slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
      });

      // L1: skills, hooks, commands, agents — fanned inner ring.
      var setup = [];
      setupKinds.forEach(function (k) { (g[k] || []).forEach(function (x) { setup.push(x); }); });
      if (setup.length) {
        var arc1 = SECTOR_SETUP_HALF * 2;
        setup.forEach(function (n, i) {
          var t = outward + ((i + 0.5) / setup.length - 0.5) * arc1;
          var r = SETUP_R + (i % 2) * 8;
          slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      }

      // Discussions lane (opposite side from setup, one side). Galaxy-only in
      // practice: trace discussions are pre-placed into their session disk
      // above (clusterKinds includes 'discussion' when isTrace), so the guard
      // makes this lane a no-op for them — bySession placement wins.
      var disc = g.discussion || [];
      if (disc.length) {
        var center = outward + SECTOR_SIDE_ANGLE;
        var arc2 = SECTOR_SIDE_HALF * 2 + Math.min(Math.PI / 3, disc.length * 0.04);
        disc.forEach(function (n, i) {
          if (slotOf[n.id]) return;
          var t = center + ((i + 0.5) / disc.length - 0.5) * arc2;
          var r = DISC_R + (i % 3) * 6;
          slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      }

      // Memories lane (opposite side from setup, other side). Like discussions:
      // trace memory nodes carry a session_id and are pre-placed into their
      // session disk above, so the guard skips them here; galaxy memories have
      // no session_id and are placed by this lane as before.
      var mem = g.memory || [];
      if (mem.length) {
        var center2 = outward - SECTOR_SIDE_ANGLE;
        var arc3 = SECTOR_SIDE_HALF * 2 + Math.min(Math.PI / 2.5, mem.length * 0.03);
        mem.forEach(function (n, i) {
          if (slotOf[n.id]) return;
          var t = center2 + ((i + 0.5) / mem.length - 0.5) * arc3;
          var r = MEM_R + (i % 4) * 8;
          slotOf[n.id] = { x: a.x + r * Math.cos(t), y: a.y + r * Math.sin(t) };
        });
      }

      // MCPs sit INSIDE the domain (between the center of the graph and the
      // domain anchor), so their long INVOKED_MCP edges fan visibly between
      // domains that share the MCP.
      (g.mcp || []).forEach(function (n, i) {
        var t = outward + Math.PI;  // inward
        var jitter = (i - (g.mcp.length - 1) / 2) * 0.25;
        slotOf[n.id] = { x: a.x + MCP_R * Math.cos(t + jitter),
                         y: a.y + MCP_R * Math.sin(t + jitter) };
      });

      // L5+E entities: see ADR-0047. Slot = heat-weighted memory
      // centroid blended 15% to domain hub (Kekulé valence analysis).
      // Heat gate is OR-semantic by design: entity is kept if within
      // top-N OR above heat threshold. `ENTITY_TOPN` therefore acts as
      // a per-domain *floor* on visibility (cold domains still show
      // their top-40), not a ceiling on hot ones.
      var ents = (g.entity || []).slice();
      if (ents.length) {
        ents.sort(function (a, b) {
          return (b.heat != null ? b.heat : 0) - (a.heat != null ? a.heat : 0);
        });
        var kept = ents.filter(function (en, idx) {
          return idx < ENTITY_TOPN || (en.heat != null && en.heat >= ENTITY_HEAT_TAU);
        });
        var hubX = a.x, hubY = a.y;
        kept.forEach(function (en) {
          var memIds = entityMemLinks[en.id] || [];
          var cx2 = 0, cy2 = 0, wTotal = 0;
          for (var mi = 0; mi < memIds.length; mi++) {
            var mSlot = slotOf[memIds[mi]];
            if (!mSlot) continue;
            // Heat of the memory node itself (hotter memories pull harder).
            var mNode = byId ? byId[memIds[mi]] : null;
            var w = mNode && mNode.heat != null ? Math.max(0.05, mNode.heat) : 0.5;
            cx2 += mSlot.x * w; cy2 += mSlot.y * w; wTotal += w;
          }
          if (wTotal > 0) {
            // Kekulé centroid blended 15% toward the domain hub.
            var mcx = cx2 / wTotal, mcy = cy2 / wTotal;
            slotOf[en.id] = {
              x: (1 - ENTITY_DOMAIN_BLEND) * mcx + ENTITY_DOMAIN_BLEND * hubX,
              y: (1 - ENTITY_DOMAIN_BLEND) * mcy + ENTITY_DOMAIN_BLEND * hubY,
            };
          } else {
            // Orphan: hash-deterministic ring around the domain hub so
            // the same entity lands in the same place across runs.
            var h = 0;
            for (var ci = 0; ci < en.id.length; ci++) {
              h = ((h << 5) - h + en.id.charCodeAt(ci)) | 0;
            }
            var theta = (Math.abs(h) % 1000) / 1000 * Math.PI * 2;
            slotOf[en.id] = {
              x: hubX + ENTITY_ORPHAN_R * Math.cos(theta),
              y: hubY + ENTITY_ORPHAN_R * Math.sin(theta),
            };
          }
        });
        // Entities below the heat gate are intentionally slot-free —
        // they'll drift to default positions and can be filter-hidden
        // via the existing "kind:entity" toggle.
      }

      // L6 symbols intentionally have NO slot — their final position
      // is determined by the codebase-analysis edges the force
      // simulation operates on (`defined_in` pulls toward the parent
      // file, `calls` pulls toward callers/callees, `imports` bridges
      // files, `member_of` clusters methods with their class). The
      // initial x/y seeding happens in mount() from the parent file's
      // position, then the force simulation does the layout work.
    });
    return slotOf;
  }

  // ── Force helpers (pure closures) ──
  function linkDistance(e) {
    if (e._crossDomain) return CROSS_DOMAIN_DISTANCE;
    return EDGE_DISTANCE[e.kind] != null ? EDGE_DISTANCE[e.kind] : 30;
  }
  function linkStrength(e) {
    if (e._crossDomain) return CROSS_DOMAIN_STRENGTH;
    var s = EDGE_STRENGTH[e.kind] != null ? EDGE_STRENGTH[e.kind] : 0.4;
    return s * (e.weight != null ? Math.min(1, 0.3 + e.weight * 0.7) : 1);
  }
  function chargeStrength(n) {
    if (n.kind === 'domain')   return -620;
    if (n.kind === 'tool_hub') return -140;
    if (n.kind === 'agent' || n.kind === 'skill') return -80;
    // Symbols: enough mutual repulsion to spread laterally in the
    // interlock space (Maxwell: -22, local distanceMax).
    if (n.kind === 'symbol')   return -22;
    return -28;
  }
  function slotForce(ctx, k) {
    return function (alpha) {
      var s = k * alpha;
      for (var i = 0; i < ctx.nodes.length; i++) {
        var n = ctx.nodes[i];
        if (n.kind === 'domain') continue;
        var slot = ctx.slotOf[n.id];
        if (!slot) continue;
        n.vx += (slot.x - n.x) * s;
        n.vy += (slot.y - n.y) * s;
      }
    };
  }
  // Multi-centroid attraction (Alexander's deep interlock): a symbol
  // is pulled by EVERY domain it touches via its edges, weighted 1/N
  // where N = number of distinct domains touched. Symbols connected
  // only to their home domain sit near it; cross-domain symbols
  // literally fall into the interlock space between two or more hubs.
  // No containment — position emerges from connectivity alone.
  function symbolMultiCenterForce(ctx) {
    // Precompute each symbol's domain centroid list ONCE.
    var symDomains = {};
    for (var i = 0; i < ctx.nodes.length; i++) {
      var n = ctx.nodes[i];
      if (n.kind !== 'symbol') continue;
      var set = {};
      // Home domain (from parent file or node's own domain_id).
      var home = ctx.domainOf[n.id];
      if (home && ctx.anchors[home]) set[home] = 1;
      symDomains[n.id] = set;
    }
    // Walk every AST edge; for each symbol endpoint, add the OTHER
    // endpoint's domain to its centroid set.
    ctx.edges.forEach(function (e) {
      var k = e.kind;
      if (k !== 'defined_in' && k !== 'calls' &&
          k !== 'imports' && k !== 'member_of') return;
      var sId = typeof e.source === 'object' ? e.source.id : e.source;
      var tId = typeof e.target === 'object' ? e.target.id : e.target;
      var sN = ctx.byId[sId], tN = ctx.byId[tId];
      if (!sN || !tN) return;
      if (sN.kind === 'symbol' && ctx.domainOf[tId] && ctx.anchors[ctx.domainOf[tId]]) {
        symDomains[sId] = symDomains[sId] || {};
        symDomains[sId][ctx.domainOf[tId]] = 1;
      }
      if (tN.kind === 'symbol' && ctx.domainOf[sId] && ctx.anchors[ctx.domainOf[sId]]) {
        symDomains[tId] = symDomains[tId] || {};
        symDomains[tId][ctx.domainOf[sId]] = 1;
      }
    });
    ctx._symDomains = symDomains;

    return function (alpha) {
      var s = 0.06 * alpha;
      for (var i = 0; i < ctx.nodes.length; i++) {
        var n = ctx.nodes[i];
        if (n.kind !== 'symbol') continue;
        var set = symDomains[n.id];
        if (!set) continue;
        var keys = Object.keys(set);
        if (!keys.length) continue;
        var w = s / keys.length;
        for (var j = 0; j < keys.length; j++) {
          var a = ctx.anchors[keys[j]];
          if (!a) continue;
          n.vx += (a.x - n.x) * w;
          n.vy += (a.y - n.y) * w;
        }
      }
    };
  }
  function interDomainRepelForce(ctx, k) {
    return function (alpha) {
      var doms = ctx.domains, strength = k * alpha * 8000;
      for (var i = 0; i < doms.length; i++) {
        var a = doms[i];
        for (var j = i + 1; j < doms.length; j++) {
          var b = doms[j];
          var dx = b.x - a.x, dy = b.y - a.y;
          var d2 = dx * dx + dy * dy + 1;
          var f = strength / d2, inv = 1 / Math.sqrt(d2);
          a.vx -= dx * inv * f; a.vy -= dy * inv * f;
          b.vx += dx * inv * f; b.vy += dy * inv * f;
        }
      }
    };
  }
  function collisionRadius(n, ctx) {
    var base = KIND_RADIUS[n.kind] != null ? KIND_RADIUS[n.kind] : 6;
    return base + Math.min(8, Math.sqrt(ctx.degree[n.id] || 0));
  }

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
  function nodeColor(n) { return n.color || KIND_COLOR[n.kind] || _readToken(FALLBACK_TOKEN); }
  function labelOf(n) { return n.label || n.name || n.title || n.path || n.id || ''; }

  window.JUG = window.JUG || {};
  window.JUG._wfg = window.JUG._wfg || {};
  window.JUG._wfg.nodeRadius = nodeRadius;
  window.JUG._wfg.nodeColor  = nodeColor;
  window.JUG._wfg.labelOf    = labelOf;
  window.JUG.renderWorkflowGraph = renderWorkflowGraph;
})();
