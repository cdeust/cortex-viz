// Knowledge-view card + markdown contracts for ui/unified/js/knowledge.js.
//
// These pin three renderings that were computed but never emitted (CodeQL
// js/unused-local-variable #164/#165, js/useless-assignment-to-local #155):
//   * the consolidation stage badge — the UI label + the .kv-badge-* ink
//     class knowledge.css defines, never the raw DB key (`early_ltp`);
//   * the feeling dot's emotion ink;
//   * the `lang-*` class on a fenced code block.
// Drives the live buildCard / renderMemoryContent via JUG._kvTest.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

let kv;

beforeAll(() => {
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  loadScript('ui/unified/js/knowledge.js');
  kv = window.JUG._kvTest;
});

// Minimal record: buildCard reads these fields and nothing else is required.
function mem(extra) {
  return Object.assign({ id: 'm1', content: 'A memory body.', heat: 0.1 }, extra);
}

// Parse a rendered HTML string so assertions read the DOM the browser would
// build, not the source text.
function frag(html) {
  const d = document.createElement('div');
  d.innerHTML = html;
  return d;
}

describe('stage badge — vocabulary + ink', () => {
  it('renders the UI label, never the raw DB key', () => {
    const card = kv.buildCard(mem({ consolidationStage: 'early_ltp' }), []);
    const badge = card.querySelector('.kv-badge');
    expect(badge).not.toBeNull();
    expect(badge.textContent).toBe('Growing');
    expect(card.textContent).not.toContain('early_ltp');
    expect(card.textContent).not.toContain('Early_ltp');
  });

  it('carries the stage ink class knowledge.css styles', () => {
    const card = kv.buildCard(mem({ consolidationStage: 'late_ltp' }), []);
    const badge = card.querySelector('.kv-badge');
    expect(badge.classList.contains('kv-badge-strong')).toBe(true);
    // The DS base class stays — the stage class only supplies the ink.
    expect(badge.classList.contains('aia-badge')).toBe(true);
  });

  it('maps every stage in the vocabulary to a distinct label and class', () => {
    const stages = ['labile', 'early_ltp', 'late_ltp', 'consolidated', 'reconsolidating'];
    const labels = stages.map((s) => kv.stageMeta(s).label);
    const classes = stages.map((s) => kv.stageMeta(s).cls);
    expect(new Set(labels).size).toBe(stages.length);
    expect(new Set(classes).size).toBe(stages.length);
    expect(classes.every((c) => c.startsWith('kv-badge-'))).toBe(true);
  });

  it('falls back to a capitalized, uninked badge for an unknown stage', () => {
    expect(kv.stageMeta('quantum_ltp')).toEqual({ label: 'Quantum_ltp', cls: '' });
    const card = kv.buildCard(mem({ consolidationStage: 'quantum_ltp' }), []);
    const badge = card.querySelector('.kv-badge');
    expect(badge.textContent).toBe('Quantum_ltp');
    expect(badge.className).toBe('aia-badge kv-badge');
  });

  it('emits no badge at all when the record carries no stage', () => {
    const card = kv.buildCard(mem(), []);
    expect(card.querySelector('.kv-badge')).toBeNull();
  });

  it('returns an empty label and class for an absent stage', () => {
    // The inspector's Stage row calls stageMeta directly (no `if (stage)`
    // guard in front of it) and falls back to '--' on an empty label.
    expect(kv.stageMeta('')).toEqual({ label: '', cls: '' });
    expect(kv.stageMeta(undefined)).toEqual({ label: '', cls: '' });
    expect(kv.stageMeta(null)).toEqual({ label: '', cls: '' });
  });

  it('places the badge in the card badge row, beside domain and heat', () => {
    const card = kv.buildCard(mem({ consolidationStage: 'labile', domain: 'cortex' }), []);
    expect(card.querySelector('.kv-mc-brow .kv-badge')).not.toBeNull();
  });
});

