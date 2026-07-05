/* =============================================================================
   cortex-viz × AI Architect — surface posture toggle
   ---------------------------------------------------------------------------
   The design system defines two surfaces, remapped per-alias in tokens/
   surfaces.css:

     · paper  (BRAND DEFAULT) — cream record, warm inks, terracotta stamp.
                                data views render in DEEP data inks on cream.
     · ink    (legacy)        — warm-neutral dark instrument, for parity with
                                the shipped product's current dark screens.

   Doctrine (README §3): "everything renders on the record — no black
   backgrounds." So cortex-viz boots on PAPER; ink is an explicit opt-in kept
   in localStorage. The attribute lives on <html> (documentElement) so it wins
   before first paint and every stylesheet + canvas reads one posture.

   This module is deliberately framework-free and side-effect-only on load:
   it stamps the attribute from storage IMMEDIATELY (no FOUC), then exposes
   window.CortexSurface for the toggle button and for renderers that must
   re-read the data palette on change.

   Load this in <head>, BEFORE the stylesheets, as a classic script:
       <script src="/shared/surface-toggle.js"></script>
   ============================================================================= */
(function (root) {
  "use strict";

  var STORAGE_KEY = "cortex-viz.surface";
  var SURFACES = ["paper", "ink"];
  var DEFAULT_SURFACE = "paper"; // brand doctrine — no black backgrounds
  var EVENT = "cortex:surface-change";

  function readStored() {
    try {
      var v = root.localStorage.getItem(STORAGE_KEY);
      return SURFACES.indexOf(v) !== -1 ? v : null;
    } catch (_) {
      return null; // storage blocked (private mode) — fall back to default
    }
  }

  function persist(surface) {
    try {
      root.localStorage.setItem(STORAGE_KEY, surface);
    } catch (_) {
      /* non-fatal: the attribute is still applied for this session */
    }
  }

  function current() {
    return document.documentElement.getAttribute("data-surface") || DEFAULT_SURFACE;
  }

  function apply(surface, opts) {
    if (SURFACES.indexOf(surface) === -1) surface = DEFAULT_SURFACE;
    var previous = current();
    document.documentElement.setAttribute("data-surface", surface);
    if (!opts || opts.persist !== false) persist(surface);
    if (previous !== surface || (opts && opts.force)) {
      // Renderers that bake colour values (WebGL / canvas / Three.js) cannot
      // react to a CSS custom-property change — they listen for this event and
      // re-read window.CortexPalette. DOM/SVG/CSS consumers update for free.
      try {
        root.dispatchEvent(
          new CustomEvent(EVENT, { detail: { surface: surface, previous: previous } })
        );
      } catch (_) {
        /* CustomEvent unsupported — DOM/CSS still updated, only baked renderers miss it */
      }
    }
    return surface;
  }

  function toggle() {
    return apply(current() === "paper" ? "ink" : "paper");
  }

  // ── Stamp the posture NOW, before stylesheets resolve (prevents a flash of
  //    the wrong surface). Stored choice wins; otherwise the brand default. ──
  apply(readStored() || DEFAULT_SURFACE, { persist: false });

  root.CortexSurface = {
    EVENT: EVENT,
    SURFACES: SURFACES.slice(),
    DEFAULT: DEFAULT_SURFACE,
    get: current,
    set: apply,
    toggle: toggle,
    /** Wire a button to flip the surface and reflect the resulting posture. */
    bindButton: function (el) {
      if (!el) return;
      var sync = function () {
        el.setAttribute("data-surface-state", current());
        el.setAttribute(
          "aria-label",
          current() === "paper" ? "Switch to instrument (ink) mode" : "Switch to paper mode"
        );
      };
      el.addEventListener("click", function () {
        toggle();
        sync();
      });
      root.addEventListener(EVENT, sync);
      sync();
    },
  };
})(window);
