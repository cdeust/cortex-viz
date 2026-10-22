// Behaviour tests for the Wiki view (ui/unified/js/wiki.js): index fetch, tree
// grouping/search/collapse, welcome panel, page rendering (header, banner,
// backlinks, inspector, memos), markdown renderer, academic passes, export
// download and graph mode. Only fetch, alert/confirm and URL blobs are stubbed.
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

const flush = async (n = 6) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
};
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const PAGES = [
  { path: 'reference/alpha/overview.md', title: 'alpha — Overview', kind: 'reference', domain: 'alpha', tags: ['core'], updated: '2026-02-01', maturity: 'stable' },
  { path: 'adr/alpha/10-choice.md', title: 'Choice', kind: 'adr', domain: 'alpha', tags: [], created: '2026-01-01' },
  { path: 'adr/alpha/20-choice.md', title: 'Choice', kind: 'adr', domain: 'alpha', tags: [] },
  { path: 'lesson/beta/1-x.md', title: 'Lesson X', kind: 'lesson', domain: 'beta', tags: ['t'] },
  { path: 'notes/2024/misc.md', title: 'Misc', domain: '2024' },
  { path: 'top.md', title: 'Top' },
];

let routes;
let calls;

function resp(body, init) {
  init = init || {};
  const headers = init.headers || {};
  return {
    ok: init.ok !== false,
    status: init.status || 200,
    headers: { get: (k) => headers[k] || null },
    json: () => Promise.resolve(body),
    blob: () => Promise.resolve(new Blob(['x'])),
  };
}

