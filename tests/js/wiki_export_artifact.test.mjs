// @vitest-environment node
//
// #112 criterion 1: the bundle opens from file:// with no server and no network,
// "asserted by a test that launches the BUILT ARTIFACT, not the sources".
//
// So this test builds the bundle with the real exporter, loads the resulting
// file in a fresh JSDOM over a `file://` URL, and forbids the network outright —
// `fetch`, `XMLHttpRequest` and `WebSocket` all throw if anything reaches for
// them. A bundle that needs the network fails here rather than on someone's
// laptop.
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { JSDOM } from 'jsdom';

const REPO = new URL('../../', import.meta.url).pathname;

let artifact = '';
let outDir = '';

// One page of each kind so the taxonomy assertion below has something to find,
// plus a maturity value, because those two are criterion 4.
const KINDS = [
  'adr', 'spec', 'lesson', 'convention', 'note',
  'guide', 'domain', 'entity', 'index', 'misc',
];

function buildBundle(pagesJson) {
  const dir = mkdtempSync(join(tmpdir(), 'cortex-wiki-export-'));
  const script = `
import json, pathlib, sys
from cortex_viz.handlers.wiki_export_bundle import export_wiki
pages = json.loads(sys.argv[1])
def respond(path, params):
    if path == '/api/wiki/list':          return {'pages': pages}
    if path == '/api/wiki/bibliography':  return {'files': []}
    if path == '/api/wiki/projects':      return {'projects': []}
    if path == '/api/wiki/page':
        return {'path': params.get('path'), 'meta': {'kind': 'adr'},
                'body': '# Body of ' + str(params.get('path'))}
    if path == '/api/wiki/page_meta':     return {'ok': True}
    if path == '/api/wiki/graph':
        return {'nodes': [], 'edges': [], 'clusters': [],
                'meta': {'schema': 'workflow_graph.v1', 'empty': not pages}}
    return {'ok': True, 'items': [], 'unavailable': True}
m = export_wiki(out_dir=pathlib.Path(sys.argv[2]), ui_root=pathlib.Path('ui'),
                respond=respond)
print(json.dumps(m))
`;
  const out = execFileSync(
    join(REPO, '.venv/bin/python'),
    ['-c', script, JSON.stringify(pagesJson), dir],
    { cwd: REPO, encoding: 'utf8' }
  );
  return { dir, manifest: JSON.parse(out.trim().split('\n').pop()) };
}

