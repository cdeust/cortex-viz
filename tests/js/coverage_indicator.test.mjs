// Coverage-honesty indicator (issue #36) — the DOM surface,
// ui/unified/js/coverage_indicator.js. Criterion 6 / §13 A3: every emission is
// asserted directly — including the emission of the quiet "complete"
// affordance (criterion 5) and the ABSENCE of a warning on a covered view
// (a negative assertion, §13 G4). Tests drive the pure render() with synthetic
// reports and the live update() through gatherSources over stubbed globals.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

function setup() {
  document.body.innerHTML = '';
  // fetch is called by boot()'s first engine read; resolve an unavailable
  // payload so boot never rejects and the model records the named mode.
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ available: false, reason: 'test' }) })
  );
  const jug = makeJUG();
  jug.state = { activeView: 'graph' };
  window.JUG = jug;
  globalThis.JUG = jug;
  loadScript('ui/unified/js/coverage_model.js');
  loadScript('ui/unified/js/coverage_indicator.js');
  return jug;
}

describe('render — the quiet complete affordance (criterion 5)', () => {
  beforeEach(setup);

  it('shows a compact "complete" pill and NO omission rows, NO warning', () => {
    const c = document.createElement('div');
    const report = window.JUG.Coverage.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 10, files_indexed: 10 },
    });
    expect(report.status).toBe('complete');
    window.JUG.CoverageIndicator.render(c, report);

    expect(c.querySelector('.coverage-pill.is-complete')).not.toBe(null);
    expect(c.querySelector('.coverage-pill-label').textContent).toBe('Complete');
    // Negative assertions — a covered view raises no warning.
    expect(c.querySelector('.coverage-pill.is-incomplete')).toBe(null);
    expect(c.querySelectorAll('.coverage-omission').length).toBe(0);
    expect(c.getAttribute('data-status')).toBe('complete');
  });
});

