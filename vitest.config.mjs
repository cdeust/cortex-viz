import { defineConfig } from 'vitest/config';

// V8 coverage instrumentation inflates wall-clock by roughly 2x on the trigram
// scan (measured: 1079 ms instrumented vs 271 ms clean, against a 500 ms
// bound), so a perf assertion cannot hold under it. Tests read this through
// `inject('coverageEnabled')` and report the skip rather than passing silently.
// The bound still runs unconditionally in `npm test`, which is the CI gate.
const coverageEnabled = process.argv.includes('--coverage');

// Vanilla-JS UI test config. The default environment is jsdom because most of
// ui/ manipulates the DOM; the pure-logic trigram suite opts back into the
// faster `node` environment with a per-file `// @vitest-environment node`
// docblock. Tests live next to the harness they replaced, under tests/js/.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['tests/js/**/*.test.mjs', 'tests/js/**/*.test.js'],
    // Each file loads global browser IIFEs into a shared jsdom global, so
    // files must not share a global — vitest isolates per file by default;
    // keep it explicit so a future parallelism change cannot leak state.
    isolate: true,
    provide: { coverageEnabled },
    coverage: {
      provider: 'v8',
      // Every ui/ file this suite actually loads, plus the files it is
      // expected to grow into. Report-only: there is no threshold here, on
      // purpose — a wide coverage number that gates would freeze the tree,
      // while a number nobody can read is not a measurement. Add a file when
      // its first test lands.
      include: [
        'ui/brain/js/boot.js',
        'ui/brain/js/edges.js',
        'ui/brain/js/impact.js',
        'ui/dashboard/js/interaction.js',
        'ui/shared/palette.js',
        'ui/shared/surface-toggle.js',
        'ui/unified/js/activity_stream.js',
        'ui/unified/js/config.js',
        'ui/unified/js/coverage_indicator.js',
        'ui/unified/js/coverage_model.js',
        'ui/unified/js/detail_panel.js',
        'ui/unified/js/graph.js',
        'ui/unified/js/knowledge.js',
        'ui/unified/js/polling.js',
        'ui/unified/js/renderer.js',
        'ui/unified/js/state.js',
        'ui/unified/js/trace.js',
        'ui/unified/js/trace_detail.js',
        'ui/unified/js/wiki.js',
        'ui/unified/js/workflow_graph.js',
        'ui/unified/js/workflow_graph_bridge.js',
        'ui/unified/js/workflow_graph_const.js',
        'ui/unified/js/workflow_graph_filters.js',
        'ui/unified/js/workflow_graph_lod.js',
        'ui/unified/js/workflow_graph_render_canvas.js',
        'ui/unified/js/workflow_graph_render_svg.js',
        'ui/unified/js/workflow_graph_slots.js',
        'ui/unified/js/workflow_graph_tokens.js',
        'ui/unified/js/workflow_graph_topology.js',
        'ui/unified/js/workflow_graph_trace_layout.js',
      ],
      reporter: ['text', 'json-summary'],
    },
  },
});
