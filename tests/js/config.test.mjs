// Graph config palette resolution (issue #35 AC#4: palette resolution) for
// ui/unified/js/config.js. Loaded WITHOUT CortexPalette so getNodeColor
// resolves against the deterministic pre-hydration JUG._tok fallback — the
// kind->token mapping is the contract under test, not the CSS values.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let JUG;

beforeAll(() => {
  // No window.CortexPalette / CortexSurface installed → literal token fallback.
  loadScript('ui/unified/js/config.js');
  JUG = window.JUG;
});

describe('getNodeColor — memory vocabulary', () => {
  it('semantic memory resolves to the reconsolidation token', () => {
    expect(JUG.getNodeColor({ type: 'memory', storeType: 'semantic' })).toBe(JUG._tok.semantic);
  });
  it('episodic (non-semantic) memory resolves to the episodic token', () => {
    expect(JUG.getNodeColor({ type: 'memory', storeType: 'episodic' })).toBe(JUG._tok.episodic);
  });
  it('discussion resolves to the episodic token', () => {
    expect(JUG.getNodeColor({ type: 'discussion' })).toBe(JUG._tok.episodic);
  });
});

describe('getNodeColor — structure vs data', () => {
  it('structural hub types resolve to the single hub token', () => {
    for (const t of ['root', 'category', 'domain', 'agent', 'type-group']) {
      expect(JUG.getNodeColor({ type: t })).toBe(JUG._tok.hub);
    }
  });
  it('wiki nodes resolve to the accent-deep token (their resting colour)', () => {
    expect(JUG.getNodeColor({ type: 'wiki' })).toBe(JUG._tok.accentDeep);
  });
});

describe('getNodeColor — negative space (AC#6)', () => {
  // "No accent colour on non-wiki data": a plain entity/file/topic node must
  // resolve to the neutral info token, never the terracotta accent.
  it('a plain entity resolves to info, not the accent', () => {
    const c = JUG.getNodeColor({ type: 'entity' });
    expect(c).toBe(JUG._tok.info);
    expect(c).not.toBe(JUG._tok.accentDeep);
  });
  it('an unknown/leaf type falls back to info, not the accent', () => {
    const c = JUG.getNodeColor({ type: 'topic' });
    expect(c).toBe(JUG._tok.info);
    expect(c).not.toBe(JUG._tok.accentDeep);
  });
  it('a null node resolves to info without throwing', () => {
    expect(JUG.getNodeColor(null)).toBe(JUG._tok.info);
  });
});

describe('ZOOM_LEVELS — LOD distance ladder', () => {
  // The zoom ladder is monotone: coarser levels require greater camera
  // distance. A reordered/duplicated minDist would silently break level
  // selection at scale.
  it('minDist strictly decreases L3 > L2 > L1 > L0', () => {
    const z = JUG.ZOOM_LEVELS;
    expect(z.L3.minDist).toBeGreaterThan(z.L2.minDist);
    expect(z.L2.minDist).toBeGreaterThan(z.L1.minDist);
    expect(z.L1.minDist).toBeGreaterThan(z.L0.minDist);
    expect(z.L0.minDist).toBe(0);
  });
});
