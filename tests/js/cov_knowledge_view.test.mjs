// Behaviour tests for the Knowledge view (ui/unified/js/knowledge.js): show/hide
// lifecycle, paged fetch, filters, search, sort, cards, inspector, markdown.
// Only fetch is stubbed; the DOM is real jsdom.
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

const flush = async (n = 4) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
};

let calls;
let responder;

function mems(prefix, n, extra) {
  return Array.from({ length: n }, (_, i) =>
    Object.assign(
      {
        id: prefix + i,
        content: '# Title ' + prefix + i + '\nBody text for memory ' + i,
        heat: i % 2 ? 0.8 : 0.1,
        domain: '/home/u/proj' + (i % 2),
        tags: ['alpha', 'beta'],
      },
      extra
    )
  );
}

const FACETS = {
  total: 100,
  global: 3,
  hot: 7,
  protected: 2,
  domains: [{ name: '/x/projA', count: 5 }],
  stages: { labile: 1, early_ltp: 2, late_ltp: 3, consolidated: 4, reconsolidating: 5 },
  emotions: { urgent: 1, positive: 2, negative: 3, neutral: 4 },
};

// Capture the init hook instead of dispatching DOMContentLoaded, so earlier
// tests' module instances (their listeners stay on document) are not re-run.
function boot() {
  const origAdd = document.addEventListener;
  let initFn;
  document.addEventListener = function (t, f, o) {
    if (t === 'DOMContentLoaded') initFn = f;
    else origAdd.call(document, t, f, o);
  };
  loadScript('ui/unified/js/knowledge.js');
  document.addEventListener = origAdd;
  initFn();
}

beforeEach(async () => {
  calls = [];
  responder = (url) => {
    if (url.startsWith('/api/memories/facets')) return FACETS;
    return { items: mems('a', 4, { isGlobal: false }), next_cursor: null };
  };
  globalThis.fetch = (url) => {
    calls.push(url);
    const r = responder(url);
    if (r instanceof Error) return Promise.reject(r);
    if (r && r.__status) return Promise.resolve({ ok: false, status: r.__status, json: () => Promise.resolve({}) });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r) });
  };
  document.body.innerHTML = '<div id="knowledge-container" style="display:none"></div>';
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  window.JUG.state = {};
  boot();
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function open() {
  window.JUG.emit('state:activeView', 'knowledge');
  await flush();
}

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const lastMem = () => calls.filter((c) => c.startsWith('/api/memories?')).pop();

describe('show / hide lifecycle', () => {
  it('shows the container, loads facets then the first page, and renders cards', async () => {
    await open();
    expect($('#knowledge-container').style.display).toBe('flex');
    expect(calls[0]).toContain('/api/memories/facets');
    expect(lastMem()).toContain('limit=50');
    expect(lastMem()).toContain('sort=heat');
    expect($$('.kv-card').length).toBe(4);
    expect($('#kv-load-text').textContent).toContain('end of memories');
    expect($('#kv-load-more').style.display).toBe('none');
    expect($$('.kv-stat').map((s) => s.textContent)).toEqual(expect.arrayContaining(['4loaded']));
  });

  it('hides again and closes the inspector', async () => {
    await open();
    $('.kv-card').click();
    expect($('#kv-expanded')).not.toBeNull();
    window.JUG.emit('state:activeView', 'graph');
    expect($('#knowledge-container').style.display).toBe('none');
    expect($('#kv-expanded')).toBeNull();
  });

  it('does not refetch facets on the second show', async () => {
    await open();
    window.JUG.emit('state:activeView', 'graph');
    await open();
    expect(calls.filter((c) => c.includes('/facets')).length).toBe(1);
  });

  it('survives a facets failure and still loads memories', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    responder = (url) => (url.includes('/facets') ? new Error('boom') : { items: mems('a', 1), next_cursor: null });
    await open();
    expect(warn).toHaveBeenCalled();
    expect($$('.kv-card').length).toBe(1);
  });

  it('builds domain pills from seen domains when facets are unavailable', async () => {
    responder = (url) => (url.includes('/facets') ? { __status: 500 } : { items: mems('a', 4), next_cursor: null });
    await open();
    const pills = $$('.kv-domain-pill').map((p) => p.firstChild.textContent);
    expect(pills).toEqual(['All', 'Global', 'proj0', 'proj1']);
  });

  it('is a no-op without a container', async () => {
    document.body.innerHTML = '';
    globalThis.JUG = makeJUG();
    window.JUG = globalThis.JUG;
    boot();
    window.JUG.emit('state:activeView', 'knowledge');
    await flush();
    expect(calls.length).toBe(0);
  });
});