describe('render — incomplete drill-down (criteria 2/3/4)', () => {
  beforeEach(setup);

  it('emits one omission row per omission, each with a count', () => {
    const c = document.createElement('div');
    const report = window.JUG.Coverage.computeCoverage('graph', {
      rendered: { nodes: 60, edges: 20, lodTier: { heavy: true, snapToSlots: false, extreme: false }, droppedEdges: 700 },
      store: { node_count: 100, edge_count: 40, revision: 'r2' },
      stream: { revision: 'r1', node_total: 100, edge_total: 40, truncated: true },
      progress: { full_ready: true },
      engine: {
        available: true, files_present: 30, files_indexed: 25,
        extraction_failures: [{ path: 'x.c', error_ranges: [], reason: 'ERROR' }],
      },
    });
    window.JUG.CoverageIndicator.render(c, report);

    expect(c.querySelector('.coverage-pill.is-incomplete')).not.toBe(null);
    const kinds = [...c.querySelectorAll('.coverage-omission')].map((li) => li.getAttribute('data-kind'));
    expect(kinds).toContain('nodes');
    expect(kinds).toContain('files');
    expect(kinds).toContain('lod');
    expect(kinds).toContain('stale');
    expect(kinds).toContain('truncated');
    expect(kinds).toContain('extraction-failures');

    // LOD collapse magnitude is DISPLAYED, not inferred (criterion 3).
    expect(c.querySelector('.coverage-lod').getAttribute('data-collapsed')).toBe('700');
    // Staleness shows the revisions (criterion 4).
    expect(c.querySelector('.coverage-staleness.is-stale')).not.toBe(null);
    expect(c.querySelector('.coverage-staleness').textContent).toContain('store rev r2');
  });

  it('renders the named degraded chip when engine coverage is unavailable (§13 F2)', () => {
    const c = document.createElement('div');
    const report = window.JUG.Coverage.computeCoverage('graph', {
      rendered: { nodes: 100, edges: 50 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: null,
    });
    window.JUG.CoverageIndicator.render(c, report);
    const chip = c.querySelector('.coverage-degraded[data-mode="engine-coverage-unavailable"]');
    expect(chip).not.toBe(null);
    expect(chip.textContent.length).toBeGreaterThan(0);
  });

  it('the drill-down is collapsed until the pill is clicked (non-modal)', () => {
    const c = document.createElement('div');
    const report = window.JUG.Coverage.computeCoverage('graph', {
      rendered: { nodes: 10, edges: 5 },
      store: { node_count: 100, edge_count: 50, revision: 'r1' },
      stream: { revision: 'r1', node_total: 100, edge_total: 50 },
      progress: { full_ready: true },
      engine: { available: true, files_present: 1, files_indexed: 1 },
    });
    window.JUG.CoverageIndicator.render(c, report);
    const drill = c.querySelector('.coverage-drill');
    expect(drill.hidden).toBe(true);
    c.querySelector('.coverage-pill').click();
    expect(drill.hidden).toBe(false);
  });
});

describe('gatherSources + update — live wiring', () => {
  beforeEach(setup);

  it('reads the published globals for the active view', () => {
    const streamObj = { revision: 'z', node_total: 100, edge_total: 20 };
    const progressObj = { full_ready: true };
    const engineObj = { available: true, files_present: 3, files_indexed: 3 };
    window.JUG.__wfgRendered = { nodes: 42, edges: 10 };
    window.JUG.__storeMeta = { node_count: 100, edge_count: 20, revision: 'z' };
    window.JUG.__streamResult = streamObj;
    window.JUG.__progress = progressObj;
    window.JUG.__engineCoverage = engineObj;
    const s = window.JUG.CoverageIndicator.gatherSources('graph');
    expect(s.rendered.nodes).toBe(42);
    expect(s.store.node_count).toBe(100);
    expect(s.store.revision).toBe('z');
    // Each published global is passed through by reference (not coerced away).
    expect(s.stream).toBe(streamObj);
    expect(s.progress).toBe(progressObj);
    expect(s.engine).toBe(engineObj);
    expect(s.denominatorMeaningful).toBe(true);
  });

  it('yields null (not a truthy stand-in) for each source a producer has not published', () => {
    delete window.JUG.__wfgRendered;
    delete window.JUG.__storeMeta;
    delete window.JUG.__streamResult;
    delete window.JUG.__progress;
    delete window.JUG.__engineCoverage;
    const s = window.JUG.CoverageIndicator.gatherSources('graph');
    expect(s.rendered).toBe(null);
    expect(s.store).toBe(null);
    expect(s.stream).toBe(null);
    expect(s.progress).toBe(null);
    expect(s.engine).toBe(null);
  });

  it('store.revision is null when the store meta carries none (not undefined)', () => {
    window.JUG.__storeMeta = { node_count: 1, edge_count: 1 };
    expect(window.JUG.CoverageIndicator.gatherSources('graph').store.revision).toBe(null);
  });

  it('marks the trace view denominator as not meaningful (on-demand)', () => {
    expect(window.JUG.CoverageIndicator.gatherSources('trace').denominatorMeaningful).toBe(false);
  });

  it('defaults an unregistered view to a meaningful denominator', () => {
    // gatherSources is a public seam; called with a view not in VIEWS it must
    // still return a usable config (denominator meaningful by default), not throw.
    expect(window.JUG.CoverageIndicator.gatherSources('mystery').denominatorMeaningful).toBe(true);
  });

  it('hides the indicator on a non-graph view', () => {
    window.JUG.state.activeView = 'wiki';
    const report = window.JUG.CoverageIndicator.update();
    expect(report).toBe(null);
    expect(document.getElementById('coverage-indicator').style.display).toBe('none');
  });

  it('paints the indicator on the graph view', () => {
    window.JUG.__wfgRendered = { nodes: 100, edges: 50 };
    window.JUG.__storeMeta = { node_count: 100, edge_count: 50, revision: 'r1' };
    window.JUG.__streamResult = { revision: 'r1', node_total: 100, edge_total: 50 };
    window.JUG.__progress = { full_ready: true };
    window.JUG.__engineCoverage = { available: true, files_present: 5, files_indexed: 5 };
    window.JUG.state.activeView = 'graph';
    const report = window.JUG.CoverageIndicator.update();
    expect(report.status).toBe('complete');
    expect(document.getElementById('coverage-indicator').getAttribute('data-status')).toBe('complete');
  });
});

describe('fmtAge — snapshot age humanisation (criterion 4)', () => {
  beforeEach(setup);
  it('rolls seconds/minutes/hours/days', () => {
    const f = window.JUG.CoverageIndicator.fmtAge;
    expect(f(null)).toBe(null);
    expect(f(1000)).toBe('just now');
    expect(f(30000)).toBe('30s ago');
    expect(f(120000)).toBe('2m ago');
    expect(f(3 * 3600 * 1000)).toBe('3h ago');
    expect(f(2 * 86400 * 1000)).toBe('2d ago');
  });
  it('pins the exact rollover boundaries (< not <=)', () => {
    const f = window.JUG.CoverageIndicator.fmtAge;
    expect(f(5000)).toBe('5s ago');       // 5s is NOT "just now" (s < 5)
    expect(f(60000)).toBe('1m ago');      // 60s rolls to minutes (s < 60)
    expect(f(3600000)).toBe('1h ago');    // 60m rolls to hours (m < 60)
    expect(f(86400000)).toBe('1d ago');   // 24h rolls to days (h < 24)
  });
});

describe('degraded-mode chips — exact copy is the F2 signal (pinned)', () => {
  beforeEach(setup);
  it('renders the exact label for each named degraded mode', () => {
    const c = document.createElement('div');
    // engine null → unavailable; denominatorMeaningful false → on-demand;
    // progress.full_ready false → build-in-progress. All three at once.
    const report = window.JUG.Coverage.computeCoverage('trace', {
      rendered: { nodes: 5, edges: 3 },
      denominatorMeaningful: false,
      progress: { full_ready: false },
      engine: null,
    });
    window.JUG.CoverageIndicator.render(c, report);
    const text = (mode) => c.querySelector(`.coverage-degraded[data-mode="${mode}"]`).textContent;
    expect(text('engine-coverage-unavailable')).toBe(
      'index parse-coverage unavailable (engine has not reported it)');
    expect(text('on-demand-expansion')).toBe(
      'expands on demand; subtree not fully materialised');
    expect(text('build-in-progress')).toBe('graph still building');
  });
});
