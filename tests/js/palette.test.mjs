// Palette resolution + cortex:surface-change refresh (issue #35 AC#4) for
// ui/shared/palette.js — the bridge that lets baked-colour renderers (canvas /
// WebGL, which cannot read a CSS custom property) resolve a design-system token
// to sRGB and re-resolve it after a surface flip. This is the exact class of
// silent bug the issue calls out.
import { describe, it, expect, beforeEach } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

// A stand-in 2D context: palette.hex() writes the resolved CSS value to
// fillStyle then reads the pixel back. We parse rgb()/#hex into the pixel and
// count getImageData calls so the cache behaviour is observable.
function installFakeCanvas() {
  const state = { rgb: [0, 0, 0], reads: 0 };
  const ctx = {
    clearRect() {},
    fillRect() {},
    set fillStyle(v) {
      const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(v);
      if (m) {
        state.rgb = [Number(m[1]), Number(m[2]), Number(m[3])];
        return;
      }
      const h = /^#([0-9a-f]{6})$/i.exec(v);
      if (h) {
        state.rgb = [0, 2, 4].map((i) => parseInt(h[1].slice(i, i + 2), 16));
      }
      // unparseable (e.g. '#000' sentinel or an oklch the engine can't read)
      // leaves the previous pixel — matches the real 1x1-canvas fallback.
    },
    get fillStyle() {
      return '';
    },
    getImageData() {
      state.reads += 1;
      return { data: [state.rgb[0], state.rgb[1], state.rgb[2], 255] };
    },
  };
  window.HTMLCanvasElement.prototype.getContext = () => ctx;
  return state;
}

function setVar(name, value) {
  document.documentElement.style.setProperty(name, value);
}

let canvasState;

beforeEach(() => {
  document.documentElement.removeAttribute('data-surface');
  document.documentElement.removeAttribute('style');
  canvasState = installFakeCanvas();
  // surface-toggle first so palette registers its flush-on-change listener.
  loadScript('ui/shared/surface-toggle.js');
  loadScript('ui/shared/palette.js');
});

describe('CortexPalette.readVar', () => {
  it('returns the resolved custom-property value on the current surface', () => {
    setVar('--stage-labile', 'rgb(11, 22, 33)');
    expect(window.CortexPalette.readVar('--stage-labile').trim()).toBe('rgb(11, 22, 33)');
  });
});

describe('CortexPalette.hex', () => {
  it('collapses a resolved rgb() token to #rrggbb', () => {
    setVar('--x', 'rgb(10, 20, 30)');
    expect(window.CortexPalette.hex('--x')).toBe('#0a141e');
  });

  // Negative assertion (AC#6): a second read of the same token on the same
  // surface must NOT re-paint the probe canvas — the value is cached.
  it('caches per (surface, token): no recompute on the second read', () => {
    setVar('--x', 'rgb(10, 20, 30)');
    window.CortexPalette.hex('--x');
    const readsAfterFirst = canvasState.reads;
    window.CortexPalette.hex('--x');
    expect(canvasState.reads).toBe(readsAfterFirst); // no additional getImageData
  });

  it('serves the stale cached value until a surface-change flushes it', () => {
    setVar('--x', 'rgb(10, 20, 30)');
    expect(window.CortexPalette.hex('--x')).toBe('#0a141e');

    // Token re-inked underneath, but no event yet → cache still serves old.
    setVar('--x', 'rgb(40, 50, 60)');
    expect(window.CortexPalette.hex('--x')).toBe('#0a141e');

    // Force the surface-change event on the SAME surface (force:true) so only
    // the cache flush — not a surface-key change — can explain a fresh value.
    window.CortexSurface.set('paper', { force: true });
    expect(window.CortexPalette.hex('--x')).toBe('#28323c');
  });

  it('exposes flush() to invalidate the cache explicitly', () => {
    setVar('--x', 'rgb(10, 20, 30)');
    expect(window.CortexPalette.hex('--x')).toBe('#0a141e');
    setVar('--x', 'rgb(40, 50, 60)');
    window.CortexPalette.flush();
    expect(window.CortexPalette.hex('--x')).toBe('#28323c');
  });
});

describe('CortexPalette.stages/heat/emo maps', () => {
  it('keys the stage map by cortex\'s data vocabulary', () => {
    const t = window.CortexPalette.tokens.stage;
    expect(Object.keys(t).sort()).toEqual(
      ['consolidated', 'early-ltp', 'labile', 'late-ltp', 'semantic'].sort()
    );
  });
});
