// CodeMirror 6 extension assembly for the wiki inline editor
// (ui/unified/js/wiki.js).
//
// `keymap` and the history extension were resolved and then left out of the
// EditorState (CodeQL js/unused-local-variable #174/#175): the editor took
// text but had no undo stack and no command bindings at all. These tests pin
// the assembly and its degradation when the CDN payload is partial.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

let buildEditorSetup;

// Stand-ins for the CM6 modules. Each extension is a tagged object so the
// assembled array can be identified positionally.
function fakeMods(over) {
  const mods = {
    view: { keymap: { of: (b) => ({ ext: 'keymap', bindings: b }) } },
    commands: {
      history: () => ({ ext: 'history' }),
      defaultKeymap: [{ key: 'Enter' }, { key: 'Tab' }],
      historyKeymap: [{ key: 'Mod-z' }],
    },
    autoClose: {
      closeBrackets: () => ({ ext: 'closeBrackets' }),
      closeBracketsKeymap: [{ key: 'Backspace' }],
    },
  };
  return Object.assign(mods, over || {});
}

const names = (setup) => setup.map((e) => e.ext);
const bindingsOf = (setup) => (setup.find((e) => e.ext === 'keymap') || {}).bindings;

beforeAll(() => {
  globalThis.JUG = makeJUG();
  window.JUG = globalThis.JUG;
  loadScript('ui/unified/js/wiki.js');
  ({ buildEditorSetup } = window.JUG._wikiTest);
});

describe('buildEditorSetup — the full payload', () => {
  it('installs the undo stack', () => {
    expect(names(buildEditorSetup(fakeMods()))).toContain('history');
  });

  it('installs a keymap — without one CM6 has no bindings at all', () => {
    expect(names(buildEditorSetup(fakeMods()))).toContain('keymap');
  });

  it('binds undo/redo, the default commands and bracket handling', () => {
    const keys = bindingsOf(buildEditorSetup(fakeMods())).map((b) => b.key);
    expect(keys).toContain('Mod-z');      // historyKeymap — the undo that was missing
    expect(keys).toContain('Enter');      // defaultKeymap
    expect(keys).toContain('Backspace');  // closeBracketsKeymap
  });

  it('installs bracket auto-closing from the module already imported', () => {
    expect(names(buildEditorSetup(fakeMods()))).toContain('closeBrackets');
  });

  it('orders bracket bindings ahead of the defaults so they win the key', () => {
    const keys = bindingsOf(buildEditorSetup(fakeMods())).map((b) => b.key);
    expect(keys.indexOf('Backspace')).toBeLessThan(keys.indexOf('Enter'));
  });
});

describe('buildEditorSetup — partial CDN payloads degrade, never throw', () => {
  it('drops history but keeps the default bindings when commands.history is absent', () => {
    const mods = fakeMods();
    delete mods.commands.history;
    const setup = buildEditorSetup(mods);
    expect(names(setup)).not.toContain('history');
    expect(bindingsOf(setup).map((b) => b.key)).toContain('Enter');
  });

  it('survives a missing autocomplete module', () => {
    const mods = fakeMods();
    delete mods.autoClose;
    const setup = buildEditorSetup(mods);
    expect(names(setup)).not.toContain('closeBrackets');
    expect(bindingsOf(setup).map((b) => b.key)).toContain('Mod-z');
  });

  it('emits no keymap when nothing supplies bindings', () => {
    const setup = buildEditorSetup(fakeMods({ commands: {}, autoClose: {} }));
    expect(names(setup)).not.toContain('keymap');
  });

  it('emits no keymap when the view module has none, without throwing', () => {
    const setup = buildEditorSetup(fakeMods({ view: {} }));
    expect(names(setup)).not.toContain('keymap');
    expect(names(setup)).toContain('history');
  });

  it('returns an empty setup for an empty module map', () => {
    expect(buildEditorSetup({})).toEqual([]);
    expect(buildEditorSetup(null)).toEqual([]);
  });
});
