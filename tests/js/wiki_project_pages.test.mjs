// Project wiki pages reach the view under a '@<project>/' path prefix
// (server: cortex_viz/infrastructure/wiki_roots.py), and their index entry
// carries the project as `domain`. The tree files a page by that entry, not
// by its path: read off the path, '@cortex/adr/cortex/1060-x.md' would put
// the page under 'adr', the kind directory, instead of under the project.
import { beforeEach, describe, expect, it } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

const ADR_PATH = '@cortex/adr/cortex/1060-refuse-a-decision-written-into-code.md';
const INDEX = {
  pages: [
    { path: 'reference/agentic-ai/overview.md', title: 'Overview', kind: 'reference', domain: '', tags: [] },
    { path: ADR_PATH, title: 'Refuse a decision written into code', kind: 'adr', domain: 'cortex', tags: [] },
  ],
};

function mountDom() {
  document.body.innerHTML = `
    <div id="wiki-container"></div>
    <div id="graph-container" style="display:none"></div>
  `;
}

function serve(url) {
  if (url.startsWith('/api/wiki/list')) return INDEX;
  if (url.startsWith('/api/wiki/projects')) return { projects: [] };
  return { ok: true, items: [], unavailable: true };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(async () => {
  globalThis.fetch = (url) =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(serve(url)) });
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  window.JUG.state = { activeView: 'wiki' };
  mountDom();
  loadScript('ui/unified/js/wiki.js');
  document.dispatchEvent(new window.Event('DOMContentLoaded'));
  window.JUG.emit('state:activeView', 'wiki');
  await flush();
  await flush();
});

describe('project wiki pages in the tree', () => {
  it('files a project page under its project and kind, not under the kind directory', () => {
    const sections = Array.from(document.querySelectorAll('.wiki-tree-section'));
    const labels = sections.map((s) => s.querySelector('.wiki-tree-kind-label').textContent);
    expect(labels).toContain('cortex');
    expect(labels).not.toContain('adr');
    const cortex = sections[labels.indexOf('cortex')];
    expect(cortex.querySelector('.wiki-tree-domain-label').textContent).toBe('Architecture Decisions');
    expect(cortex.querySelector('.wiki-tree-item').dataset.path).toBe(ADR_PATH);
  });

  it('keeps a global page without a frontmatter domain under its path domain', () => {
    const sections = Array.from(document.querySelectorAll('.wiki-tree-section'));
    const labels = sections.map((s) => s.querySelector('.wiki-tree-kind-label').textContent);
    expect(labels).toContain('agentic-ai');
  });
});