beforeEach(async () => {
  calls = [];
  routes = {
    '/api/wiki/list': () => ({ pages: PAGES }),
    '/api/wiki/projects': () => ({
      projects: [
        { domain: 'zeta', page_total: 1, scope_coverage_ratio: null },
        { domain: 'alpha', page_total: 4, scope_coverage_ratio: 0.5, file_coverage_ratio: 0.25, file_covered: 1, file_total: 4, missing_scopes: ['a', 'b', 'c', 'd'] },
        { domain: 'beta', page_total: 2, scope_coverage_ratio: 0.1 },
        { domain: 'eta', page_total: 1, scope_coverage_ratio: null },
      ],
    }),
    '/api/wiki/page_meta': () => null,
    '/api/wiki/page': (u) => ({ path: decodeURIComponent(u.split('path=')[1]), meta: { title: 'T' }, body: '# Hi\n\ntext' }),
  };
  globalThis.fetch = (url, opts) => {
    calls.push(url);
    const key = Object.keys(routes).find((k) => url.startsWith(k + '?') || url === k);
    if (!key) return Promise.resolve(resp({}, { ok: false, status: 404 }));
    const r = routes[key](url, opts);
    if (r instanceof Error) return Promise.reject(r);
    if (r && r.__raw) return Promise.resolve(r.__raw);
    if (r === null) return Promise.resolve(resp(null, { ok: false, status: 404 }));
    return Promise.resolve(resp(r));
  };
  document.body.innerHTML = '<div id="wiki-container"></div><div id="graph-container" style="display:none"></div>';
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  window.JUG.state = {};
  window.alert = vi.fn();
  window.confirm = vi.fn(() => true);
  globalThis.alert = window.alert;
  globalThis.confirm = window.confirm;
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  // Capture the init hook instead of dispatching DOMContentLoaded, so earlier
  // tests' module instances (their listeners stay on document) are not re-run.
  const origAdd = document.addEventListener;
  let initFn;
  document.addEventListener = function (t, f, o) {
    if (t === 'DOMContentLoaded') initFn = f;
    else origAdd.call(document, t, f, o);
  };
  loadScript('ui/unified/js/wiki.js');
  document.addEventListener = origAdd;
  initFn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function open() {
  window.JUG.emit('state:activeView', 'wiki');
  await flush();
}

describe('index + tree', () => {
  it('groups by domain with catch-all buckets last and counts', async () => {
    await open();
    expect($('#wiki-container').style.display).toBe('block');
    const labels = $$('.wiki-tree-kind-label').map((l) => l.textContent);
    expect(labels).toEqual(['alpha', 'beta', '_general', '2024']);
    const alpha = $$('.wiki-tree-section')[0];
    expect(alpha.querySelector('.wiki-tree-count').textContent).toBe('3');
    // Duplicated titles collapse to one row with a count badge; newest id wins.
    const dup = alpha.querySelector('.wiki-tree-dup-badge');
    expect(dup.textContent).toBe('×2');
    expect(dup.parentElement.dataset.path).toBe('adr/alpha/20-choice.md');
    // The domain prefix of a title is trimmed from the row.
    expect(alpha.querySelector('[data-path="reference/alpha/overview.md"] .wiki-tree-item-label').textContent).toBe('Overview');
  });

  it('collapses and expands domains and kinds on header click', async () => {
    await open();
    const section = $$('.wiki-tree-section')[0];
    const header = section.querySelector('.wiki-tree-kind');
    const items = section.querySelector('.wiki-tree-items');
    header.click();
    expect(items.classList.contains('collapsed')).toBe(true);
    header.click();
    expect(items.classList.contains('collapsed')).toBe(false);
    const kindHeader = section.querySelector('.wiki-tree-domain');
    const kindItems = section.querySelector('.wiki-tree-domain-items');
    kindHeader.click();
    expect(kindItems.classList.contains('collapsed')).toBe(true);
    kindHeader.click();
    expect(kindItems.classList.contains('collapsed')).toBe(false);
    // State survives a rebuild of the tree (search keeps expanded flags).
    header.click();
    const input = $('.wiki-search');
    vi.useFakeTimers();
    input.value = 'alpha';
    input.dispatchEvent(new window.Event('input'));
    vi.advanceTimersByTime(300);
    vi.useRealTimers();
    expect($('.wiki-tree-items').classList.contains('collapsed')).toBe(true);
  });

  it('filters by search and shows an empty message', async () => {
    await open();
    const input = $('.wiki-search');
    vi.useFakeTimers();
    input.value = 'lesson';
    input.dispatchEvent(new window.Event('input'));
    vi.advanceTimersByTime(300);
    expect($$('.wiki-tree-kind-label').map((l) => l.textContent)).toEqual(['beta']);
    input.value = 'zzzz';
    input.dispatchEvent(new window.Event('input'));
    vi.advanceTimersByTime(300);
    vi.useRealTimers();
    expect($('.wiki-tree-empty').textContent).toBe('No pages match "zzzz"');
  });

  it('reports no pages found for an empty index', async () => {
    routes['/api/wiki/list'] = () => ({ pages: [] });
    await open();
    expect($('.wiki-tree-empty').textContent).toBe('No pages found');
    expect($('.wiki-welcome-subtitle').textContent).toBe('0 pages across 0 categories');
  });

  it('shows an error state when the index fails, and retries on re-show', async () => {
    routes['/api/wiki/list'] = () => null;
    await open();
    expect($('.wiki-error-title').textContent).toBe('Wiki unavailable');
    routes['/api/wiki/list'] = () => ({ pages: PAGES });
    window.JUG.emit('state:activeView', 'graph');
    expect($('#wiki-container').style.display).toBe('none');
    await open();
    expect($$('.wiki-tree-section').length).toBe(4);
  });

  it('rebuilds layout without refetching the index when shown again', async () => {
    await open();
    window.JUG.emit('state:activeView', 'other');
    await open();
    expect(calls.filter((c) => c === '/api/wiki/list').length).toBe(1);
    expect($('.wiki-layout')).not.toBeNull();
  });

  it('alt-click on a duplicate opens a sibling picker that loads the chosen page', async () => {
    await open();
    const row = $('.wiki-tree-dup-badge').parentElement;
    row.dispatchEvent(new window.MouseEvent('click', { bubbles: true, altKey: true }));
    const rows = $$('.wiki-sibling-row');
    expect(rows.map((r) => r.textContent)).toEqual(['adr/alpha/20-choice.md', 'adr/alpha/10-choice.md']);
    // A second alt-click replaces the picker rather than stacking one.
    row.dispatchEvent(new window.MouseEvent('click', { bubbles: true, altKey: true }));
    expect($$('.wiki-sibling-picker').length).toBe(1);
    $$('.wiki-sibling-row')[1].click();
    await flush();
    expect($('.wiki-sibling-picker')).toBeNull();
    expect(calls.some((c) => c.includes('path=adr%2Falpha%2F10-choice.md'))).toBe(true);
  });

  it('closes the sibling picker on an outside click', async () => {
    await open();
    $('.wiki-tree-dup-badge').parentElement.dispatchEvent(new window.MouseEvent('click', { bubbles: true, altKey: true }));
    await flush(2);
    document.body.click();
    expect($('.wiki-sibling-picker')).toBeNull();
  });
});

describe('welcome panel', () => {
  it('lists kinds, recent pages and core knowledge', async () => {
    await open();
    expect($('.wiki-welcome-title').textContent).toBe('Knowledge Base');
    expect($('.wiki-welcome-subtitle').textContent).toBe('6 pages across 4 categories');
    const kindLabels = $$('.wiki-welcome-kinds')[1] ? $$('.wiki-welcome-kinds')[1].textContent : '';
    expect(kindLabels).toContain('Architecture Decisions');
    expect(kindLabels).toContain('reference');
    expect(kindLabels).toContain('Miscellaneous');
    const recent = $$('.wiki-welcome-list')[0].querySelectorAll('.wiki-welcome-list-item');
    expect(recent.length).toBe(6);
    expect(recent[0].querySelector('.wiki-welcome-list-title').textContent).toBe('alpha — Overview');
    expect(recent[0].querySelector('.wiki-welcome-list-meta').textContent).toBe('reference · alpha · 2026-02-01');
    expect($('.wiki-welcome-list-item--stable .wiki-mat-stable').textContent).toBe('Stable');
    recent[1].click();
    await flush();
    expect(calls.some((c) => c.startsWith('/api/wiki/page?'))).toBe(true);
  });

  it('renders project cards sorted by coverage and filters the tree on click', async () => {
    await open();
    const cards = $$('#wiki-projects-grid .wiki-welcome-kind-card');
    expect(cards.map((c) => c.querySelector('.wiki-welcome-kind-label').textContent)).toEqual(['beta', 'alpha', 'eta', 'zeta']);
    const alpha = cards[1];
    expect(alpha.querySelector('.wiki-welcome-kind-badges').textContent).toBe('scope: 50% • files: 25% (1/4) • missing: a, b, c…');
    alpha.click();
    expect($('.wiki-search').value).toBe('alpha');
    expect($$('.wiki-tree-kind-label').map((l) => l.textContent)).toEqual(['alpha']);
  });

  it('shows empty and failure states for the project grid', async () => {
    routes['/api/wiki/projects'] = () => ({ projects: [] });
    await open();
    expect($('#wiki-projects-grid').textContent).toBe('No projects detected yet.');
    routes['/api/wiki/projects'] = () => null;
    window.JUG.emit('state:activeView', 'x');
    await open();
    expect($('#wiki-projects-grid').textContent).toBe('Could not load project index.');
  });
});

describe('page rendering', () => {
  const META = {
    title: 'Full Page',
    kind: 'adr',
    domain: 'alpha',
    maturity: 'stable',
    created: '2026-01-01',
    updated: '2026-02-02',
    tags: ['x', 'y'],
    curation_gaps: ['purpose', 'public-api', 'custom-gap'],
  };
  const PMETA = {
    db_row: { id: 7, lifecycle_state: 'cooling', is_stale: true, heat: 0.456, citation_count: 3, backlink_count: 2, status: 'ok', planted: 'p', tended: 't', archived_at: 'a', memory_id: 'mem', concept_id: 'con' },
    backlinks: [
      { src_title: 'Other', src_rel_path: 'adr/alpha/10-choice.md', link_kind: 'depends' },
      { src_rel_path: '' },
    ],
  };

  function servePage(body, meta, pmeta) {
    routes['/api/wiki/page'] = (u) => ({ path: decodeURIComponent(u.split('path=')[1]), meta: meta || { title: 'T' }, body });
    routes['/api/wiki/page_meta'] = () => pmeta || null;
  }
  async function loadFirst() {
    await open();
    $('.wiki-tree-item[data-path="lesson/beta/1-x.md"]').click();
    await flush();
  }

  it('renders header, pills, heat bar, meta bar, curation banner and backlinks', async () => {
    servePage('Body para', META, PMETA);
    await loadFirst();
    expect($('.wiki-breadcrumb').textContent).toBe('Architecture Decisions/alpha');
    expect($('.wiki-page-title').textContent).toBe('Full Page');
    expect($('.wiki-mat-pill').textContent).toBe('Stable');
    expect($('.wiki-kind-pill').textContent).toBe('Architecture Decisions');
    expect($('.wiki-lc-cooling').textContent).toBe('cooling');
    expect($('.wiki-stale-pill').textContent).toBe('stale');
    expect($('.wiki-heat-fill').style.width).toBe('45.6%');
    expect($('.wiki-heat-label').textContent).toBe('heat 0.46 · cited 3 · 2 backlinks');
    expect($$('.wiki-meta-label').map((l) => l.textContent)).toEqual(['Created', 'Updated']);
    expect($$('.wiki-tag').length).toBe(2);
    expect($('.wiki-curation-summary').textContent).toContain('Page 79% curated — 3 of 14');
    expect($$('.wiki-curation-list li').map((l) => l.textContent)[0]).toMatch(/^Purpose/);
    expect($$('.wiki-curation-list li')[2].textContent).toBe('custom-gap');
    expect($('.wiki-backlinks-title').textContent).toBe('Backlinks (2)');
    expect($$('.wiki-backlinks-item').length).toBe(2);
    expect($('.wiki-backlinks-item .wiki-link-kind').textContent).toBe('depends');
    $('.wiki-backlinks-item a').click();
    await flush();
    expect(calls.some((c) => c.includes('path=adr%2Falpha%2F10-choice.md'))).toBe(true);
  });

  it('falls back to draft maturity, shows render and not-found errors', async () => {
    servePage('x', { title: 'Plain' });
    await loadFirst();
    expect($('.wiki-mat-pill').textContent).toBe('Draft');
    expect($('.wiki-kind-pill')).toBeNull();
    expect($('.wiki-heat-bar')).toBeNull();
    routes['/api/wiki/page'] = () => ({ error: 'nope' });
    $('.wiki-tree-item').click();
    await flush();
    expect($('.wiki-error-title').textContent).toBe('Page not found');
    routes['/api/wiki/page'] = () => null;
    $('.wiki-tree-item').click();
    await flush();
    expect($('.wiki-error-sub').textContent).toContain('Could not load');
  });

  it('reports a render error when the page payload is malformed', async () => {
    servePage('x', { title: 'Plain', tags: 'nope' });
    routes['/api/wiki/page'] = () => ({ path: 'p', meta: { curation_gaps: [1], created: {} }, body: 5 });
    await open();
    $('.wiki-tree-item').click();
    await flush();
    expect($('.wiki-error-title').textContent).toBe('Render error');
  });

  it('inspector loads memos lazily on first open only', async () => {
    servePage('x', META, PMETA);
    routes['/api/wiki/memos'] = () => ({ memos: [{ decision: 'Keep', rationale: 'why', author: 'bob', created_at: 'now' }, { decision: 'Drop' }] });
    await loadFirst();
    const details = $('.wiki-inspector');
    expect($$('.wiki-inspect-label').map((l) => l.textContent)).toEqual(
      expect.arrayContaining(['page id', 'archived_at', 'memory_id', 'concept_id'])
    );
    expect($('.wiki-inspector-memos').textContent).toBe('Loading…');
    details.open = true;
    details.dispatchEvent(new window.Event('toggle'));
    await flush();
    expect($$('.wiki-memo-entry').length).toBe(2);
    expect($('.wiki-memo-author').textContent).toBe('bob · now');
    details.dispatchEvent(new window.Event('toggle'));
    await flush();
    expect(calls.filter((c) => c.startsWith('/api/wiki/memos')).length).toBe(1);
  });

  it('inspector shows empty and failed memo states', async () => {
    servePage('x', META, PMETA);
    routes['/api/wiki/memos'] = () => ({ memos: [] });
    await loadFirst();
    let d = $('.wiki-inspector');
    d.open = true;
    d.dispatchEvent(new window.Event('toggle'));
    await flush();
    expect($('.wiki-inspector-memos').textContent).toBe('No memos yet.');
    routes['/api/wiki/memos'] = () => new Error('x');
    $('.wiki-tree-item').click();
    await flush();
    d = $('.wiki-inspector');
    d.open = true;
    d.dispatchEvent(new window.Event('toggle'));
    await flush();
    expect($('.wiki-inspector-memos').textContent).toBe('Failed to load memos.');
  });

  it('wiki links navigate, search, or fall back to search when the page is absent', async () => {
    servePage('See [[adr/alpha/10-choice]] and [[nowhere/else|Gone]] and [[bare]] and [rel](x/y.md)', META, null);
    await loadFirst();
    const links = $$('.wiki-body .wiki-link');
    expect(links.map((l) => l.textContent)).toEqual(['adr/alpha/10-choice', 'Gone', 'bare', 'rel']);
    expect(links[0].dataset.path).toBe('adr/alpha/10-choice.md');
    links[0].click();
    await flush();
    expect(calls.filter((c) => c.includes('path=adr%2Falpha%2F10-choice.md')).length).toBeGreaterThan(0);
    // After navigation the body is re-rendered; use fresh links.
    servePage('See [[nowhere/else|Gone]] and [[bare]]', META, null);
    $('.wiki-tree-item').click();
    await flush();
    $$('.wiki-body .wiki-link')[0].click();
    expect($('.wiki-search').value).toBe('nowhere/else');
    $$('.wiki-body .wiki-link')[1].click();
    expect($('.wiki-search').value).toBe('bare');
    expect($('.wiki-tree-empty')).not.toBeNull();
  });

  it('runs mermaid and KaTeX hooks, tolerating an unavailable mermaid CDN', async () => {
    const math = vi.fn();
    window.renderMathInElement = math;
    servePage('```mermaid\ngraph TD; A-->B\n```\n\n$x$', META, null);
    await loadFirst();
    expect($('.wiki-mermaid').textContent).toBe('graph TD; A-->B');
    expect(math).toHaveBeenCalled();
    window.renderMathInElement = () => {
      throw new Error('katex');
    };
    $('.wiki-tree-item').click();
    await flush();
    expect($('.wiki-body')).not.toBeNull();
    delete window.renderMathInElement;
  });

  it('numbers headings, figures, tables, equations and resolves cross refs', async () => {
    servePage('# One\n## Two\n# Three\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nSee {@tbl:t} and {@nope} end.', META, null);
    await loadFirst();
    expect($$('.wiki-section-num').map((s) => s.textContent)).toEqual(['1 ', '1.1 ', '2 ']);
    expect($('table').getAttribute('data-num')).toBe('1');
    const x = $$('.wiki-xref').map((a) => a.textContent);
    expect(x).toEqual(['?tbl:t', '?nope']);
  });

  it('labels figures and resolves references to them; opt-out disables numbering', async () => {
    servePage('x', { title: 'F', section_numbering: false }, null);
    routes['/api/wiki/page'] = () => ({
      path: 'p.md',
      meta: { title: 'F', section_numbering: false },
      body: '# Head\n\n<figure data-label="fig1"><figcaption>Cap</figcaption></figure>',
    });
    await loadFirst();
    expect($('.wiki-section-num')).toBeNull();
  });

  it('citations degrade to a console warning when Citation.js cannot load', async () => {
    routes['/api/wiki/bibliography'] = () => ({ files: [{ path: 'a.bib' }] });
    routes['/api/wiki/bibliography/read'] = () => ({ content: '' });
    servePage('Claim [@smith2020] and [@a; @b].', META, null);
    await loadFirst();
    await flush(10);
    expect(console.warn).toHaveBeenCalled();
    servePage('Claim [@k].', Object.assign({}, META, { bibliography: [] }), null);
    $('.wiki-tree-item').click();
    await flush(10);
    expect($('.wiki-body')).not.toBeNull();
  });
});

describe('markdown renderer via the page body', () => {
  async function body(md) {
    routes['/api/wiki/page'] = () => ({ path: 'p.md', meta: { title: 'M', section_numbering: false }, body: md });
    await open();
    $('.wiki-tree-item').click();
    await flush();
    return $('.wiki-body');
  }

  it('renders headings with slug ids, hr, blockquote, lists, inline formats, images, links', async () => {
    const b = await body([
      '# Hello *World*', '---', '> quote a', '> quote b', '',
      '- one', '* two', '+ three', '', '1. first', '2) second', '',
      'para with **bold** __b2__ *it* _i2_ ***both*** `code` <tag>', '',
      '![alt](img.png) [ext](https://x.dev) [rel](a/b.md)',
    ].join('\n'));
    expect(b.querySelector('h1').id).toBe('hello-world');
    expect(b.querySelector('h1 em').textContent).toBe('World');
    expect(b.querySelector('hr')).not.toBeNull();
    expect(b.querySelector('blockquote').innerHTML).toBe('quote a<br>quote b');
    expect(b.querySelectorAll('ul li').length).toBe(3);
    expect(b.querySelectorAll('ol li').length).toBe(2);
    expect(b.querySelectorAll('strong').length).toBeGreaterThanOrEqual(3);
    expect(b.querySelector('strong em').textContent).toBe('both');
    expect(b.querySelector('p code').textContent).toBe('code');
    expect(b.textContent).toContain('<tag>');
    expect(b.querySelector('img.wiki-img').getAttribute('src')).toBe('img.png');
    expect(b.querySelector('a[target="_blank"]').getAttribute('href')).toBe('https://x.dev');
  });

  it('renders tables with header and body, closing before other blocks', async () => {
    const b = await body('| H1 | H2 |\n| --- | --- |\n| a | b |\n| c | d |\nafter\n\n- l\n| solo |\n');
    expect(b.querySelectorAll('table')[0].querySelectorAll('thead th').length).toBe(2);
    expect(b.querySelectorAll('table')[0].querySelectorAll('tbody tr').length).toBe(2);
    expect(b.querySelectorAll('table').length).toBe(2);
    expect(b.querySelector('table td').textContent).toBe('a');
  });

  it('renders fenced code with language, unclosed fences and bare JSON blocks', async () => {
    const b = await body('```js\nvar a = "<";\n```\n\n- item\n```\nunclosed code\n\n{\n  "a": [1,\n  2]\n}\n\n[1, 2]\n\ntail');
    expect(b.querySelector('code.lang-js').textContent).toBe('var a = "<";');
    expect(b.querySelector('code').textContent).toContain('var a');
    expect(b.textContent).toContain('unclosed code');
    const b2 = await body('{\n "a": 1\n}\n\n[1, 2]\n\ntail');
    expect(b2.querySelectorAll('code.lang-json').length).toBe(2);
    expect(b2.querySelector('p').textContent).toBe('tail');
  });
});

describe('export download', () => {
  async function openExport() {
    routes['/api/wiki/page'] = () => ({ path: 'a/b/page.md', meta: { title: 'E' }, body: 'x' });
    await open();
    $('.wiki-tree-item').click();
    await flush();
  }
  const btn = (f) => $$('.wiki-export-btn').find((b) => b.textContent.startsWith(f));

  it('downloads a blob using the server filename', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    const clicked = [];
    const orig = window.HTMLAnchorElement.prototype.click;
    window.HTMLAnchorElement.prototype.click = function () {
      clicked.push(this.download);
    };
    routes['/api/wiki/export'] = () => ({ __raw: resp(null, { headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="named.pdf"' } }) });
    await openExport();
    btn('PDF').click();
    expect(btn('PDF').disabled).toBe(true);
    await flush();
    expect(clicked).toEqual(['named.pdf']);
    expect(btn('PDF').disabled).toBe(false);
    expect(btn('PDF').textContent).toBe('PDF');
    // No Content-Disposition: filename is derived from the page path.
    routes['/api/wiki/export'] = () => ({ __raw: resp(null, { headers: { 'Content-Type': 'application/x' } }) });
    btn('TEX').click();
    await flush();
    expect(clicked[1]).toBe('page.tex');
    await new Promise((r) => setTimeout(r, 250));
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    window.HTMLAnchorElement.prototype.click = orig;
  });

  it('alerts with server JSON errors, HTTP failures and network errors', async () => {
    await openExport();
    routes['/api/wiki/export'] = () => ({ __raw: resp({ error: 'no pandoc', stderr: 'oops' }, { headers: { 'Content-Type': 'application/json' } }) });
    btn('DOCX').click();
    await flush();
    expect(window.alert.mock.calls[0][0]).toContain('no pandoc');
    expect(window.alert.mock.calls[0][0]).toContain('oops');
    routes['/api/wiki/export'] = () => ({ __raw: resp({}, { headers: { 'Content-Type': 'application/json' } }) });
    btn('DOCX').click();
    await flush();
    expect(window.alert.mock.calls[1][0]).toContain('export failed');
    routes['/api/wiki/export'] = () => ({ __raw: resp(null, { ok: false, status: 500 }) });
    btn('HTML').click();
    await flush();
    expect(window.alert.mock.calls[2][0]).toContain('HTTP 500');
    routes['/api/wiki/export'] = () => new Error('net down');
    btn('HTML').click();
    await flush();
    expect(window.alert.mock.calls[3][0]).toContain('net down');
  });
});

describe('editor entry', () => {
  it('restores the page and alerts when the editor modules cannot load', async () => {
    routes['/api/wiki/page'] = () => ({ path: 'p.md', meta: { title: 'Ed' }, body: 'text' });
    await open();
    $('.wiki-tree-item').click();
    await flush();
    $('.wiki-edit-btn').click();
    expect($('#wiki-main').textContent).toContain('Loading editor');
    await flush(10);
    expect(window.alert).toHaveBeenCalledWith('Editor failed to load. See console for details.');
    expect($('.wiki-page-title').textContent).toBe('Ed');
  });
});

describe('graph mode', () => {
  const clickMode = (t) => $$('.wiki-mode-btn').find((b) => b.textContent === t).click();
  const toggle = (t) => $$('.wiki-toggle').find((l) => l.textContent === t).querySelector('input');

  it('enters graph mode, requests the graph with toggles, and mounts it', async () => {
    routes['/api/wiki/graph'] = () => ({ nodes: [], edges: [], meta: {} });
    await open();
    clickMode('Graph');
    await flush();
    expect($('#graph-container').style.display).toBe('block');
    const url = calls.find((c) => c.startsWith('/api/wiki/graph'));
    expect(url).toBe('/api/wiki/graph?domain=alpha&cooccur=0&xlens=1');
    expect(window.JUG.state.lastData).toEqual({ nodes: [], edges: [], meta: {} });
    // Clicking the already-active mode is a no-op.
    const n = calls.length;
    clickMode('Graph');
    expect(calls.length).toBe(n);
    toggle('co-occurrence').checked = true;
    toggle('co-occurrence').dispatchEvent(new window.Event('change'));
    await flush();
    expect(calls.pop()).toContain('cooccur=1');
    toggle('cross-lens').checked = false;
    toggle('cross-lens').dispatchEvent(new window.Event('change'));
    await flush();
    expect(calls.pop()).toContain('xlens=0');
    // Hiding the lens hides the graph host; so does switching back to tree.
    window.JUG.emit('state:activeView', 'other');
    expect($('#graph-container').style.display).toBe('none');
  });

  it('switching back to tree hides the host and shows the welcome panel', async () => {
    routes['/api/wiki/graph'] = () => ({ nodes: [] });
    await open();
    clickMode('Graph');
    await flush();
    clickMode('Tree');
    expect($('#graph-container').style.display).toBe('none');
    expect($('.wiki-welcome')).not.toBeNull();
    // Toggles in tree mode do not fetch the graph.
    const n = calls.length;
    toggle('cross-lens').dispatchEvent(new window.Event('change'));
    expect(calls.length).toBe(n);
  });

  it('uses the active page domain for the graph query', async () => {
    routes['/api/wiki/graph'] = () => ({ nodes: [] });
    await open();
    $('.wiki-tree-item[data-path="lesson/beta/1-x.md"]').click();
    await flush();
    clickMode('Graph');
    await flush();
    expect(calls.find((c) => c.startsWith('/api/wiki/graph'))).toContain('domain=beta');
  });

  it('says so when the graph is empty, unavailable, errored or HTTP-failed', async () => {
    await open();
    routes['/api/wiki/graph'] = () => ({ meta: { empty: true } });
    clickMode('Graph');
    await flush();
    expect($('.wiki-error-title').textContent).toBe('Nothing to graph');
    expect($('#graph-container').style.display).toBe('none');
    const enter = window.JUG._wikiTest.enterGraphMode;
    routes['/api/wiki/graph'] = () => ({ unavailable: true });
    enter();
    await flush();
    expect($('.wiki-error-title').textContent).toBe('Graph unavailable');
    expect($('.wiki-error-sub').textContent).toContain('not served');
    routes['/api/wiki/graph'] = () => ({ error: 'bad domain' });
    enter();
    await flush();
    expect($('.wiki-error-sub').textContent).toContain('bad domain');
    routes['/api/wiki/graph'] = () => null;
    enter();
    await flush();
    expect($('.wiki-error-sub').textContent).toContain('HTTP 404');
  });
});