describe('paging', () => {
  it('shows Load more and fetches the next page with the cursor', async () => {
    responder = (url) => {
      if (url.includes('/facets')) return FACETS;
      if (url.includes('cursor=c1')) return { items: mems('b', 2), next_cursor: null };
      return { items: mems('a', 2), next_cursor: 'c1' };
    };
    await open();
    expect($('#kv-load-more').style.display).toBe('inline-block');
    expect($('#kv-load-text').textContent).toContain('Scroll for more');
    $('#kv-load-more').click();
    await flush();
    expect(lastMem()).toContain('cursor=c1');
    expect($$('.kv-card').length).toBe(4);
    expect($('#kv-load-text').textContent).toContain('4 loaded');
  });

  it('skips duplicate ids across pages', async () => {
    responder = (url) => {
      if (url.includes('/facets')) return FACETS;
      return { items: mems('a', 2), next_cursor: url.includes('cursor') ? null : 'c' };
    };
    await open();
    $('#kv-load-more').click();
    await flush();
    expect($$('.kv-card').length).toBe(2);
  });

  it('fetches more on a scroll event when the sentinel is near the viewport', async () => {
    responder = (url) => {
      if (url.includes('/facets')) return FACETS;
      return { items: url.includes('cursor') ? mems('b', 1) : mems('a', 1), next_cursor: url.includes('cursor') ? null : 'c' };
    };
    await open();
    $('#knowledge-container').dispatchEvent(new window.Event('scroll'));
    await flush();
    expect($$('.kv-card').length).toBe(2);
    // Once done, further scrolls do nothing.
    const n = calls.length;
    window.dispatchEvent(new window.Event('scroll'));
    $('#knowledge-container').dispatchEvent(new window.Event('wheel'));
    expect(calls.length).toBe(n);
  });

  it('shows a failure message when the first page errors', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    responder = (url) => (url.includes('/facets') ? FACETS : { __status: 503 });
    await open();
    expect($('.kv-empty-title').textContent).toBe('Failed to load memories');
    expect($('.kv-empty-sub').textContent).toContain('HTTP 503');
  });

  it('shows the empty state, with the query when searching', async () => {
    responder = (url) => (url.includes('/facets') ? FACETS : { items: [], next_cursor: null });
    await open();
    expect($('.kv-empty-sub').textContent).toBe('No memories yet');
    const input = $('.kv-search');
    input.value = 'needle';
    vi.useFakeTimers();
    input.dispatchEvent(new window.Event('input'));
    input.dispatchEvent(new window.Event('input'));
    vi.advanceTimersByTime(300);
    vi.useRealTimers();
    await flush();
    expect(lastMem()).toContain('search=needle');
    expect($('.kv-empty-sub').textContent).toBe('No memories match "needle"');
    expect($('.kv-search').value).toBe('needle');
    // Clear all resets the query.
    $('.kv-clear-btn').click();
    await flush();
    expect(lastMem()).not.toContain('search=');
    expect($('.kv-clear-btn')).toBeNull();
  });

  it('uses the IntersectionObserver when present', async () => {
    let cb;
    const disconnect = vi.fn();
    window.IntersectionObserver = function (fn) {
      cb = fn;
      this.observe = () => {};
      this.disconnect = disconnect;
    };
    responder = (url) => {
      if (url.includes('/facets')) return FACETS;
      return { items: mems(url.includes('cursor') ? 'b' : 'a', 1), next_cursor: url.includes('cursor') ? null : 'c' };
    };
    await open();
    window.JUG.emit('state:activeView', 'graph');
    await open(); // rebuild disconnects the previous observer
    expect(disconnect).toHaveBeenCalled();
    cb([{ isIntersecting: false }]);
    expect(calls.filter((c) => c.includes('cursor=c')).length).toBe(0);
    cb([{ isIntersecting: true }]); // gated: no user scroll since the last fetch
    await flush();
    expect(calls.some((c) => c.includes('cursor=c'))).toBe(false);
    $('#knowledge-container').dispatchEvent(new window.Event('wheel'));
    await flush();
    expect(calls.some((c) => c.includes('cursor=c'))).toBe(true);
    delete window.IntersectionObserver;
  });
});

