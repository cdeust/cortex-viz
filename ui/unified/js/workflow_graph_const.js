// Cortex — Workflow Graph constants (pure data tables).
//
// Extracted from workflow_graph.js (issue #41: the 1441-line renderer
// exceeded the 500-line file cap, coding-standards §4.1). These are the
// kind-driven radii, radial-hierarchy geometry, per-tool angles, and
// per-edge distance/strength tables the topology, slot, and force modules
// all consume. Pure data with no logic — factors out cleanly, same
// precedent as workflow_graph_labels.js. Every constant keeps its original
// provenance comment (ADR / measurement source per §8).
//
// Exposes window.JUG._wfgConst = { … }. Loaded BEFORE the token, slot,
// topology, and orchestration modules (see ui/unified-viz.html), each of
// which binds the names it needs into locals at load time.
(function () {
  // Tokens — kind-driven radii.
  var KIND_RADIUS = {
    domain: 26, tool_hub: 14, agent: 10, skill: 10, command: 8,
    hook: 9, memory: 7, discussion: 8, entity: 6, file: 5, mcp: 12,
    symbol: 2,
    session: 16, prompt: 9, action: 6, api: 10, database: 10,
  };

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
    did: 28, use: 30, call: 30, spawn: 30, fetch: 30,
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
    did: 0.0, use: 0.0, call: 0.0, spawn: 0.0, fetch: 0.0,
  };
  var CROSS_DOMAIN_DISTANCE = 260;
  var CROSS_DOMAIN_STRENGTH = 0.02;

  window.JUG = window.JUG || {};
  window.JUG._wfgConst = {
    KIND_RADIUS: KIND_RADIUS,
    SETUP_R: SETUP_R, TOOL_R: TOOL_R, FILE_R: FILE_R,
    DISC_R: DISC_R, MEM_R: MEM_R, MCP_R: MCP_R,
    SYM_R_OUTER: SYM_R_OUTER, SYM_R_SPREAD: SYM_R_SPREAD, SYM_CLUMP_R: SYM_CLUMP_R,
    ENTITY_DOMAIN_BLEND: ENTITY_DOMAIN_BLEND, ENTITY_ORPHAN_R: ENTITY_ORPHAN_R,
    ENTITY_HEAT_TAU: ENTITY_HEAT_TAU, ENTITY_TOPN: ENTITY_TOPN,
    SECTOR_SETUP_HALF: SECTOR_SETUP_HALF, SECTOR_SIDE_HALF: SECTOR_SIDE_HALF,
    SECTOR_SIDE_ANGLE: SECTOR_SIDE_ANGLE,
    SHELL_LEVELS: SHELL_LEVELS, TOOL_LOCAL_ANGLE: TOOL_LOCAL_ANGLE,
    EDGE_DISTANCE: EDGE_DISTANCE, EDGE_STRENGTH: EDGE_STRENGTH,
    CROSS_DOMAIN_DISTANCE: CROSS_DOMAIN_DISTANCE,
    CROSS_DOMAIN_STRENGTH: CROSS_DOMAIN_STRENGTH,
  };
})();
