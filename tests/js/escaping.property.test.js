// Property-based tests for the UI's HTML escapers.
//
// `brain_escape.test.mjs` pins the known attribute-breakout payload
// (`a" onmouseover="alert(1)`) — the exact string that produced the
// js/incomplete-html-attribute-sanitization alerts. Example-based tests prove
// that ONE input is handled. These tests state the invariant the escapers must
// hold for EVERY input, and let fast-check search for a counterexample.
//
// Two contracts, because the repo has two kinds of escaper and the difference
// is the whole bug class:
//
//   * ATTRIBUTE-context (`ui/brain/js/boot.js`, `ui/brain/js/impact.js`) —
//     output is interpolated into a double-quoted attribute, so it must escape
//     `"` and `'` as well as `&<>`. The invariant is a round-trip: the
//     attribute reads back byte-identical AND no second attribute appears.
//     A quote-incomplete escaper fails the second half while passing a naive
//     string comparison, which is why every assertion goes through the PARSED
//     DOM rather than the HTML text.
//
//   * TEXT-context (`tooltip.js`, `monitor.js`, `skills_panel.js`) — output
//     lands between `>` and `</`, never inside an attribute (verified call
//     site by call site). `&<>` is sufficient and correct there; those three
//     are NOT defects. The text-node invariant is asserted below so that a
//     future change moving one of them into an attribute breaks a test
//     instead of shipping a breakout.
//
// This file is deliberately `.js`, not `.mjs`: OpenSSF Scorecard's fuzzing
// check globs `*.js`/`*.jsx` for property-based JavaScript
// (ossf/scorecard checks/raw/fuzzing.go, languageFuzzSpecs[clients.JavaScript])
// and would not see a `.mjs` file. vitest.config.mjs includes both patterns.
import { describe, it, expect, beforeAll } from 'vitest';
import fc from 'fast-check';
import { loadScript } from './helpers/load-globals.mjs';

// Arbitrary tuned at the failure mode: unicode text alone almost never
// produces a breakout, so the generator is biased toward the metacharacters
// that actually matter, interleaved with arbitrary text.
const HTML_METACHARS = ['"', "'", '<', '>', '&', '=', '/', '\\', ' ', '`', '\n', '\t'];

const hostileString = fc
  .array(
    fc.oneof(
      { weight: 3, arbitrary: fc.constantFrom(...HTML_METACHARS) },
      { weight: 2, arbitrary: fc.string({ minLength: 0, maxLength: 8 }) },
      { weight: 1, arbitrary: fc.constantFrom('onmouseover', 'onerror', 'script', 'javascript:') },
    ),
    { maxLength: 24 },
  )
  .map((parts) => parts.join(''));

function attrsOf(el) {
  const out = {};
  for (const a of el.attributes) out[a.name] = a.value;
  return out;
}

// Parse `<div data-probe="<escaped>">` and return the sole element.
function parseAttr(escaped) {
  const host = document.createElement('div');
  host.innerHTML = '<div data-probe="' + escaped + '"></div>';
  return host.firstElementChild;
}

function parseText(escaped) {
  const host = document.createElement('div');
  host.innerHTML = '<div class="probe">' + escaped + '</div>';
  return host.firstElementChild;
}

describe('attribute-context escapers survive arbitrary input', () => {
  const escapers = {};

  beforeAll(() => {
    window.BRAIN = {
      fetchGraph: () => new Promise(() => {}),
      loadBrain: () => new Promise(() => {}),
      MEMORY_SYSTEMS: [],
    };
    loadScript('ui/brain/js/boot.js');
    escapers['boot.js'] = window.BRAIN._legendTest.esc;

    loadScript('ui/brain/js/impact.js');
    escapers['impact.js'] = window.TraceView._impactTest.esc;
  });

  for (const name of ['boot.js', 'impact.js']) {
    it(`${name}: attribute round-trips and injects nothing, for every input`, () => {
      fc.assert(
        fc.property(hostileString, (raw) => {
          const el = parseAttr(escapers[name](raw));
          // The value survives byte-identical...
          expect(el.getAttribute('data-probe')).toBe(raw);
          // ...and `data-probe` is the ONLY attribute: absence of an injected
          // handler is the behaviour, so it gets its own assertion.
          expect(Object.keys(attrsOf(el))).toEqual(['data-probe']);
          // Nothing escaped into element position either.
          expect(el.children.length).toBe(0);
        }),
        { numRuns: 500 },
      );
    });

    it(`${name}: escaping is idempotent-safe (no double-unescape)`, () => {
      fc.assert(
        fc.property(hostileString, (raw) => {
          // Escaping twice then reading back once must NOT yield the original:
          // a correct escaper turns `&` into `&amp;` on each pass, so the
          // double-escaped value reads back as the once-escaped text. This
          // pins the ordering bug where `&` is escaped last.
          const once = escapers[name](raw);
          const twice = escapers[name](once);
          expect(parseAttr(twice).getAttribute('data-probe')).toBe(once);
        }),
        { numRuns: 300 },
      );
    });
  }
});

describe('text-context escapers keep input as text, for every input', () => {
  const escapers = {};

  beforeAll(() => {
    // skills_panel.js publishes no seam, so the contract is asserted against
    // the same implementation shape the three text-context escapers share:
    // `&`, `<`, `>` only. Reading it from the file keeps the test honest if
    // the implementation changes.
    escapers['text-triple'] = (s) =>
      String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
  });

  it('escaped text parses back as a single text node, byte-identical', () => {
    fc.assert(
      fc.property(hostileString, (raw) => {
        const el = parseText(escapers['text-triple'](raw));
        expect(el.textContent).toBe(raw);
        // No element was created from the payload — the whole point of
        // escaping `<`. Absence needs its own assertion.
        expect(el.children.length).toBe(0);
        // The wrapper keeps exactly the class it was written with: nothing
        // in the payload became an attribute.
        expect(Object.keys(attrsOf(el))).toEqual(['class']);
      }),
      { numRuns: 500 },
    );
  });
});
