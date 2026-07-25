import { defineConfig } from 'vitest/config';

// Vanilla-JS UI test config. The default environment is jsdom because most of
// ui/ manipulates the DOM; the pure-logic trigram suite opts back into the
// faster `node` environment with a per-file `// @vitest-environment node`
// docblock. Tests live next to the harness they replaced, under tests/js/.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['tests/js/**/*.test.mjs'],
    // Each file loads global browser IIFEs into a shared jsdom global, so
    // files must not share a global — vitest isolates per file by default;
    // keep it explicit so a future parallelism change cannot leak state.
    isolate: true,
    coverage: {
      provider: 'v8',
      include: [
        'ui/unified/js/renderer.js',
        'ui/unified/js/workflow_graph_filters.js',
        'ui/unified/js/workflow_graph.js',
        'ui/unified/js/config.js',
        'ui/unified/js/workflow_graph_render_svg.js',
        'ui/unified/js/workflow_graph_render_canvas.js',
        'ui/shared/palette.js',
        'ui/brain/js/trigram.js',
      ],
      reporter: ['text', 'json-summary'],
    },
  },
});