describe('feeling dot — emotion ink', () => {
  it('inks the dot with the emotion data token', () => {
    const card = kv.buildCard(mem({ emotion: 'urgent' }), []);
    const dot = card.querySelector('.kv-mc-feel-dot');
    expect(dot.style.background).toBe('var(--emo-urgent)');
  });

  it('inks Cortex raw affect labels too, not only the API facet values', () => {
    const card = kv.buildCard(mem({ emotion: 'frustration' }), []);
    expect(card.querySelector('.kv-mc-feel-dot').style.background)
      .toBe('var(--emo-frustr)');
    // Same token as the facet-vocabulary synonym — one ink per feeling.
    expect(kv.EMO_COLORS.frustration).toBe(kv.EMO_COLORS.negative);
  });

  it('leaves neutral (and unknown) feelings on the CSS default — no inline ink', () => {
    expect(kv.buildCard(mem({ emotion: 'neutral' }), [])
      .querySelector('.kv-mc-feel-dot').style.background).toBe('');
    expect(kv.buildCard(mem({ emotion: 'wistful' }), [])
      .querySelector('.kv-mc-feel-dot').style.background).toBe('');
  });

  it('never inks from a prototype member', () => {
    // 'constructor' resolves up the chain on a plain-object map; the lookup
    // must be own-property only.
    const card = kv.buildCard(mem({ emotion: 'constructor' }), []);
    expect(card.querySelector('.kv-mc-feel-dot').getAttribute('style')).toBeNull();
  });

  it('names the feeling in text — the ink is never the only carrier', () => {
    const card = kv.buildCard(mem({ emotion: 'urgent' }), []);
    expect(card.querySelector('.kv-mc-feel').textContent).toContain('Urgent');
  });
});

describe('renderMemoryContent — fenced code language', () => {
  it('carries the fence language as a lang-* class', () => {
    const html = kv.renderMemoryContent('```python\nprint(1)\n```');
    expect(html).toContain('<code class="lang-python">');
    expect(html).toContain('print(1)');
  });

  it('emits a bare <code> when the fence names no language', () => {
    const html = kv.renderMemoryContent('```\nplain\n```');
    expect(html).toContain('<pre class="kv-code"><code>');
    expect(html).not.toContain('class="lang-');
  });

  it('does not leak the previous block language into a later bare fence', () => {
    const html = kv.renderMemoryContent('```python\na\n```\ntext\n```\nb\n```');
    expect(html).toContain('<code class="lang-python">');
    expect(html.match(/class="lang-/g)).toHaveLength(1);
  });

  it('escapes the language so it cannot break out of the class attribute', () => {
    // The fence regex only captures \w, so a quote never reaches the
    // attribute — assert the outcome, whichever way it is enforced.
    const html = kv.renderMemoryContent('```py"onload=x\ncode\n```');
    expect(html).not.toContain('onload=x');
  });

  it('carries the language on an unterminated fence too', () => {
    const html = kv.renderMemoryContent('```rust\nfn main() {}');
    expect(html).toContain('<code class="lang-rust">');
  });

  it('preserves line breaks inside the block', () => {
    const code = frag(kv.renderMemoryContent('```js\nlet a = 1;\nlet b = 2;\n```'))
      .querySelector('code');
    expect(code.textContent).toBe('let a = 1;\nlet b = 2;');
  });

  it('closes the block — following prose is a sibling, not code', () => {
    const el = frag(kv.renderMemoryContent('```js\ncode line\n```\nafter the fence'));
    const code = el.querySelector('code');
    expect(code.textContent).toBe('code line');
    expect(code.textContent).not.toContain('after');
    expect(el.textContent).toContain('after the fence');
  });

  it('starts a block only on a line-leading fence', () => {
    const el = frag(kv.renderMemoryContent('prose with ```js inline\nplain line'));
    expect(el.querySelector('code')).toBeNull();
    expect(el.textContent).toContain('plain line');
  });

  it('does not carry one block\'s lines into the next', () => {
    const el = frag(kv.renderMemoryContent('```\nfirst\n```\nmid\n```\nsecond\n```'));
    const blocks = el.querySelectorAll('code');
    expect(blocks).toHaveLength(2);
    expect(blocks[1].textContent).toBe('second');
    expect(blocks[1].textContent).not.toContain('first');
  });
});
