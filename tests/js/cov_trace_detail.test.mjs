// Behaviour tests for ui/unified/js/trace_detail.js: build() HTML per node
// kind and wire() lazy sections (diff, versions, AST, convo, impact).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let TD;
let routes;
let content;

const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
const ok = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
const bad = () => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });

beforeEach(() => {
  document.body.innerHTML = '<div id="detail-content"></div>';
  content = document.getElementById('detail-content');
  delete window.CortexPalette;
  window.JUG = { _disc: { openConversationModal: vi.fn() } };
  globalThis.JUG = window.JUG;
  window.TraceView = { showImpact: vi.fn() };
  loadScript('ui/unified/js/trace_detail.js');
  TD = window.JUG._traceDetail;
  routes = {};
  globalThis.fetch = vi.fn((url) => {
    const key = Object.keys(routes).find((k) => String(url).startsWith(k));
    return key ? routes[key](url) : Promise.reject(new Error('no route'));
  });
});
afterEach(() => {
  delete globalThis.fetch;
  delete window.TraceView;
});

function mount(node) {
  content.innerHTML = TD.build(node);
  TD.wire(content, node);
  return content;
}

describe('build', () => {
  it('domain hub', () => {
    const h = TD.build({ kind: 'domain', label: 'D', session_count: 4 });
    expect(h).toContain('4 sessions');
    expect(TD.build({ type: 'domain', id: 'x' })).toContain('? sessions');
  });

  it('prompt shows the message and timestamp, escaped', () => {
    const h = TD.build({ kind: 'prompt', full: '<b>hi</b>', ts: '10:00' });
    expect(h).toContain('&lt;b&gt;hi&lt;/b&gt;');
    expect(h).toContain('10:00');
    expect(TD.build({ kind: 'prompt', label: 'fallback' })).toContain('fallback');
  });

  it('action shows tool, causal context, target, input, result, host, convo button', () => {
    const h = TD.build({
      kind: 'action', tool: 'Bash', ts: 't', session_id: 'abcdefghijk', target_label: 'x.py',
      target_kind: 'file', input_summary: 'ls', result: 'out', host: 'box',
    });
    expect(h).toContain('Tool: Bash');
    expect(h).toContain('Part of session abcdefgh.');
    expect(h).toContain('Target: x.py');
    expect(h).toContain('Observed input');
    expect(h).toContain('Observed result');
    expect(h).toContain('Host: box');
    expect(h).toContain('data-session-id="abcdefghijk"');
    const bare = TD.build({ kind: 'action' });
    expect(bare).toContain('Tool: action');
    expect(bare).not.toContain('disc-view-btn');
  });

  it('session shows counts and a lazy conversation section', () => {
    const h = TD.build({ kind: 'session', id: 'session:s1', action_count: 3, git_branch: 'main', started_at: 'now' });
    expect(h).toContain('Actions: 3');
    expect(h).toContain('Branch: main');
    expect(h).toContain('data-sid="s1"');
    expect(TD.build({ kind: 'session', id: 'zz' })).toContain('Actions: ?');
  });

  it('file shows path or resolving placeholder', () => {
    expect(TD.build({ kind: 'file', path: '/a/b.py' })).toContain('/a/b.py');
    expect(TD.build({ kind: 'file', id: 'f' })).toContain('Resolving path');
  });

  it('discussion and memory render full text, timestamp and convo button', () => {
    const d = TD.build({ kind: 'discussion', full: 'said', ts: 't', session_id: 's' });
    expect(d).toContain('said');
    expect(d).toContain('View Full Conversation');
    expect(TD.build({ kind: 'discussion', label: 'L' })).not.toContain('View Full');
    const m = TD.build({ kind: 'memory', label: 'recall · q', full: 'query', ts: 't', session_id: 's' });
    expect(m).toContain('Operation: recall');
    expect(m).toContain('Recalled query');
    const r = TD.build({ type: 'memory', label: 'remember · x' });
    expect(r).toContain('Operation: remember');
    expect(r).toContain('Remembered content');
  });

  it('activity targets list observed activity; unknown kinds just get a header', () => {
    for (const k of ['tool_hub', 'mcp', 'api', 'database', 'skill', 'command', 'agent', 'web']) {
      expect(TD.build({ kind: k, label: k, session_id: 's' })).toContain('Observed activity target');
    }
    expect(TD.build({ kind: 'tool_hub' })).not.toContain('disc-view-btn');
    const u = TD.build({ kind: 'mystery', id: 'q' });
    expect(u).toContain('<h2>q</h2>');
  });

  it('tool colours resolve via CortexPalette when present, else via CSS tokens', () => {
    document.documentElement.style.setProperty('--tool-edit', ' #ff0000 ');
    expect(TD.build({ kind: 'action', tool: 'Edit' })).toContain('#ff0000');
    window.CortexPalette = { hex: vi.fn((t) => '#0a0b0c') };
    const h = TD.build({ kind: 'action', tool: 'Nope' });
    expect(h).toContain('#0a0b0c');
    expect(window.CortexPalette.hex).toHaveBeenCalledWith('--tool-read');
    delete window.CortexPalette;
    // unresolvable token falls through to --info-ink (null when also unset)
    expect(TD.build({ kind: 'mystery' })).toContain('node-badge');
  });
});

