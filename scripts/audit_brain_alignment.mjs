// Brain-viz alignment audit.
//
// One command to answer: "is the brain visualization wired to everything the
// backend currently emits, or has something gone stale?" Run it after Claude
// Science adds neuroscience gap-fillers to confirm each new element surfaces
// in the brain — or to get an exact list of what still needs wiring.
//
//   node scripts/audit_brain_alignment.mjs [port]      (default 3458)
//
// It cross-checks four things the brain depends on against the LIVE server:
//   1. Vitals   — every system_vitals field has a VITALS_SPEC row (nice
//                 label/colour) or at least renders via the fallback.
//   2. Kinds    — every node kind has a legend slot (KIND_ORDER) and a brain
//                 placement (KIND_REGION, or the special memory/domain paths).
//   3. Colours  — every distinct node colour a graded kind renders has a
//                 palette label (else the legend shows it as "other").
//   4. Staleness— the running server emits every vital the on-disk backend
//                 returns (else: restart the server).
//
// Read-only: fetches endpoints and reads source files; changes nothing.
// Exit code 0 = fully aligned, 1 = gaps found (details printed).

import { readFileSync } from 'fs';
import http from 'http';

const PORT = process.argv[2] || '3458';
const ROOT = new URL('..', import.meta.url).pathname;

// ── tiny helpers ───────────────────────────────────────────────────────────
const read = (p) => readFileSync(ROOT + p, 'utf8');
function get(path) {
  return new Promise((res, rej) => {
    http.get({ host: '127.0.0.1', port: PORT, path, headers: { 'Accept-Encoding': 'identity' } },
      (r) => { let b = ''; r.on('data', (d) => (b += d)); r.on('end', () => res({ status: r.statusCode, body: b })); })
      .on('error', rej);
  });
}
function streamKindsColors(path) {
  return new Promise((res, rej) => {
    const seen = {}; let buf = '';
    http.get({ host: '127.0.0.1', port: PORT, path }, (r) => {
      r.on('data', (d) => {
        buf += d.toString(); let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl); buf = buf.slice(nl + 1);
          if (!line) continue;
          let f; try { f = JSON.parse(line); } catch { continue; }
          for (const n of (f.nodes || [])) {
            const k = n.kind || n.type; const c = (n.color || '#8AA0C0').toUpperCase();
            (seen[k] = seen[k] || {})[c] = (seen[k][c] || 0) + 1;
          }
          if (f.done) { res(seen); r.destroy(); return; }
        }
      });
      r.on('end', () => res(seen));
    }).on('error', rej);
  });
}

// ── load the brain's own maps (eval the dependency-free JS modules) ─────────
globalThis.window = globalThis; globalThis.BRAIN = {};
eval(read('ui/brain/js/palette.js'));
eval(read('ui/brain/js/vitals_spec.js'));
const PAL = BRAIN.PALETTE, SPEC = BRAIN.VITALS_SPEC, STRUCT = BRAIN.VITALS_STRUCTURAL;
const specKeys = new Set(SPEC.map((s) => s.key));

// KIND_REGION keys (anatomy.js) + KIND_ORDER (boot.js) via light regex — plus
// the two kinds placed by special code paths (layout.js), not KIND_REGION.
const kindRegion = new Set(
  [...read('ui/brain/js/anatomy.js').matchAll(/^\s{4}([a-z_]+):\s*'[a-z_]+',/gm)].map((m) => m[1]));
const SPECIAL_PLACED = new Set(['memory', 'domain']);
const kindOrder = new Set(
  (read('ui/brain/js/boot.js').match(/KIND_ORDER\s*=\s*\[([^\]]+)\]/)[1].match(/'([a-z_]+)'/g) || [])
    .map((s) => s.replace(/'/g, '')));

// On-disk backend vital keys (staleness check) from the return dict.
const backendKeys = new Set(
  [...read('cortex_viz/server/graph_discussions.py')
    .split('return {')[1].split('}')[0].matchAll(/"([a-z_]+)":/g)].map((m) => m[1]));

// ── run the checks ──────────────────────────────────────────────────────────
const gaps = [];
function report(title, items) {
  if (!items.length) { console.log(`✓ ${title}: aligned`); return; }
  gaps.push(title);
  console.log(`✗ ${title}:`);
  for (const it of items) console.log(`    - ${it}`);
}

const stats = JSON.parse((await get('/api/stats')).body);
const sv = stats.system_vitals || {};
const kinds = await streamKindsColors('/api/graph/full/stream');

// 1. Vitals: unspecced (fallback-only) + spec rows the backend isn't emitting.
const vitalGaps = [];
for (const k of Object.keys(sv)) {
  if (STRUCT[k] || specKeys.has(k)) continue;
  vitalGaps.push(`${k} — renders via FALLBACK; add a VITALS_SPEC entry for a proper label/colour`);
}
for (const s of SPEC) {
  if (s.key !== 'mean_heat' && !(s.key in sv))
    vitalGaps.push(`${s.label} (${s.key}) — spec row present but backend not emitting it (shows '--')`);
}
report('1. Vitals', vitalGaps);

// 2. Kinds: legend slot + placement.
const kindGaps = [];
for (const k of Object.keys(kinds)) {
  if (!kindOrder.has(k)) kindGaps.push(`${k} — not in KIND_ORDER (missing from the legend)`);
  if (!kindRegion.has(k) && !SPECIAL_PLACED.has(k))
    kindGaps.push(`${k} — no KIND_REGION mapping (placed at the default region)`);
}
report('2. Node kinds', kindGaps);

// 3. Colours: graded-kind colours the palette doesn't label.
const colourGaps = [];
for (const [k, cmap] of Object.entries(kinds)) {
  if (!PAL.isGraded(k)) continue;
  for (const c of Object.keys(cmap)) {
    if (!PAL.labelFor(k, c)) colourGaps.push(`${k} ${c} (${cmap[c]} nodes) — legend shows "other"; add to palette.js`);
  }
}
report('3. Node colours', colourGaps);

// 4. Staleness: on-disk backend vitals the running server isn't serving yet.
const staleGaps = [];
for (const k of backendKeys) {
  if (!(k in sv)) staleGaps.push(`${k} — in _compute_memory_vitals on disk but NOT in the live payload → restart the server`);
}
report('4. Staleness (restart needed?)', staleGaps);

console.log(gaps.length
  ? `\n${gaps.length} area(s) need wiring: ${gaps.join(', ')}`
  : '\nAll aligned — the brain surfaces everything the backend emits.');
process.exit(gaps.length ? 1 : 0);