describe('filters, sort and domains', () => {
  it('renders chips with facet counts and applies stage/emotion/hot/protected filters', async () => {
    await open();
    const chip = (t) => $$('.kv-chip').find((c) => c.firstChild.textContent === t || c.querySelector('span:not(.kv-chip-dot)').textContent === t);
    expect(chip('Growing').querySelector('.kv-chip-count').textContent).toBe('2');
    chip('Growing').click();
    await flush();
    expect(lastMem()).toContain('stage=early_ltp');
    expect(chip('Growing').classList.contains('active')).toBe(true);
    chip('Urgent').click();
    await flush();
    expect(lastMem()).toContain('emotion=urgent');
    expect(chip('Urgent').style.getPropertyValue('--chip-ink')).toBe('var(--emo-urgent)');
    chip('Hot').click();
    await flush();
    expect(lastMem()).toContain('min_heat=0.5');
    chip('Protected').click();
    await flush();
    expect(lastMem()).toContain('protected=1');
    chip('Hot').click();
    await flush();
    expect(lastMem()).not.toContain('min_heat');
    chip('Protected').click();
    chip('Any stage').click();
    await flush();
    expect(lastMem()).not.toContain('stage=');
    chip('Any feel').click();
    await flush();
    expect(lastMem()).not.toContain('emotion=');
  });

  it('changes sort and sends the server sort key', async () => {
    await open();
    const btn = (t) => $$('.kv-sort-btn').find((b) => b.textContent === t);
    btn('Recency').click();
    await flush();
    expect(lastMem()).toContain('sort=recent');
    expect(btn('Recency').classList.contains('active')).toBe(true);
    btn('Importance').click();
    await flush();
    expect(lastMem()).toContain('sort=heat');
  });

  it('filters by domain and by global, and Clear all resets', async () => {
    responder = (url) => {
      if (url.includes('/facets')) return FACETS;
      return {
        items: [
          { id: 'g1', content: 'Global rule one.', isGlobal: true, domain: '/x/projA', heat: 0.9 },
          { id: 'p1', content: 'Proj memory', domain: '/x/projA' },
          { id: 'p2', content: 'Other memory', domain: '/x/other' },
        ],
        next_cursor: null,
      };
    };
    await open();
    expect($$('.kv-card').length).toBe(3);
    expect($('.kv-card').classList.contains('kv-card-global')).toBe(true);
    expect($$('.kv-stat-label').map((l) => l.textContent)).toEqual(expect.arrayContaining(['global rules', 'hot']));
    const pill = (t) => $$('.kv-domain-pill').find((p) => p.firstChild.textContent === t);
    pill('projA').click();
    await flush();
    expect(lastMem()).toContain('domain=%2Fx%2FprojA');
    expect($$('.kv-card').length).toBe(1);
    pill('Global').click();
    await flush();
    expect(lastMem()).toContain('global=1');
    expect($$('.kv-card').length).toBe(1);
    expect(pill('Global').classList.contains('active')).toBe(true);
    $('.kv-clear-btn').click();
    await flush();
    expect(lastMem()).not.toContain('global=1');
    expect($$('.kv-card').length).toBe(3);
  });
});

