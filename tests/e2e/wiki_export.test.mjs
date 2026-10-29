// Execute the shipped CLI against a disposable filesystem wiki, then exercise
// its generated HTML through the same navigation and rendering used by readers.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setImmediate as nextTurn } from 'node:timers/promises';
import test from 'node:test';
import { JSDOM, VirtualConsole } from 'jsdom';

const repo = fileURLToPath(new URL('../../', import.meta.url));

function exportFixture(fixture) {
  const fixtureHome = join(fixture, 'home');
  const wiki = join(fixtureHome, '.claude', 'methodology', 'wiki', 'guide');
  mkdirSync(wiki, { recursive: true });
  const pages = [
    { file: 'first.md', title: 'First exported page', body: '# First heading\n\nThe **first** rendered paragraph.' },
    { file: 'second.md', title: 'Second exported page', body: '# Second heading\n\nThe **second** rendered paragraph.' },
  ];
  for (const page of pages) {
    page.source = `---\ntitle: ${page.title}\nkind: guide\ndomain: e2e\nmaturity: live\n---\n${page.body}\n`;
    writeFileSync(join(wiki, page.file), page.source);
  }
  const output = join(fixture, 'export');
  const executable = join(repo, '.venv', process.platform === 'win32' ? 'Scripts/hypermnesia-mcp-viz.exe' : 'bin/hypermnesia-mcp-viz');
  execFileSync(executable, ['--export', output], {
    cwd: repo,
    // Child-only home prevents reading the owner's wiki, sessions or repos.
    // Port zero cannot address a live PostgreSQL service; exercise the
    // exporter's shipped filesystem-only fallback without replacing code.
    env: { ...process.env, HOME: fixtureHome, USERPROFILE: fixtureHome,
      DATABASE_URL: 'postgresql://127.0.0.1:0/cortex_viz_e2e',
      CLAUDE_CONFIG_DIR: join(fixtureHome, '.claude') },
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  return { artifact: join(output, 'index.html'), pages, wiki };
}

function loadArtifact(artifact) {
  const wikiNetwork = [];
  const console = new VirtualConsole();
  const dom = new JSDOM(readFileSync(artifact, 'utf8'), {
    url: pathToFileURL(artifact).href,
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: console,
    beforeParse(window) {
      window.fetch = async (url) => {
        if (String(url).includes('/api/wiki/')) wikiNetwork.push(String(url));
        throw new Error(`No network permitted in exported artifact: ${url}`);
      };
    },
  });
  return { dom, wikiNetwork };
}

async function assertPage(document, page, wiki) {
  document.querySelector(`.wiki-tree-item[data-path="guide/${page.file}"]`).click();
  await nextTurn();
  assert.match(document.querySelector('#wiki-main').textContent, new RegExp(page.title));
  assert.equal(document.querySelector('#wiki-main .wiki-body strong')?.textContent, page.file.split('.')[0]);
  assert.equal(document.querySelector('.wiki-tree-item.active')?.dataset.path, `guide/${page.file}`);
  assert.equal(readFileSync(join(wiki, page.file), 'utf8'), page.source);
}

test('shipped export CLI renders and navigates a filesystem wiki offline', async () => {
  const fixture = mkdtempSync(join(tmpdir(), 'cortex-viz-e2e-'));
  let dom;
  try {
    const { artifact, pages, wiki } = exportFixture(fixture);
    const loaded = loadArtifact(artifact);
    dom = loaded.dom;
    await new Promise(resolve => dom.window.document.addEventListener('DOMContentLoaded', resolve, { once: true }));
    const { document } = dom.window;
    document.querySelector('[data-view="wiki"]').click();
    // Offline transport resolves only promises: the next event-loop turn
    // drains their microtasks without any elapsed-time pass/fail deadline.
    await nextTurn();
    const items = [...document.querySelectorAll('.wiki-tree-item')];
    assert.equal(items.length, pages.length);
    assert.deepEqual(items.map(item => item.textContent).sort(), pages.map(page => page.title).sort());
    for (const page of pages) {
      await assertPage(document, page, wiki);
    }
    assert.deepEqual(loaded.wikiNetwork, []);
  } finally {
    dom?.window.close();
    rmSync(fixture, { recursive: true, force: true });
  }
});