function launch(file) {
  const dom = new JSDOM(readFileSync(file, 'utf8'), {
    url: pathToFileURL(file).href,
    runScripts: 'dangerously',
    pretendToBeVisual: true,
  });
  const deny = (name) => () => {
    throw new Error(`${name} was used — the bundle must not touch the network`);
  };
  dom.window.fetch = deny('fetch');
  dom.window.XMLHttpRequest = deny('XMLHttpRequest');
  dom.window.WebSocket = deny('WebSocket');
  return dom;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

beforeAll(() => {
  const pages = KINDS.map((kind, index) => ({
    path: `${kind}/page-${index}.md`,
    title: `Page ${kind}`,
    kind,
    domain: 'cortex',
    tags: ['shared'],
    maturity: index % 2 ? 'draft' : 'live',
  }));
  const built = buildBundle(pages);
  outDir = built.dir;
  artifact = built.manifest.path;
});

afterAll(() => {
  if (outDir) rmSync(outDir, { recursive: true, force: true });
});

describe('the built static wiki bundle', () => {
  it('carries no remote script or style reference', () => {
    const html = readFileSync(artifact, 'utf8');
    const quotedRemote = [...html.matchAll(/(['"])(https?:\/\/[^'"\s]+)\1/g)]
      .map((m) => m[2])
      // An XML namespace is an identifier, not a fetch.
      .filter((url) => !url.startsWith('http://www.w3.org/'));

    expect(quotedRemote).toEqual([]);
    expect(html).not.toMatch(/<script[^>]+src\s*=\s*["']https?:/i);
    expect(html).not.toMatch(/<link[^>]+href\s*=\s*["']https?:/i);
  });

  it('opens from file:// and installs the offline transport', async () => {
    const dom = launch(artifact);
    await settle();

    expect(dom.window.location.protocol).toBe('file:');
    expect(typeof dom.window.JUG._wikiTransport).toBe('function');
    dom.window.close();
  });

  it('answers the wiki view from the inlined payload, not the network', async () => {
    const dom = launch(artifact);
    await settle();

    const response = await dom.window.JUG._wikiTransport('/api/wiki/list');
    const body = await response.json();

    expect(response.ok).toBe(true);
    expect(body.pages.map((p) => p.kind).sort()).toEqual([...KINDS].sort());
    dom.window.close();
  });

  it('renders the whole 10-kind taxonomy and both maturity values', async () => {
    // Criterion 4, asserted on the SETS rather than a screenshot: the export
    // renders through wiki.js itself, so what this pins is that the payload
    // carries every kind and maturity the served view would have shown.
    const dom = launch(artifact);
    await settle();

    const pages = (await (
      await dom.window.JUG._wikiTransport('/api/wiki/list')
    ).json()).pages;

    expect(new Set(pages.map((p) => p.kind))).toEqual(new Set(KINDS));
    expect(new Set(pages.map((p) => p.maturity))).toEqual(new Set(['live', 'draft']));
    dom.window.close();
  });

  it('serves every page body offline', async () => {
    const dom = launch(artifact);
    await settle();

    const url = `/api/wiki/page?path=${encodeURIComponent('adr/page-0.md')}`;
    const body = await (await dom.window.JUG._wikiTransport(url)).json();

    expect(body.body).toContain('Body of adr/page-0.md');
    dom.window.close();
  });

  it('answers graph mode offline for the toggle combinations a reader can reach', async () => {
    const dom = launch(artifact);
    await settle();

    for (const query of [
      'cooccur=0&domain=&xlens=1',
      'cooccur=1&domain=&xlens=1',
      'cooccur=0&domain=&xlens=0',
    ]) {
      const body = await (
        await dom.window.JUG._wikiTransport(`/api/wiki/graph?${query}`)
      ).json();
      expect(body.meta.schema).toBe('workflow_graph.v1');
      expect(body.unavailable).toBeUndefined();
    }
    dom.window.close();
  });

  it('refuses a save with a named reason instead of failing obscurely', async () => {
    const dom = launch(artifact);
    await settle();

    const response = await dom.window.JUG._wikiTransport('/api/wiki/save', {
      method: 'POST',
      body: '{}',
    });
    const body = await response.json();

    expect(response.status).toBe(405);
    expect(body.error).toContain('static export');
    expect(body.unavailable).toBe(true);
    dom.window.close();
  });

  it('marks anything absent from the payload as unavailable, not as empty data', async () => {
    // #119's marker, reused: the view renders its named error state for this
    // rather than mistaking the reply for a real but empty result.
    const dom = launch(artifact);
    await settle();

    const body = await (
      await dom.window.JUG._wikiTransport('/api/wiki/nope')
    ).json();

    expect(body.unavailable).toBe(true);
    dom.window.close();
  });

  it('names the capabilities it does not bundle', async () => {
    // mermaid and KaTeX both degrade to source text, and both degrade SILENTLY
    // in the served view. The bundle states the trade (§F2).
    const dom = launch(artifact);
    await settle();

    expect(dom.window.__CORTEX_WIKI_EXPORT__.omitted_capabilities)
      .toEqual(['mermaid diagrams', 'LaTeX math']);
    dom.window.close();
  });

  it('is byte-identical when built twice from the same input', () => {
    // Criterion 5. Built through the real exporter both times, into different
    // directories, so nothing about the path can leak into the bytes.
    const pages = [{ path: 'a.md', title: 'A', kind: 'adr', domain: 'd', tags: [] }];
    const first = buildBundle(pages);
    const second = buildBundle(pages);
    try {
      expect(readFileSync(second.manifest.path, 'utf8'))
        .toBe(readFileSync(first.manifest.path, 'utf8'));
    } finally {
      rmSync(first.dir, { recursive: true, force: true });
      rmSync(second.dir, { recursive: true, force: true });
    }
  });

  it('produces a valid bundle for an empty wiki, and says it is empty', () => {
    // Criterion 6 — no silent success: an empty wiki must still open, and must
    // report zero pages rather than looking like a build that half-worked.
    const built = buildBundle([]);
    try {
      expect(built.manifest.page_count).toBe(0);
      expect(built.manifest.remote_references).toEqual([]);
      const dom = launch(built.manifest.path);
      expect(dom.window.__CORTEX_WIKI_EXPORT__.page_count).toBe(0);
      dom.window.close();
    } finally {
      rmSync(built.dir, { recursive: true, force: true });
    }
  });
});