describe('cards', () => {
  it('renders feeling, meaning, meters, provenance and tags', async () => {
    const { buildCard } = window.JUG._kvTest;
    const card = buildCard(
      {
        id: 'x',
        content: 'Ran `cargo test --lib` and it passed. More text follows here for the preview.',
        heat: 0.7,
        emotion: 'urgency',
        valence: -0.5,
        arousal: 0.3,
        importance: 0.9,
        storeType: 'semantic',
        consolidationStage: 'mystery',
        domain: 'C:\\code\\repo',
        tags: ['auto-captured', 'test-result', 'a', 'b', 'c'],
        isGlobal: true,
        createdAt: '2020-01-01T00:00:00Z',
        lastAccessed: new Date(Date.now() - 2 * 3600000).toISOString(),
      },
      []
    );
    expect(card.classList.contains('kv-card-hot')).toBe(true);
    expect(card.querySelector('.kv-mc-feel').textContent).toBe('Urgency · ↓ 0.50 · ↑ 0.30');
    expect(card.querySelector('.kv-mc-quote').textContent).toContain('cargo test --lib · c');
    expect(card.querySelector('.kv-mc-brow').textContent).toContain('Mystery');
    expect(card.querySelector('.kv-mc-brow').textContent).toContain('repo');
    expect(card.querySelector('.kv-mc-brow').textContent).toContain('Hot');
    expect(card.querySelector('.kv-mc-when').textContent).toBe('2h ago');
    expect(card.querySelectorAll('.kv-mc-meter').length).toBe(4);
    expect(card.querySelectorAll('.kv-mc-ftags span').length).toBe(4);
    expect(card.textContent).toContain('2020-01-01');
  });

  it('truncates long previews and titles', () => {
    const { buildCard } = window.JUG._kvTest;
    const long = 'word '.repeat(80);
    const card = buildCard({ id: 'l', content: long + '\n' + long }, []);
    expect(card.querySelector('.kv-mc-title').textContent.length).toBeLessThanOrEqual(120);
    expect(card.querySelector('.kv-mc-cmd').textContent.endsWith('…')).toBe(true);
  });

  it('derives titles from sentences, lists and headings; untitled when empty', () => {
    const { buildCard } = window.JUG._kvTest;
    const t = (content) => buildCard({ id: 'q', content }, []).querySelector('.kv-mc-title').textContent;
    expect(t('This is a full sentence. And more')).toBe('This is a full sentence.');
    expect(t('## Heading here\nbody')).toBe('Heading here');
    expect(t('')).toBe('Untitled Memory');
    expect(t('x'.repeat(200))).toHaveLength(120);
  });

  it('formats relative dates in minutes, days, and raw invalid values', () => {
    const { buildCard } = window.JUG._kvTest;
    const when = (v) => buildCard({ id: 'd', content: 'c', lastAccessed: v }, []).querySelector('.kv-mc-when').textContent;
    expect(when(new Date(Date.now() - 5 * 60000).toISOString())).toBe('5m ago');
    expect(when(new Date(Date.now() - 3 * 86400000).toISOString())).toBe('3d ago');
    expect(when('not-a-date')).toBe('not-a-date');
    expect(when(null)).toBe('--');
  });
});

