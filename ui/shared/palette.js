/* =============================================================================
   cortex-viz × AI Architect — data palette, single source of truth
   ---------------------------------------------------------------------------
   "Colour only from data." The chrome is greyscale; every colour a renderer
   paints (memory stage, heat, tool, kind, edge, emotional valence) is DATA and
   MUST come from the design-system data tokens — which surfaces.css already
   re-inks per surface (bright on the ink instrument; DEEP on the paper record,
   the cold end fading into the page).

   The tokens live in CSS. CSS/SVG/DOM consumers read them for free. This module
   is the bridge for the renderers that BAKE colour numbers — force-graph canvas,
   the Three.js brain — which cannot observe a CSS custom property:

     · readVar('--stage-labile')  → the resolved value on the CURRENT surface
     · hex('--stage-labile')      → '#rrggbb' (paints the token in an offscreen
                                     canvas and reads it back — so oklch, on
                                     either surface, resolves to real sRGB)
     · stages() / heat() / emo()  → surface-correct maps keyed by cortex's own
                                     data vocabulary, ready to hand a renderer

   Values are read LIVE, so after a `cortex:surface-change` event a renderer just
   re-invokes these to get the re-inked palette. Load AFTER surface-toggle.js
   and AFTER ds.css so the tokens resolve:
       <script src="/shared/palette.js"></script>

   PYTHON / OFF-BROWSER CONSUMERS (cortex_viz/core/workflow_graph_palette.py,
   ui/brain/js/palette.js STAGE_COLORS): you cannot read CSS at build time —
   mirror the authoritative oklch table in shared/README.md, keyed by the SAME
   names, and keep both surfaces. That table is the contract; this file is its
   browser-side reader.
   ============================================================================= */
(function (root) {
  "use strict";

  var _probe = null;
  var _cache = {}; // "surface|--token" -> "#rrggbb"

  function _rootStyle() {
    return getComputedStyle(document.documentElement);
  }

  /** Resolved value of a design-system token on the current surface (may be
   *  an oklch(...) string on modern engines). */
  function readVar(name) {
    return _rootStyle().getPropertyValue(name).trim();
  }

  function _canvas() {
    if (_probe) return _probe;
    _probe = document.createElement("canvas");
    _probe.width = _probe.height = 1;
    return _probe;
  }

  /** sRGB hex for a token on the current surface. Paints the resolved value
   *  into a 1×1 canvas and reads the pixel back, so any colour syntax the
   *  browser understands (oklch, color-mix, hex) collapses to #rrggbb — the
   *  form Three.js / WebGL need. Cached per (surface, token). */
  function hex(name) {
    var surface = (root.CortexSurface && root.CortexSurface.get()) || "paper";
    var key = surface + "|" + name;
    if (_cache[key]) return _cache[key];

    var value = readVar(name);
    var ctx = _canvas().getContext("2d");
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000";
    try {
      ctx.fillStyle = value; // ignored silently if the engine can't parse it
    } catch (_) {
      /* leave the #000 fallback */
    }
    ctx.fillRect(0, 0, 1, 1);
    var d = ctx.getImageData(0, 0, 1, 1).data;
    var out =
      "#" +
      [d[0], d[1], d[2]]
        .map(function (c) {
          return ("0" + c.toString(16)).slice(-2);
        })
        .join("");
    _cache[key] = out;
    return out;
  }

  function _mapHex(dict) {
    var out = {};
    Object.keys(dict).forEach(function (k) {
      out[k] = hex(dict[k]);
    });
    return out;
  }

  // ── cortex data vocabulary → design-system tokens ──────────────────────────
  // Consolidation lifecycle. cortex paints five memory stages; the design
  // system ships five stage tokens (labile → early → late → cons → recon).
  // "semantic" (extracted schema) reads as the reconsolidation hue.
  // Refs: McClelland et al. 1995; Foster & Wilson 2006.
  var STAGE_TOKENS = {
    labile: "--stage-labile",
    "early-ltp": "--stage-early",
    "late-ltp": "--stage-late",
    consolidated: "--stage-cons",
    semantic: "--stage-recon",
  };
  var HEAT_TOKENS = {
    hot: "--heat-hot",
    warm: "--heat-warm",
    cool: "--heat-cool",
    cold: "--heat-cold",
  };
  var EMO_TOKENS = {
    urgent: "--emo-urgent",
    frustration: "--emo-frustr",
    satisfaction: "--emo-satisf",
    discovery: "--emo-discov",
    conflict: "--emo-conflct",
  };

  root.CortexPalette = {
    readVar: readVar,
    hex: hex,
    /** Invalidate the hex cache — call if tokens are swapped at runtime. */
    flush: function () {
      _cache = {};
    },
    /** Surface-correct hex maps, keyed by cortex's data vocabulary. */
    stages: function () {
      return _mapHex(STAGE_TOKENS);
    },
    heat: function () {
      return _mapHex(HEAT_TOKENS);
    },
    emo: function () {
      return _mapHex(EMO_TOKENS);
    },
    tokens: { stage: STAGE_TOKENS, heat: HEAT_TOKENS, emo: EMO_TOKENS },
  };

  // Baked-colour renderers listen here and re-read the maps above.
  if (root.CortexSurface) {
    root.addEventListener(root.CortexSurface.EVENT, function () {
      _cache = {};
    });
  }
})(window);
