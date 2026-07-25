// Load a browser UI file into the current jsdom global, the way a <script>
// tag would (issue #35).
//
// The UI under ui/ is deliberately build-step-free: every file is an IIFE that
// attaches its API to a namespace on `window` (JUG, BRAIN, CMV). Only two
// first-party files carry a CommonJS export, so `import` cannot reach the rest.
// Rewriting 91 files as ES modules to make them testable would be the tail
// wagging the dog — and would change what ships. Instead the harness evaluates
// the file in the jsdom window, which is exactly the browser's contract, so a
// test exercises the code as deployed rather than a module-shaped variant.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import vm from "node:vm";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

/**
 * Evaluate a UI script in the jsdom global and return `window`.
 *
 * @param {string} relPath  path under the repo root, e.g. "ui/unified/js/detail_format.js"
 * @returns {object} the jsdom `window`, with whatever namespace the file defined
 *
 * precondition: a jsdom `window`/`document` is the ambient global (vitest
 *   `environment: "jsdom"`).
 * postcondition: side effects of the IIFE are visible on `globalThis`.
 */
export function loadUiScript(relPath) {
  const src = readFileSync(resolve(REPO_ROOT, relPath), "utf8");
  // `runInThisContext` would not see jsdom's window; a direct indirect-eval in
  // the jsdom global does. Wrapped so a syntax error names the file.
  try {
    vm.runInThisContext(`(function(window, document, globalThis){${src}\n})`)(
      globalThis.window,
      globalThis.document,
      globalThis,
    );
  } catch (err) {
    throw new Error(`failed to evaluate ${relPath}: ${err.message}`);
  }
  return globalThis.window;
}

export { REPO_ROOT };