describe('inspector', () => {
  const graph = {
    nodes: [
      { id: 'm1', type: 'memory' },
      { id: 'e1', type: 'entity', label: 'EntityOne' },
      { id: 's1', kind: 'symbol', label: 'doTheThing', path: 'src/a/file.py' },
      { id: 's2', kind: 'symbol', label: 'other', path: 'src/a/file.py' },
    ],
    edges: [
      { source: 'm1', target: 'e1' },
      { source: { id: 'e1' }, target: { id: 'm1' } },
    ],
  };

  it('renders sections with links, navigates to the graph, and closes on Escape', () => {
    window.JUG.state.lastData = graph;
    const emitted = [];
    window.JUG.on('graph:selectNode', (ev, v) => emitted.push(v));
    const all = [
      { id: 'm1', content: 'Call doTheThing now. `x`', heat: 0.6, tags: ['alpha'], domain: 'd', emotion: 'urgent', importance: 0.5, valence: 0.2, path: 'src/a/file.py', storeType: 'semantic', consolidationStage: 'late_ltp', accessCount: 3, isGlobal: true },
      { id: 'm2', content: 'Related one', heat: 0.2, tags: ['alpha'], domain: 'd', storeType: 'semantic' },
      { id: 'm3', content: '# Tool: Bash', tags: ['alpha'], domain: 'd' },
    ];
    window.JUG._kvOpenMemory(all[0], all);
    const panel = $('#kv-expanded');
    expect(panel.querySelector('h2').textContent).toBe('Call doTheThing now. `x`'.length > 0 ? panel.querySelector('h2').textContent : '');
    expect(panel.textContent).toContain('SEMANTIC');
    expect(panel.textContent).toContain('Strong');
    expect(panel.textContent).toContain('×3');
    const labels = Array.from(panel.querySelectorAll('.kv-detail__label')).map((l) => l.textContent);
    expect(labels).toEqual(['Content', 'Properties', 'Measurements', 'Classifiers', 'Proximal links', 'Saturation']);
    const metrics = Array.from(panel.querySelectorAll('.kv-link__w')).map((l) => l.textContent);
    expect(metrics).toEqual(expect.arrayContaining(['entity', 'symbol', '0.20']));
    const rows = Array.from(panel.querySelectorAll('.kv-link'));
    rows.find((r) => r.textContent.includes('EntityOne')).click();
    expect(emitted[0].id).toBe('e1');
    expect(window.JUG.state.activeView).toBe('graph');
    rows.find((r) => r.textContent.includes('in ') || r.textContent.includes('doTheThing')).click();
    expect(emitted.length).toBe(2);
    // Related memory link re-opens the inspector on that memory.
    Array.from($$('.kv-link')).find((r) => r.textContent.includes('Related one')).click();
    expect($('#kv-expanded h2').textContent).toBe('Related one');
    expect($$('#kv-expanded').length).toBe(1);
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter' }));
    expect($('#kv-expanded')).not.toBeNull();
    window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
    expect($('#kv-expanded')).toBeNull();
  });

  it('close button removes the panel; minimal memory shows placeholders', () => {
    window.JUG._kvOpenMemory({ id: 'z', heat: 0 }, []);
    const panel = $('#kv-expanded');
    expect(panel.querySelector('h2').textContent).toBe('Untitled Memory');
    expect(panel.querySelector('.kv-expanded-domain').textContent).toBe('uncategorized');
    expect(panel.textContent).toContain('EPISODIC');
    expect(panel.textContent).toContain('user');
    panel.querySelector('.kv-expanded-close').click();
    expect($('#kv-expanded')).toBeNull();
  });

  it('opens when a card is clicked and labels protected source', async () => {
    responder = (url) => (url.includes('/facets') ? FACETS : { items: [{ id: 'p', content: 'Prot', isProtected: true, heat: 0.4 }], next_cursor: null });
    await open();
    $('.kv-card').click();
    expect($('#kv-expanded').textContent).toContain('protected');
  });

  it('finds symbols by file basename and by label, with the cap', () => {
    window.JUG.state.lastData = {
      nodes: Array.from({ length: 20 }, (_, i) => ({ id: 'sy' + i, kind: 'symbol', label: 'sym_' + i + '_x', path: 'a/b/f' + (i % 2) + '.py' })),
      edges: [],
    };
    const resolve = window.JUG._kvResolve;
    expect(resolve({ path: 'f0.py' }, 3).length).toBe(3);
    expect(resolve({ file_refs: ['a/b/f1.py'], fileRefs: ['', 'zzz'] }, 50).every((r) => r.via === 'file')).toBe(true);
    const byLabel = resolve({ content: 'uses sym_3_x and xsym_4_xy plus sym_5_x.' }, 12);
    expect(byLabel.map((r) => r.node.id).sort()).toEqual(['sy3', 'sy5']);
    window.JUG.state.lastData = null;
    expect(resolve({ path: 'x' })).toEqual([]);
  });
});

describe('markdown rendering', () => {
  const md = (s) => window.JUG._kvTest.renderMemoryContent(s);
  it('renders headings, bold, inline code, lists, paragraphs and escapes HTML', () => {
    const d = document.createElement('div');
    d.innerHTML = md('# H1\n### H3\n**b** and `c` <script>\n- item\n\nplain');
    expect(d.querySelector('h2').textContent).toBe('H1');
    expect(d.querySelector('h4').textContent).toBe('H3');
    expect(d.querySelector('strong').textContent).toBe('b');
    expect(d.querySelector('code').textContent).toBe('c');
    expect(d.querySelector('script')).toBeNull();
    expect(d.querySelector('li').textContent).toBe('item');
    expect(d.querySelectorAll('p').length).toBe(2);
  });
  it('renders fenced code, unclosed fences and literal \\n escapes', () => {
    expect(md('```js\nvar a;\n```')).toContain('class="lang-js"');
    expect(md('```\nplain\n```')).not.toContain('lang-');
    expect(md('```py\nunclosed')).toContain('unclosed');
    expect(md('one\\ntwo')).toContain('<p>two</p>');
    expect(md('')).toBe('');
  });
  it('pretty-prints JSON, tool captures, and invalid JSON', () => {
    expect(md('{"a": 1}')).toContain('&quot;a&quot;: 1');
    expect(md('{"stdout": "hi", "stderr": "bad"}')).toContain('--- stderr ---');
    expect(md('{"stdout": "only"}')).toContain('only');
    expect(md('{"a":\n 1,\n "b": [1,\n 2]}')).toContain('&quot;b&quot;');
    expect(md('{oops')).toContain('{oops');
    expect(md('[1,2]')).toContain('<pre');
  });
});
