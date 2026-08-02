// ESLint flat config for the browser UI (issue #45).
//
// Scope: `ui/` only. This is vanilla browser JavaScript — no bundler, no
// framework, no module graph — so the rule set is deliberately narrow:
// the classes of defect a reader cannot catch by reading one file.
// Style is NOT enforced here; `ruff format`'s Python equivalent has no
// counterpart in this tree and review covers formatting.
//
// Vendored bundles are excluded: they are minified third-party artefacts,
// not authored source, and linting them reports 197 findings nobody will
// ever act on.

// Cross-file namespaces. Each is created once with
// `var X = window.X || {}` and read unqualified everywhere else, which is
// what makes this tree work without a module system. Declared writable
// because the creating file assigns to the same binding.
const projectGlobals = {
  JUG: "writable", // unified viz
  JMD: "writable", // methodology map
  BRAIN: "writable", // brain view
  CMV: "writable", // shared viz helpers
  TraceView: "writable", // trace panel (window.TraceView, trace.js)
};

// Third-party globals loaded via <script> from ui/*/vendor/.
const vendorGlobals = {
  THREE: "readonly",
  d3: "readonly",
  ForceGraph: "readonly",
  ForceGraph3D: "readonly",
};

const browserGlobals = {
  // Two UI modules (spatial_hash, trigram) carry a
  // `typeof module !== 'undefined' && module.exports` tail so the vitest
  // suite can import them directly. It is guarded, so it is not a CommonJS
  // assumption leaking into the browser build.
  module: "readonly",
  window: "readonly",
  document: "readonly",
  console: "readonly",
  fetch: "readonly",
  self: "readonly",
  globalThis: "readonly",
  location: "readonly",
  history: "readonly",
  navigator: "readonly",
  localStorage: "readonly",
  sessionStorage: "readonly",
  innerWidth: "readonly",
  innerHeight: "readonly",
  devicePixelRatio: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  setInterval: "readonly",
  clearInterval: "readonly",
  requestAnimationFrame: "readonly",
  cancelAnimationFrame: "readonly",
  performance: "readonly",
  addEventListener: "readonly",
  removeEventListener: "readonly",
  postMessage: "readonly",
  scrollTo: "readonly",
  matchMedia: "readonly",
  getComputedStyle: "readonly",
  alert: "readonly",
  confirm: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  Blob: "readonly",
  Image: "readonly",
  Worker: "readonly",
  importScripts: "readonly",
  WebSocket: "readonly",
  EventSource: "readonly",
  AbortController: "readonly",
  AbortSignal: "readonly",
  CustomEvent: "readonly",
  HTMLElement: "readonly",
  customElements: "readonly",
  DOMParser: "readonly",
  NodeFilter: "readonly",
  MutationObserver: "readonly",
  ResizeObserver: "readonly",
  IntersectionObserver: "readonly",
  TextDecoder: "readonly",
  TextEncoder: "readonly",
};

export default [
  {
    ignores: ["ui/**/vendor/**", "node_modules/**", "coverage/**", "reports/**"],
  },
  {
    // Same call as `ignore = ["RUF100"]` in pyproject.toml, for the same
    // reason: with a curated rule set, an "unused" disable directive is
    // usually a suppression for a rule this config does not enable
    // (`no-new-func`, `no-console`), not a stale one. Reporting them
    // invites deleting reviewed decisions.
    linterOptions: { reportUnusedDisableDirectives: "off" },
  },
  {
    files: ["ui/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: { ...browserGlobals, ...vendorGlobals, ...projectGlobals },
    },
    rules: {
      // A name that resolves nowhere is either a typo or a missing
      // <script> tag — the failure mode this tree is most exposed to,
      // since load order is hand-maintained in the HTML.
      "no-undef": "error",
      // An assignment with no declaration silently creates a property on
      // window, shared by every view on the page.
      "no-implicit-globals": "error",
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
      "no-redeclare": ["error", { builtinGlobals: false }],
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-unreachable": "error",
      "no-const-assign": "error",
      "no-self-assign": "error",
      "no-cond-assign": "error",
    },
  },
  {
    // The vitest suite runs in Node with the vitest globals injected.
    files: ["tests/js/**/*.js", "tests/js/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...browserGlobals,
        ...projectGlobals,
        describe: "readonly",
        it: "readonly",
        test: "readonly",
        expect: "readonly",
        vi: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly",
        beforeAll: "readonly",
        afterAll: "readonly",
        process: "readonly",
        global: "readonly",
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
      "no-dupe-keys": "error",
      "no-unreachable": "error",
    },
  },
];
