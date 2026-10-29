// Behaviour tests for ui/shared/surface-toggle.js, palette.js and
// ui/dashboard/js/interaction.js (tooltip, detail panel, shortcuts).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

describe('surface-toggle', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-surface');
    delete window.CortexSurface;
  });

  it('boots on paper by default without persisting', () => {
    loadScript('ui/shared/surface-toggle.js');
    expect(document.documentElement.getAttribute('data-surface')).toBe('paper');
    expect(localStorage.getItem('cortex-viz.surface')).toBeNull();
    expect(window.CortexSurface.SURFACES).toEqual(['paper', 'ink']);
    expect(window.CortexSurface.DEFAULT).toBe('paper');
  });

  it('honours a stored ink choice and ignores garbage', () => {
    localStorage.setItem('cortex-viz.surface', 'ink');
    loadScript('ui/shared/surface-toggle.js');
    expect(window.CortexSurface.get()).toBe('ink');
    localStorage.setItem('cortex-viz.surface', 'neon');
    loadScript('ui/shared/surface-toggle.js');
    expect(window.CortexSurface.get()).toBe('paper');
  });

  it('toggle flips, persists and dispatches the change event', () => {
    loadScript('ui/shared/surface-toggle.js');
    const seen = [];
    window.addEventListener('cortex:surface-change', (e) => seen.push(e.detail));
    expect(window.CortexSurface.toggle()).toBe('ink');
    expect(localStorage.getItem('cortex-viz.surface')).toBe('ink');
    expect(window.CortexSurface.toggle()).toBe('paper');
    expect(seen).toEqual([
      { surface: 'ink', previous: 'paper' },
      { surface: 'paper', previous: 'ink' },
    ]);
  });

  it('set coerces unknown values, honours persist:false and force', () => {
    loadScript('ui/shared/surface-toggle.js');
    const seen = [];
    window.addEventListener('cortex:surface-change', (e) => seen.push(e.detail));
    expect(window.CortexSurface.set('bogus')).toBe('paper');
    expect(seen.length).toBe(0);
    localStorage.clear();
    window.CortexSurface.set('ink', { persist: false });
    expect(localStorage.getItem('cortex-viz.surface')).toBeNull();
    window.CortexSurface.set('ink', { force: true, persist: false });
    expect(seen.length).toBe(2);
  });

  it('survives blocked storage', () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    loadScript('ui/shared/surface-toggle.js');
    expect(window.CortexSurface.get()).toBe('paper');
    expect(window.CortexSurface.toggle()).toBe('ink');
    get.mockRestore();
    set.mockRestore();
  });

  it('survives a CustomEvent failure', () => {
    loadScript('ui/shared/surface-toggle.js');
    const orig = window.dispatchEvent;
    window.dispatchEvent = () => {
      throw new Error('nope');
    };
    expect(window.CortexSurface.toggle()).toBe('ink');
    window.dispatchEvent = orig;
  });

  it('bindButton reflects state, aria-label and toggles on click', () => {
    loadScript('ui/shared/surface-toggle.js');
    window.CortexSurface.bindButton(null);
    const btn = document.createElement('button');
    window.CortexSurface.bindButton(btn);
    expect(btn.getAttribute('data-surface-state')).toBe('paper');
    expect(btn.getAttribute('aria-label')).toMatch(/instrument/);
    btn.click();
    expect(document.documentElement.getAttribute('data-surface')).toBe('ink');
    expect(btn.getAttribute('data-surface-state')).toBe('ink');
    expect(btn.getAttribute('aria-label')).toMatch(/paper/);
    window.CortexSurface.set('paper');
    expect(btn.getAttribute('data-surface-state')).toBe('paper');
  });
});

