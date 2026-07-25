import { defineConfig } from "vitest/config";

// Issue #35. The UI under ui/ is plain script-tag JavaScript: IIFEs that attach
// namespaces to `window` (JUG, BRAIN, CMV). There is no bundler and adding one
// is out of scope — the tests load each file the same way the browser does
// (see tests/js/helpers/load-ui.mjs) rather than requiring the source to be
// rewritten as modules to become testable.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["tests/js/**/*.test.mjs"],
    // The trigram conformance harness shells out to a second node process and
    // builds a 300k-label corpus; it is slower than a unit test by design.
    testTimeout: 120_000,
    reporters: ["default"],
  },
});
