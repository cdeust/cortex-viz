// Test harness helper: load a vanilla browser IIFE (ui/**) into the current
// jsdom global scope, exactly as a <script> tag would. The UI files attach to
// `window` / a global `JUG` bus rather than exporting modules, so tests set up
// the globals they need, load the file, then read the seam the file published
// (e.g. JUG._rendererTest, window.CortexPalette).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO = path.resolve(HERE, '..', '..', '..');

// Execute a repo-relative script file in the jsdom global scope. Bare
// identifiers inside the file (`window`, `document`, `JUG`, `d3`, …) resolve
// against globalThis, so callers must install any non-DOM globals first.
export function loadScript(rel) {
  const code = fs.readFileSync(path.join(REPO, rel), 'utf8');
  // eslint-disable-next-line no-new-func -- deliberate: run browser IIFE at
  // global scope. Input is a repo-committed source file, never user data.
  const run = new Function(code);
  run.call(globalThis);
}

// Minimal JUG event-bus + draw/tooltip stubs, enough for the UI files' load
// time wiring (handler registration, guarded palette reads) to run cleanly.
export function makeJUG(extra) {
  const handlers = {};
  const jug = Object.assign(
    {
      state: {},
      on(ev, fn) {
        (handlers[ev] || (handlers[ev] = [])).push(fn);
      },
      emit(ev, val) {
        (handlers[ev] || []).forEach((fn) => fn({ type: ev, value: val }, val));
      },
      _draw: {
        node() {},
        nodeRadius() {
          return 5;
        },
        hitArea() {},
        colorAlpha(h) {
          return h;
        },
      },
      _tooltip: { show() {}, hide() {} },
    },
    extra || {}
  );
  return jug;
}
