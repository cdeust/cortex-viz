// Dashboard connections-list row contract for ui/dashboard/js/interaction.js.
//
// The relationship kind was computed and dropped on the floor (CodeQL
// js/unused-local-variable #160) even though panels.css has styled
// `.conn-type` all along — two rows differing only in HOW the memories
// relate rendered identically. Drives the live row builder via JMD._connTest
// (buildConnectionsList routes every row through it).
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript } from './helpers/load-globals.mjs';

let connItemHtml;

const TYPE_COLORS_HEX = { episodic: '#00d4ff', semantic: '#a78bfa', entity: '#f59e0b' };

beforeAll(() => {
  // interaction.js dereferences #panel-close at load; everything else it
  // reads lives inside handlers.
  document.body.innerHTML = '<button id="panel-close"></button>';
  globalThis.JMD = {
    state: {},
    allNodes: [],
    on() {},
    emit() {},
    setState() {},
    TYPE_COLORS_HEX,
    escHtml(s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },
  };
  window.JMD = globalThis.JMD;
  loadScript('ui/dashboard/js/interaction.js');
  ({ connItemHtml } = window.JMD._connTest);
});

function conn(extra) {
  return Object.assign(
    {
      node: { isEntity: false, storeType: 'episodic', data: { content: 'a memory' } },
      idx: 3,
      weight: 0.42,
      type: 'related',
      isCausal: false,
    },
    extra
  );
}

function frag(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return d;
}

describe('connItemHtml — relationship kind', () => {
  it('renders the edge type in the .conn-type span panels.css styles', () => {
    const el = frag(connItemHtml(conn({ type: 'co_occurrence' })));
    expect(el.querySelector('.conn-type').textContent).toBe('co_occurrence');
  });

  it('renders "causal" for a causal edge, overriding its type', () => {
    const el = frag(connItemHtml(conn({ type: 'related', isCausal: true })));
    expect(el.querySelector('.conn-type').textContent).toBe('causal');
  });

  it('distinguishes two rows that differ only in relationship kind', () => {
    const a = connItemHtml(conn({ type: 'related' }));
    const b = connItemHtml(conn({ type: 'related', isCausal: true }));
    expect(a).not.toBe(b);
  });

  it('keeps the label, weight and target index alongside it', () => {
    const el = frag(connItemHtml(conn()));
    expect(el.querySelector('.conn-label').textContent).toBe('a memory');
    expect(el.querySelector('.conn-weight').textContent).toBe('W: 0.42');
    expect(el.querySelector('.conn-item').dataset.idx).toBe('3');
  });

  it('names an entity by name, a memory by its content excerpt', () => {
    const entity = frag(connItemHtml(conn({
      node: { isEntity: true, storeType: 'entity', data: { name: 'Postgres' } },
    })));
    expect(entity.querySelector('.conn-label').textContent).toBe('Postgres');
  });

  it('labels a nameless entity "Entity" rather than leaving the row blank', () => {
    const el = frag(connItemHtml(conn({
      node: { isEntity: true, storeType: 'entity', data: {} },
    })));
    expect(el.querySelector('.conn-label').textContent).toBe('Entity');
  });

  it('renders an empty label for a memory with no content, never "undefined"', () => {
    const el = frag(connItemHtml(conn({
      node: { isEntity: false, storeType: 'episodic', data: {} },
    })));
    expect(el.querySelector('.conn-label').textContent).toBe('');
  });

  it('truncates a long memory excerpt to 60 characters', () => {
    const long = 'x'.repeat(200);
    const el = frag(connItemHtml(conn({
      node: { isEntity: false, storeType: 'episodic', data: { content: long } },
    })));
    expect(el.querySelector('.conn-label').textContent).toHaveLength(60);
  });

  it('closes the row so concatenated rows are siblings, not nested', () => {
    // buildConnectionsList concatenates these into one innerHTML write; an
    // unclosed row would make every later row a child of the first.
    const el = frag(connItemHtml(conn()) + connItemHtml(conn({ idx: 4 })));
    const rows = el.querySelectorAll('.conn-item');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('.conn-item')).toBeNull();
    expect(rows[1].parentElement).toBe(el);
  });
});

describe('connItemHtml — untrusted payload values', () => {
  it('escapes markup in the relationship type', () => {
    const html = connItemHtml(conn({ type: '<img src=x onerror=alert(1)>' }));
    expect(html).not.toContain('<img');
    expect(frag(html).querySelector('.conn-type').textContent)
      .toBe('<img src=x onerror=alert(1)>');
  });

  it('escapes markup in the label', () => {
    const html = connItemHtml(conn({
      node: { isEntity: false, storeType: 'episodic', data: { content: '<script>x</script>' } },
    }));
    expect(html).not.toContain('<script>');
  });

  it('never interpolates a prototype member into the style attribute', () => {
    // A storeType of "constructor" resolves up the prototype chain on a
    // plain-object colour map — the function body would land inside
    // style="background:…", and escHtml does not escape quotes.
    const html = connItemHtml(conn({
      node: { isEntity: false, storeType: 'constructor', data: { content: 'x' } },
    }));
    expect(html).not.toContain('function');
    expect(frag(html).querySelector('.conn-dot').style.background)
      .toBe('rgb(245, 158, 11)');   // the entity fallback, #f59e0b
  });

  it('uses the store-type colour for a known store type', () => {
    const el = frag(connItemHtml(conn({
      node: { isEntity: false, storeType: 'semantic', data: { content: 'x' } },
    })));
    expect(el.querySelector('.conn-dot').style.background).toBe('rgb(167, 139, 250)');
  });
});
