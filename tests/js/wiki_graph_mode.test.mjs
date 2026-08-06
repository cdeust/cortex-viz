// The wiki view's graph mode has three outcomes that arrive over the same
// 200 response and look alike unless the payload is inspected: a real graph, a
// wiki with nothing to draw, and an op this server does not serve. Before #119
// only the first was handled, so the other two mounted an empty canvas with
// nothing on screen to explain it.
import { beforeEach, describe, expect, it } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

let enterGraphMode;

function mountDom() {
  document.body.innerHTML = `
    <div id="wiki-main"></div>
    <div id="graph-container" style="display:none"></div>
    <select id="wiki-graph-domain"><option value="cortex" selected>cortex</option></select>
  `;
}

function reply(payload) {
  globalThis.fetch = () =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
}

const mainText = () => document.getElementById('wiki-main').textContent;
const canvasShown = () =>
  document.getElementById('graph-container').style.display !== 'none';

beforeEach(() => {
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  window.JUG.state = { activeView: 'wiki' };
  mountDom();
  loadScript('ui/unified/js/wiki.js');
  ({ enterGraphMode } = window.JUG._wikiTest);
});

describe('wiki graph mode', () => {
  it('renders a real graph by handing the payload to the shared renderer', async () => {
    const payload = {
      nodes: [{ id: 'wiki:1', kind: 'wiki', label: 'Page 1' }],
      edges: [],
      meta: { schema: 'workflow_graph.v1', lens: 'wiki', empty: false },
    };
    reply(payload);

    enterGraphMode();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(window.JUG.state.lastData).toEqual(payload);
    expect(canvasShown()).toBe(true);
  });

  it('names an op this server does not serve instead of drawing nothing', async () => {
    // The exact fall-through shape from http_standalone_wiki._dispatch_get.
    reply({ ok: true, items: [], note: 'not_served_by_viz', unavailable: true });

    enterGraphMode();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mainText()).toContain('Graph unavailable');
    expect(window.JUG.state.lastData).toBeUndefined();
    expect(canvasShown()).toBe(false);
  });

  it('distinguishes an empty wiki from an unavailable one', async () => {
    reply({
      nodes: [],
      edges: [],
      meta: { schema: 'workflow_graph.v1', lens: 'wiki', empty: true },
    });

    enterGraphMode();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mainText()).toContain('Nothing to graph');
    expect(mainText()).not.toContain('Graph unavailable');
    expect(window.JUG.state.lastData).toBeUndefined();
    expect(canvasShown()).toBe(false);
  });

  it('still reports a transport failure', async () => {
    globalThis.fetch = () => Promise.resolve({ ok: false, status: 500 });

    enterGraphMode();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mainText()).toContain('Graph unavailable');
    expect(window.JUG.state.lastData).toBeUndefined();
  });

  it('reports an explicit server error', async () => {
    reply({ error: 'wiki schema absent' });

    enterGraphMode();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mainText()).toContain('wiki schema absent');
    expect(window.JUG.state.lastData).toBeUndefined();
  });
});