describe('wire: session conversation', () => {
  it('lazy-loads the transcript and truncates long ones', async () => {
    const msgs = Array.from({ length: 16 }, (_, i) => ({ role: 'user', text: 'm' + i }));
    msgs.push({ role: 'x', text: '  ' });
    routes['/api/discussion/s1'] = () => ok({ messages: msgs });
    mount({ kind: 'session', id: 'session:s1' });
    await flush();
    const slot = document.getElementById('td-convo');
    expect(slot.querySelectorAll('.td-msg').length).toBe(14);
    expect(slot.textContent).toContain('2 more');
  });

  it('shows messages from turns, empty and error states', async () => {
    routes['/api/discussion/'] = (u) => {
      if (u.endsWith('a')) return ok({ turns: [{ type: 'assistant', content: 'hello' }] });
      if (u.endsWith('b')) return ok({});
      return bad();
    };
    mount({ kind: 'session', id: 'a' });
    await flush();
    expect(document.getElementById('td-convo').textContent).toContain('hello');
    mount({ kind: 'session', id: 'b' });
    await flush();
    expect(document.getElementById('td-convo').textContent).toBe('No transcript text.');
    mount({ kind: 'session', id: 'c' });
    await flush();
    expect(document.getElementById('td-convo').textContent).toBe('Transcript unavailable.');
  });

  it('loads when a closed section is toggled open, once', async () => {
    routes['/api/discussion/'] = () => ok({ messages: [{ text: 't' }] });
    content.innerHTML = TD.build({ kind: 'session', id: 'tog' }).replace('<details class="td-sec" open', '<details class="td-sec"');
    TD.wire(content, { kind: 'session', id: 'tog' });
    expect(fetch).not.toHaveBeenCalled();
    const det = content.querySelector('details');
    det.open = true;
    det.dispatchEvent(new Event('toggle'));
    det.dispatchEvent(new Event('toggle'));
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('convo button opens the modal', () => {
    mount({ kind: 'discussion', session_id: 'sess-1', full: 'x' });
    content.querySelector('.disc-view-btn').click();
    expect(window.JUG._disc.openConversationModal).toHaveBeenCalledWith('sess-1');
  });

  it('missing modal helper leaves the button inert', () => {
    delete window.JUG._disc;
    mount({ kind: 'discussion', session_id: 'sess-1' });
    content.querySelector('.disc-view-btn').click();
  });
});

describe('wire: file sections', () => {
  const full = {
    git: {
      available: true, diff_type: 'last_commit', commit: { sha: 'abc123', subject: 'fix thing' },
      lines: [{ type: 'add', text: '+x' }, { type: 'del', text: '-y' }, 'plain'],
    },
    versions: { available: true, versions: [{ sha: 's1', date: '2026-01-02T00:00', subject: 'init' }] },
  };

  it('loads git diff and versions for the open section', async () => {
    routes['/api/trace/file'] = () => ok(full);
    mount({ kind: 'file', id: 'f', path: '/r/a.py' });
    await flush();
    const git = document.getElementById('td-git');
    expect(git.querySelector('.td-sha').textContent).toBe('abc123');
    expect(git.querySelector('.td-diff-add').textContent).toBe('+x');
    expect(git.querySelector('.td-diff-ctx').textContent).toBe('plain');
    expect(git.querySelector('.tool-diff-btn').getAttribute('data-file')).toBe('/r/a.py');
    const ver = document.getElementById('td-versions');
    expect(ver.textContent).toContain('2026-01-02');
    expect(fetch.mock.calls[0][0]).toBe('/api/trace/file?path=%2Fr%2Fa.py');
  });

  it('caches the file response across wirings', async () => {
    routes['/api/trace/file'] = () => ok(full);
    mount({ kind: 'file', id: 'f', path: '/r/a.py' });
    await flush();
    mount({ kind: 'file', id: 'f', path: '/r/a.py' });
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(document.getElementById('td-git').textContent).toContain('fix thing');
  });

  it('shows distinct messages per diff type', async () => {
    const cases = [
      [{ available: false, reason: 'no repo' }, 'no repo'],
      [{ available: false }, 'Diff unavailable'],
      [{ available: true, diff_type: 'none', lines: [] }, 'file clean'],
      [{ available: true, diff_type: 'uncommitted', lines: [{ text: 'a' }] }, 'Uncommitted changes'],
      [{ available: true, diff_type: 'untracked', lines: [{ text: 'a' }] }, 'Untracked file'],
      [{ available: true, diff_type: 'weird', lines: [{ text: 'a' }], truncated: true }, 'truncated'],
      [{ available: true, lines: [{ text: 'a' }] }, 'Diff'],
    ];
    for (let i = 0; i < cases.length; i++) {
      routes['/api/trace/file'] = () => ok({ git: cases[i][0], versions: null });
      mount({ kind: 'file', id: 'f' + i, path: '/p/' + i });
      await flush();
      expect(document.getElementById('td-git').textContent).toContain(cases[i][1]);
    }
    expect(document.getElementById('td-versions').textContent).toBe('no history');
  });

  it('renders empty versions and long diffs', async () => {
    const lines = Array.from({ length: 301 }, (_, i) => ({ type: 'add', text: 'l' + i }));
    routes['/api/trace/file'] = () => ok({ git: { available: true, diff_type: 'uncommitted', lines }, versions: { available: true } });
    mount({ kind: 'file', id: 'f', path: '/long' });
    await flush();
    expect(document.getElementById('td-git').querySelectorAll('.td-diff-add').length).toBe(300);
    expect(document.getElementById('td-git').textContent).toContain('truncated');
    expect(document.getElementById('td-versions').textContent).toBe('untracked / no commits');
  });

  it('reports server failures for git and versions', async () => {
    routes['/api/trace/file'] = () => bad();
    mount({ kind: 'file', id: 'f', path: '/x' });
    await flush();
    expect(document.getElementById('td-git').textContent).toMatch(/Diff unavailable/);
    expect(document.getElementById('td-versions').textContent).toMatch(/History unavailable/);
  });

  it('AST loads on its own first expand with symbols of every shape', async () => {
    const shapes = [
      [{ available: true, symbols: [{ qualified_name: 'a.B' }, { name: 'n' }, { id: 'i' }, { properties: { name: 'p' } }, {}] }, ['a.B', 'n', 'i', 'p', '?']],
      [{ available: true, symbols: { rows: [{ name: 'r1' }] } }, ['r1']],
      [{ available: true, symbols: { nodes: [{ name: 'n1' }] } }, ['n1']],
    ];
    for (let i = 0; i < shapes.length; i++) {
      routes['/api/trace/file'] = () => ok({ ast: shapes[i][0] });
      mount({ kind: 'file', id: 'f', path: '/ast' + i });
      const det = content.querySelector('details[data-lazy="ast"]');
      expect(fetch.mock.calls.filter((c) => String(c[0]).includes('include=ast')).length).toBe(i);
      det.open = true;
      det.dispatchEvent(new Event('toggle'));
      await flush();
      const text = document.getElementById('td-ast').textContent;
      shapes[i][1].forEach((s) => expect(text).toContain(s));
    }
  });

  it('AST truncates, reports reason, caches and handles failure', async () => {
    const syms = Array.from({ length: 52 }, (_, i) => ({ name: 's' + i }));
    routes['/api/trace/file'] = () => ok({ ast: { available: true, symbols: syms } });
    mount({ kind: 'file', id: 'f', path: '/big' });
    let det = content.querySelector('details[data-lazy="ast"]');
    det.open = true;
    det.dispatchEvent(new Event('toggle'));
    await flush();
    expect(document.getElementById('td-ast').textContent).toContain('2 more');
    const n = fetch.mock.calls.length;
    mount({ kind: 'file', id: 'f', path: '/big' });
    det = content.querySelector('details[data-lazy="ast"]');
    det.open = true;
    det.dispatchEvent(new Event('toggle'));
    await flush();
    expect(fetch.mock.calls.length).toBe(n);

    routes['/api/trace/file'] = () => ok({ ast: { available: false, reason: 'not python' } });
    mount({ kind: 'file', id: 'f', path: '/none' });
    det = content.querySelector('details[data-lazy="ast"]');
    det.open = true;
    det.dispatchEvent(new Event('toggle'));
    await flush();
    expect(document.getElementById('td-ast').textContent).toContain('no AST · not python');

    routes['/api/trace/file'] = () => ok({});
    mount({ kind: 'file', id: 'f', path: '/bare' });
    det = content.querySelector('details[data-lazy="ast"]');
    det.open = true;
    det.dispatchEvent(new Event('toggle'));
    await flush();
    expect(document.getElementById('td-ast').textContent).toContain('not indexed');

    routes['/api/trace/file'] = () => bad();
    mount({ kind: 'file', id: 'f', path: '/err' });
    det = content.querySelector('details[data-lazy="ast"]');
    det.open = true;
    det.dispatchEvent(new Event('toggle'));
    await flush();
    expect(document.getElementById('td-ast').textContent).toMatch(/AST unavailable/);
  });

  it('impact section opens the impact diagram for the path', async () => {
    routes['/api/trace/file'] = () => ok(full);
    mount({ kind: 'file', id: 'f', path: '/imp.py' });
    await flush();
    const impact = document.getElementById('td-impact');
    expect(impact.style.cursor).toBe('pointer');
    impact.click();
    expect(window.TraceView.showImpact).toHaveBeenCalledWith('/imp.py');
    delete window.TraceView;
    impact.click();
  });

  it('resolves a missing path through /api/graph/node', async () => {
    routes['/api/graph/node'] = () => ok({ record: { path: '/resolved.py' } });
    routes['/api/trace/file'] = () => ok(full);
    mount({ kind: 'file', id: 'f 1' });
    await flush();
    expect(document.getElementById('td-file-path').textContent).toBe('/resolved.py');
    expect(fetch.mock.calls[0][0]).toBe('/api/graph/node?id=f%201');
    expect(fetch.mock.calls.some((c) => String(c[0]).includes('%2Fresolved.py'))).toBe(true);
  });

  it('marks sections unknown when the path cannot be resolved', async () => {
    routes['/api/graph/node'] = () => ok({ record: {} });
    mount({ kind: 'file', id: 'f' });
    await flush();
    expect(document.getElementById('td-file-path').textContent).toBe('Path unknown for this node');
    expect(document.getElementById('td-git').textContent).toBe('Path unknown for this node');
    expect(document.getElementById('td-impact').textContent).toBe('Path unknown for this node');
    expect(fetch.mock.calls.every((c) => !String(c[0]).includes('/api/trace/file'))).toBe(true);

    routes['/api/graph/node'] = () => bad();
    mount({ kind: 'file', id: 'g' });
    await flush();
    expect(document.getElementById('td-ast').textContent).toBe('Path unknown for this node');

    mount({ kind: 'file' });
    await flush();
    expect(document.getElementById('td-file-path').textContent).toBe('Path unknown for this node');
  });

  it('a stale resolution does not overwrite a newer render', async () => {
    let release;
    routes['/api/graph/node'] = () => new Promise((r) => { release = () => r({ ok: true, json: () => Promise.resolve({ record: { path: '/old' } }) }); });
    routes['/api/trace/file'] = () => ok(full);
    mount({ kind: 'file', id: 'slow' });
    mount({ kind: 'file', id: 'fast', path: '/fresh' });
    release();
    await flush();
    expect(document.getElementById('td-file-path').textContent).toBe('/fresh');
  });
});
