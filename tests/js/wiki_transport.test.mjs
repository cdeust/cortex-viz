// The wiki view's transport port (ui/unified/js/wiki.js).
//
// Every wiki data access goes through `wikiFetch`. Its default adapter is plain
// HTTP, so the served view is unchanged; an installed adapter takes over every
// read, which is what lets the static export render through this same file
// instead of a copy of it. Overriding global `fetch` would have achieved the
// same effect and is what §7.2 refuses, so the seam is what these tests pin.
import { beforeEach, describe, expect, it } from 'vitest';
import { loadScript, makeJUG, REPO } from './helpers/load-globals.mjs';

let wikiFetch;

function mountDom() {
  document.body.innerHTML = `
    <div id="wiki-main"></div>
    <div id="graph-container" style="display:none"></div>
    <div id="wiki-tree"></div>
  `;
}

beforeEach(() => {
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  window.JUG.state = { activeView: 'wiki' };
  mountDom();
  loadScript('ui/unified/js/wiki.js');
  ({ wikiFetch } = window.JUG._wikiTest);
});

describe('the wiki transport port', () => {
  it('falls back to HTTP when no adapter is installed', async () => {
    const calls = [];
    globalThis.fetch = (url, options) => {
      calls.push([url, options]);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    };

    await wikiFetch('/api/wiki/list', { cache: 'no-store' });

    expect(calls).toEqual([['/api/wiki/list', { cache: 'no-store' }]]);
  });

  it('hands the request to an installed adapter instead of the network', async () => {
    const seen = [];
    globalThis.fetch = () => {
      throw new Error('the network must not be reached once an adapter exists');
    };
    window.JUG._wikiTransport = (url, options) => {
      seen.push([url, options]);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: 1 }) });
    };

    const response = await wikiFetch('/api/wiki/page?path=a.md', undefined);

    expect(seen).toEqual([['/api/wiki/page?path=a.md', undefined]]);
    expect(await response.json()).toEqual({ ok: 1 });
  });

  it('reads the adapter per call, so install order cannot matter', async () => {
    // wiki.js may be parsed before the page installs an adapter. Resolving the
    // adapter once at load would make that ordering silently decide the outcome.
    globalThis.fetch = () =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ via: 'http' }) });

    const first = await (await wikiFetch('/api/wiki/list')).json();
    window.JUG._wikiTransport = () =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ via: 'adapter' }) });
    const second = await (await wikiFetch('/api/wiki/list')).json();

    expect(first).toEqual({ via: 'http' });
    expect(second).toEqual({ via: 'adapter' });
  });

  it('passes a write through the same port so an adapter can refuse it', async () => {
    // /api/wiki/save is a write. It goes through the port too, so a read-only
    // adapter can answer with a named refusal rather than letting the request
    // fail as an obscure network error.
    const seen = [];
    window.JUG._wikiTransport = (url, options) => {
      seen.push([url, options.method]);
      return Promise.resolve({ ok: false, status: 405 });
    };

    const response = await wikiFetch('/api/wiki/save', {
      method: 'POST',
      body: '{}',
    });

    expect(seen).toEqual([['/api/wiki/save', 'POST']]);
    expect(response.ok).toBe(false);
  });

  it('routes every wiki data path through the port, not just the read paths', async () => {
    // A site left on bare `fetch` would work when served and silently fail in
    // the export, so the file is checked as a whole rather than per caller.
    const { readFileSync } = await import('node:fs');
    const { join } = await import('node:path');
    const source = readFileSync(join(REPO, 'ui/unified/js/wiki.js'), 'utf8');
    const bare = source
      .split('\n')
      .map((line, index) => [index + 1, line])
      .filter(([, line]) => /(^|[^k])fetch\(/.test(line));

    // Exactly one: the default adapter's own call inside wikiFetch.
    expect(bare.map(([lineNo]) => lineNo)).toHaveLength(1);
    expect(bare[0][1]).toContain('return fetch(url, options)');
  });
});
