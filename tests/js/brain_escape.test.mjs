// HTML escaping for the brain view's two innerHTML builders — the legend
// (ui/brain/js/boot.js) and the impact panel (ui/brain/js/impact.js).
//
// Both interpolate escaped values into DOUBLE-QUOTED attributes
// (`data-kind="…"`, `data-color-cat="…"`, `data-file="…"`). Their `esc()`
// used to escape only `&<>`, so a value containing `"` closed the attribute
// early and everything after it was parsed as MORE attributes: the string
// `a" onmouseover="alert(1)` turned into a live event handler. That is the
// defect CodeQL reports as js/incomplete-html-attribute-sanitization, and it
// is reachable with real data — `data-file` carries a repo path and
// `data-kind` a node kind, both server-supplied strings.
//
// The assertions below are written against the PARSED DOM rather than the
// HTML string, because the parsed DOM is what the breakout actually produces:
// a string comparison can pass while the browser still sees two attributes.
// Each test therefore checks the round-trip (the attribute reads back as the
// exact input) AND the absence of any injected attribute — absence is the
// behaviour, so it needs its own negative assertion.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

// The canonical attribute-breakout payload, reused by every case below. It
// closes the attribute, adds a handler, and leaves a dangling opener so a
// partial fix (escaping the closing quote but not the opening one) still
// fails.
const BREAKOUT = 'a" onmouseover="alert(1)';

function parseOne(html) {
  const host = document.createElement('div');
  host.innerHTML = html;
  return host;
}

// Every attribute the parser ended up putting on `el`, as a plain object —
// the seam that makes an injected handler visible as data rather than as a
// substring of some HTML text.
function attrsOf(el) {
  const out = {};
  for (const a of el.attributes) out[a.name] = a.value;
  return out;
}

describe('brain legend escaping (ui/brain/js/boot.js)', () => {
  let esc;
  let legendRowHtml;

  beforeAll(() => {
    // boot.js bootstraps itself on load (start() → BRAIN.fetchGraph /
    // BRAIN.loadBrain). Stub both with promises that never settle so the
    // boot chain parks instead of throwing; the seam we want is published
    // before that bootstrap runs.
    window.BRAIN = {
      fetchGraph: () => new Promise(() => {}),
      loadBrain: () => new Promise(() => {}),
      MEMORY_SYSTEMS: [],
    };
    loadScript('ui/brain/js/boot.js');
    ({ esc, legendRowHtml } = window.BRAIN._legendTest);
  });

  it('escapes the full HTML special set, quotes included', () => {
    expect(esc('&')).toBe('&amp;');
    expect(esc('<')).toBe('&lt;');
    expect(esc('>')).toBe('&gt;');
    expect(esc('"')).toBe('&quot;');
    expect(esc("'")).toBe('&#39;');
  });

  it('leaves ordinary text untouched and coerces null/undefined to empty', () => {
    expect(esc('memory')).toBe('memory');
    expect(esc(null)).toBe('');
    expect(esc(undefined)).toBe('');
    expect(esc(0)).toBe('0');
  });

  it('does not let a quoted kindKey inject an attribute', () => {
    const row = parseOne(legendRowHtml('info', 'label', 3, null, BREAKOUT))
      .firstElementChild;
    // Round-trip: the parser hands back exactly what went in, which is only
    // possible if the quote was entity-encoded.
    expect(row.getAttribute('data-kind')).toBe(BREAKOUT);
    // Negative assertion — the injected handler must not exist as an
    // attribute under any casing the parser might normalise it to.
    expect(row.hasAttribute('onmouseover')).toBe(false);
    expect(Object.keys(attrsOf(row)).sort()).toEqual(
      ['aria-pressed', 'class', 'data-kind', 'role', 'tabindex']
    );
  });

  it('does not let a quoted colour category inject an attribute', () => {
    const dot = parseOne(legendRowHtml(BREAKOUT, 'label', 1, null, 'memory'))
      .querySelector('.leg-dot');
    expect(dot.getAttribute('data-color-cat')).toBe(BREAKOUT);
    expect(dot.hasAttribute('onmouseover')).toBe(false);
    expect(Object.keys(attrsOf(dot)).sort()).toEqual(['class', 'data-color-cat']);
  });

  it('renders a quoted label as text, not markup', () => {
    const row = parseOne(legendRowHtml('info', '<img src=x onerror="alert(1)">', 1))
      .firstElementChild;
    expect(row.querySelector('img')).toBeNull();
    expect(row.querySelector('.leg-label').textContent)
      .toBe('<img src=x onerror="alert(1)">');
  });
});

describe('brain impact-panel escaping (ui/brain/js/impact.js)', () => {
  let esc;
  let group;

  beforeAll(() => {
    window.BRAIN = window.BRAIN || {};
    loadScript('ui/brain/js/impact.js');
    ({ esc, group } = window.TraceView._impactTest);
  });

  it('escapes the full HTML special set, quotes included', () => {
    expect(esc('&')).toBe('&amp;');
    expect(esc('<')).toBe('&lt;');
    expect(esc('>')).toBe('&gt;');
    expect(esc('"')).toBe('&quot;');
    expect(esc("'")).toBe('&#39;');
  });

  // An impact row's optional fields (label, kind, confidence) are absent far
  // more often than not, so the null arm is the common path — without the
  // coercion the panel renders the literal text "null".
  it('leaves ordinary text untouched and coerces null/undefined to empty', () => {
    expect(esc('cortex_viz/app.py')).toBe('cortex_viz/app.py');
    expect(esc(null)).toBe('');
    expect(esc(undefined)).toBe('');
    expect(esc(0)).toBe('0');
  });

  it('does not let a quoted file path inject an attribute', () => {
    const box = parseOne(group('Depends on', [{ file: BREAKOUT, kind: 'imports' }], 'down'))
      .querySelector('.impact-box');
    expect(box.getAttribute('data-file')).toBe(BREAKOUT);
    expect(box.hasAttribute('onmouseover')).toBe(false);
    expect(Object.keys(attrsOf(box)).sort()).toEqual(['class', 'data-file']);
  });

  it('renders a quoted label as text, not markup', () => {
    const box = parseOne(
      group('Depends on', [{ file: 'a.py', label: '<img src=x onerror="alert(1)">' }], 'down')
    ).querySelector('.impact-box');
    expect(box.querySelector('img')).toBeNull();
    expect(box.querySelector('.impact-name').textContent)
      .toBe('<img src=x onerror="alert(1)">');
  });
});
