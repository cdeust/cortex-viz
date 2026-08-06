import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

beforeAll(() => {
  window.JUG = {};
  globalThis.JUG = window.JUG;
  window.CortexPalette = { hex: () => '#123456' };
  loadScript('ui/unified/js/trace_detail.js');
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
  delete globalThis.fetch;
});

describe('Trace observed action detail', () => {
  it('shows the explicit target, input, result, host and conversation link', () => {
    const html = JUG._traceDetail.build({
      id: 'act:s1:7',
      kind: 'action',
      label: 'db_read',
      tool: 'postgres',
      session_id: 's1',
      target_kind: 'database',
      target_label: 'postgres:cortex.session_activity',
      input_summary: 'SELECT & inspect',
      result: '3 <rows>',
      host: 'codex',
    });

    expect(html).toContain('postgres:cortex.session_activity · database');
    expect(html).toContain('SELECT &amp; inspect');
    expect(html).toContain('3 &lt;rows&gt;');
    expect(html).toContain('Host: codex');
    expect(html).toContain('data-session-id="s1"');
  });

  it('loads git and history directly from a rendered file path', async () => {
    document.body.innerHTML = '<div id="detail-content"></div>';
    globalThis.fetch = vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        git: { available: false, reason: 'no repository for fixture' },
        versions: { available: true, versions: [] },
      }),
    }));
    const content = document.getElementById('detail-content');
    const node = {
      id: 'file:deadbeef00', kind: 'file', label: 'x.py', path: '/repo/x.py',
    };
    content.innerHTML = JUG._traceDetail.build(node);
    JUG._traceDetail.wire(content, node);

    await vi.waitFor(() => expect(fetch)
      .toHaveBeenCalledWith('/api/trace/file?path=%2Frepo%2Fx.py'));
    await vi.waitFor(() => expect(document.getElementById('td-git').textContent)
      .toContain('no repository for fixture'));
    expect(document.getElementById('td-versions').textContent).toContain('untracked / no commits');
  });

  it('refreshes a selected sparse file when its path arrives later', async () => {
    document.body.innerHTML = '<aside id="detail-panel"><div id="detail-content"></div></aside>';
    const handlers = {};
    const traceDetail = JUG._traceDetail;
    const bus = {
      state: { activeView: 'trace' },
      _traceDetail: traceDetail,
      on(event, fn) { (handlers[event] || (handlers[event] = [])).push(fn); },
      emit(event, value) { (handlers[event] || []).forEach((fn) => fn(value)); },
    };
    window.JUG = bus;
    globalThis.JUG = bus;
    loadScript('ui/unified/js/detail_panel.js');
    expect(handlers['graph:nodeUpdated']).toHaveLength(1);

    let finishSparseLookup;
    globalThis.fetch = vi.fn((url) => {
      if (url.startsWith('/api/graph/node?')) {
        return new Promise((resolve) => { finishSparseLookup = resolve; });
      }
      if (url === '/api/trace/file?path=%2Frepo%2Flater.py') {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            git: { available: false, reason: 'fixture loaded' },
            versions: { available: true, versions: [] },
          }),
        });
      }
      throw new Error(`unexpected fetch ${url}`);
    });

    const file = { id: 'file:deadbeef00', kind: 'file', label: 'x.py' };
    bus.emit('graph:selectNode', file);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/api/graph/node?id=file%3Adeadbeef00'
    ));

    file.path = '/repo/later.py';
    bus.emit('graph:nodeUpdated', file);
    expect(document.getElementById('td-file-path').textContent).toBe('/repo/later.py');
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/api/trace/file?path=%2Frepo%2Flater.py'
    ));

    finishSparseLookup({ ok: true, json: () => Promise.resolve({ found: false }) });
    await vi.waitFor(() => expect(document.getElementById('td-file-path').textContent)
      .toBe('/repo/later.py'));
    await vi.waitFor(() => expect(document.getElementById('td-git').textContent)
      .toContain('fixture loaded'));
  });
});