describe('palette', () => {
  function fakeCanvas(opts) {
    const ctx = {
      clearRect() {},
      fillRect() {},
      _v: '',
      set fillStyle(v) {
        if (opts && opts.throwSet && v !== '#000') throw new Error('bad');
        this._v = v;
      },
      get fillStyle() {
        return this._v;
      },
      getImageData: () => ({ data: [1, 171, 255, 255] }),
    };
    window.HTMLCanvasElement.prototype.getContext = () => ctx;
  }

  beforeEach(() => {
    delete window.CortexPalette;
    delete window.CortexSurface;
    document.documentElement.style.setProperty('--stage-labile', ' #123456 ');
  });

  it('readVar trims, hex pads channel bytes', () => {
    fakeCanvas();
    loadScript('ui/shared/palette.js');
    expect(window.CortexPalette.readVar('--stage-labile')).toBe('#123456');
    expect(window.CortexPalette.hex('--x')).toBe('#01abff');
  });

  it('hex tolerates a throwing fillStyle', () => {
    fakeCanvas({ throwSet: true });
    loadScript('ui/shared/palette.js');
    expect(window.CortexPalette.hex('--stage-labile')).toBe('#01abff');
  });

  it('stages / heat / emo return maps keyed by vocabulary', () => {
    fakeCanvas();
    loadScript('ui/shared/palette.js');
    const P = window.CortexPalette;
    expect(Object.keys(P.stages())).toEqual([
      'labile',
      'early-ltp',
      'late-ltp',
      'consolidated',
      'semantic',
    ]);
    expect(Object.keys(P.heat())).toEqual(['hot', 'warm', 'cool', 'cold']);
    expect(Object.keys(P.emo())).toContain('frustration');
    expect(P.tokens.heat.hot).toBe('--heat-hot');
    P.flush();
  });

  it('flushes cache on surface change when CortexSurface is loaded', () => {
    fakeCanvas();
    loadScript('ui/shared/surface-toggle.js');
    loadScript('ui/shared/palette.js');
    window.CortexPalette.hex('--a');
    window.CortexSurface.toggle();
    expect(window.CortexPalette.hex('--a')).toBe('#01abff');
  });
});

describe('dashboard interaction', () => {
  let handlers;
  let JMD;
  beforeEach(() => {
    handlers = {};
    document.body.innerHTML = `
      <div id="tooltip" style="display:none"><span class="tt-label"></span><span class="tt-type"></span><span class="tt-meta"></span></div>
      <div id="panel"></div><span id="panel-type"></span><div id="panel-name"></div>
      <div id="panel-meta"></div><div id="panel-tags"></div><div id="panel-heat-bar"></div>
      <div id="panel-connections"></div><button id="panel-close"></button>`;
    JMD = {
      state: { analyticsOpen: false },
      allNodes: [],
      edgeNodeMap: {},
      getActiveEdges: () => [],
      TYPE_COLORS_HEX: { episodic: '#00d4ff', semantic: '#a78bfa', entity: '#f59e0b' },
      on(ev, fn) {
        handlers[ev] = fn;
      },
      emit: vi.fn((ev, v) => handlers[ev] && handlers[ev](v)),
      setState: vi.fn((k, v) => {
        JMD.state[k] = v;
      }),
      timeAgo: () => '3m ago',
      escHtml: (s) => String(s).replace(/</g, '&lt;'),
      heatColorCSS: () => 'red',
      resetCamera: vi.fn(),
    };
    globalThis.JMD = window.JMD = JMD;
    loadScript('ui/dashboard/js/interaction.js');
  });

  const mem = (extra) => ({
    isEntity: false,
    storeType: 'episodic',
    data: Object.assign(
      {
        content: 'hello',
        heat: 0.5,
        importance: 0.2,
        tags: ['<b>'],
        created_at: 1,
        domain: 'd',
        agent_context: 'ag',
        is_protected: true,
        is_global: true,
      },
      extra
    ),
  });

  it('shows a memory tooltip with meta and positions it', () => {
    handlers['graph:showTooltip']({ node: mem(), x: 10, y: 20 });
    const tt = document.getElementById('tooltip');
    expect(tt.style.display).toBe('block');
    expect(tt.querySelector('.tt-label').textContent).toBe('hello');
    expect(tt.querySelector('.tt-type').textContent).toBe('EPISODIC');
    expect(tt.querySelector('.tt-meta').textContent).toMatch(
      /3m ago.*heat 0.50.*d.*ag.*protected.*team/
    );
    expect(tt.style.left).toBe('26px');
  });

  it('tooltip for entity flips near viewport edges and hides', () => {
    handlers['graph:showTooltip']({
      node: { isEntity: true, storeType: 'entity', data: { name: 'Ent', type: 'person' } },
      x: innerWidth - 5,
      y: innerHeight - 5,
    });
    const tt = document.getElementById('tooltip');
    expect(tt.querySelector('.tt-type').textContent).toBe('ENTITY');
    expect(tt.querySelector('.tt-meta').textContent).toBe('person · heat 0.00');
    expect(parseInt(tt.style.left, 10)).toBe(innerWidth - 5 - 294);
    expect(parseInt(tt.style.top, 10)).toBe(innerHeight - 100);
    handlers['graph:hideTooltip']();
    expect(tt.style.display).toBe('none');
  });

  it('mousemove repositions only a visible tooltip', () => {
    const tt = document.getElementById('tooltip');
    dispatchEvent(new MouseEvent('mousemove', { clientX: 5, clientY: 6 }));
    expect(tt.style.left).toBe('');
    handlers['graph:showTooltip']({ node: mem() });
    dispatchEvent(new MouseEvent('mousemove', { clientX: 100, clientY: 50 }));
    expect(tt.style.left).toBe('116px');
    dispatchEvent(new MouseEvent('mousemove', { clientX: innerWidth - 1, clientY: innerHeight }));
    expect(tt.style.left).toBe(innerWidth - 1 - 294 + 'px');
    expect(tt.style.top).toBe(innerHeight - 100 + 'px');
  });

  it('opens a memory panel with escaped tags and connections', () => {
    const a = mem();
    const b = mem({ content: 'other', heat: 0.1 });
    const c = { isEntity: true, storeType: 'entity', data: { name: 'E' } };
    JMD.allNodes = [a, b, c];
    JMD.edgeNodeMap = { 0: [0, 1, 2, 9] };
    JMD.getActiveEdges = () => [
      { srcIdx: 0, tgtIdx: 1, weight: 0.2, type: 'related' },
      { srcIdx: 2, tgtIdx: 0, weight: 0.9, isCausal: true },
      { srcIdx: 0, tgtIdx: 99, weight: 1 },
    ];
    handlers['graph:openPanel'](a);
    expect(document.getElementById('panel').classList.contains('open')).toBe(true);
    expect(document.getElementById('panel-type').textContent).toBe('EPISODIC');
    expect(document.getElementById('panel-meta').innerHTML).toContain('Protected');
    expect(document.getElementById('panel-meta').innerHTML).toContain('Team');
    expect(document.getElementById('panel-tags').innerHTML).toContain('&lt;b&gt;');
    expect(document.getElementById('panel-heat-bar').style.width).toBe('50%');
    const items = document.querySelectorAll('.conn-item');
    expect(items.length).toBe(2);
    expect(items[0].querySelector('.conn-type').textContent).toBe('causal');
    items[1].click();
    expect(JMD.emit).toHaveBeenCalledWith('graph:openPanel', b);
  });

  it('opens an entity panel and handles no connections / unknown node', () => {
    const e = { isEntity: true, storeType: 'entity', data: { name: '', heat: 1 } };
    handlers['graph:openPanel'](e);
    expect(document.getElementById('panel-name').textContent).toBe('Unknown Entity');
    expect(document.getElementById('panel-meta').innerHTML).toContain('unknown');
    expect(document.getElementById('panel-connections').textContent).toBe('No connections');
    JMD.allNodes = [e];
    handlers['graph:openPanel'](e);
    expect(document.getElementById('panel-connections').textContent).toBe('No connections');
  });

  it('closes via button, Escape; A toggles analytics; inputs are ignored', () => {
    document.getElementById('panel').classList.add('open');
    document.getElementById('panel-close').click();
    expect(document.getElementById('panel').classList.contains('open')).toBe(false);
    expect(JMD.resetCamera).toHaveBeenCalled();
    const before = JMD.resetCamera.mock.calls.length;
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(JMD.resetCamera.mock.calls.length).toBeGreaterThan(before);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }));
    expect(JMD.setState).toHaveBeenCalledWith('analyticsOpen', true);
    const input = document.createElement('input');
    document.body.appendChild(input);
    const n = JMD.setState.mock.calls.length;
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
    expect(JMD.setState.mock.calls.length).toBe(n);
  });
});
