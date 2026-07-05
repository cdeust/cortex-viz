# cortex-viz × AI Architect — shared design foundation

The single source of truth that every cortex-viz UI surface (unified, brain,
methodology, atom, dashboard) is aligned against. Vendored from the **AI
Architect Design System** so cortex-viz reads as part of the ecosystem: the
certified record, instrument-grade calm, colour only from data.

## What's here

| File | Role |
|---|---|
| `ds.css` | **Link this first.** Imports the vendored token contract (colours, surfaces, type, spacing, elevation, fonts). Author chrome against the semantic aliases, never raw primitives. |
| `tokens/` | Vendored design-system tokens — do not edit here; edit the DS source and re-copy. |
| `components/` | Optional `aia-*` primitives (ProofBadge, Stamp, CitationTag, HeatBar, the appellation/ledger motifs). Import per surface if used. |
| `surface-toggle.js` | Stamps `data-surface="paper"` on `<html>` before first paint; toggles to legacy `ink`; persists in `localStorage`; fires `cortex:surface-change`. Load in `<head>` before the stylesheets. |
| `palette.js` | Browser-side reader of the data tokens for **baked-colour renderers** (force-graph canvas, Three.js brain). `CortexPalette.hex('--stage-labile')` → surface-correct `#rrggbb`. |

## Surface posture

cortex-viz **boots on paper** (cream record, deep data inks, terracotta stamp) —
the brand doctrine is *no black backgrounds, data views included*. The legacy
warm-dark instrument is `data-surface="ink"`, an explicit opt-in via the
top-right toggle. Wire the toggle button with `CortexSurface.bindButton(el)`.

## How each medium consumes colour

- **CSS / SVG / DOM** — use the tokens directly (`var(--stage-late)`,
  `var(--text)`, `var(--surface-card)`). `surfaces.css` re-inks them per surface;
  you write the variable once and it is correct on both.
- **Canvas / WebGL / Three.js** — cannot read CSS. Call `CortexPalette.hex(name)`
  (or `.stages()` / `.heat()` / `.emo()`), and re-read after the
  `cortex:surface-change` event. Never bake a literal hex.
- **Python / build-time** (`cortex_viz/core/workflow_graph_palette.py`) — cannot
  read CSS. Mirror the table below, keyed by the same names, both surfaces.

## Canonical data tokens (authoritative — oklch)

These are the design-system values (`tokens/colors.css` = ink, `tokens/surfaces.css`
= paper). Any off-browser consumer MUST reproduce these exactly.

### Consolidation stages (memory lifecycle)

| cortex stage | token | ink (on dark) | paper (on cream) |
|---|---|---|---|
| labile (new) | `--stage-labile` | `oklch(78% 0.15 230)` | `oklch(48% 0.12 230)` |
| early-LTP | `--stage-early` | `oklch(74% 0.14 190)` | `oklch(46% 0.11 190)` |
| late-LTP | `--stage-late` | `oklch(78% 0.13 155)` | `oklch(46% 0.11 155)` |
| consolidated | `--stage-cons` | `oklch(80% 0.13 95)` | `oklch(52% 0.11 95)` |
| semantic / recon | `--stage-recon` | `oklch(72% 0.14 320)` | `oklch(46% 0.12 320)` |

### Heat scale (memory temperature, hot → cold)

| token | ink | paper |
|---|---|---|
| `--heat-hot` | `oklch(78% 0.13 40)` | `oklch(55% 0.14 40)` |
| `--heat-warm` | `oklch(74% 0.10 60)` | `oklch(62% 0.11 60)` |
| `--heat-cool` | `oklch(64% 0.05 80)` | `oklch(72% 0.06 80)` |
| `--heat-cold` | `oklch(48% 0.02 80)` | `oklch(86% 0.02 85)` — fades INTO the page |

### Emotional valence

| token | ink | paper |
|---|---|---|
| `--emo-urgent` | `oklch(68% 0.17 28)` | `oklch(48% 0.16 28)` |
| `--emo-frustr` | `oklch(72% 0.15 55)` | `oklch(52% 0.13 55)` |
| `--emo-satisf` | `oklch(76% 0.13 155)` | `oklch(46% 0.11 155)` |
| `--emo-discov` | `oklch(74% 0.13 250)` | `oklch(48% 0.11 250)` |
| `--emo-conflct` | `oklch(72% 0.13 310)` | `oklch(50% 0.12 310)` |

### The accent (chrome may use this ONE colour, as a stamp — never as data)

`--accent` terracotta `oklch(64% 0.14 47)`; on paper, accent-as-text is
`--accent-deep oklch(50% 0.15 45)`. Amber `oklch(78% 0.13 75)` is the sibling
for "live / verified" warmth only.

## Re-inking cortex-specific data families (tools · kinds · edges · AST symbols)

cortex-viz carries data dimensions the design system does not name — tool
colours (Edit/Write/Read/Grep/Glob/Bash/Task), setup kinds (skill/command/hook/
agent/mcp), file clusters, AST symbol kinds, edge kinds. Today these are **neon
hexes tuned for a black canvas** (`#4ADE80`, `#A855F7`, `#22D3EE`, …) with
`0 0 6px` glows. They are legitimate *data* colour, but must be re-inked to the
same two-surface discipline as the tokens above — **do not keep neon-on-black**.

Deterministic rule, applied per family, keeping each item's source HUE so the
legend stays learnable:

- **paper (deep on cream):** lightness **46–55%**, chroma **0.11–0.16**.
- **ink (bright on dark):** lightness **74–80%**, chroma **0.13–0.15**.
- **Drop all glows** (`box-shadow: 0 0 …`) — lift comes from hairlines, not
  bloom. A legend dot is a flat filled disc.
- Keep hue families separated ≥ ~25° so tools/kinds remain distinguishable; where
  two items share a hue today (e.g. Grep/Glob magenta), separate them by
  lightness, not by adding a second accent.

Author these as CSS custom properties in the surface's own stylesheet (e.g.
`--tool-edit`, `--kind-agent`, `--edge-calls`) with an `[data-surface="paper"]`
override, so `palette.js` and the CSS legend read one truth. Then point the
inline legend/glossary dots and the JS/Python renderer palettes at those tokens.

## Provenance

`AI Architect` and the terracotta mark are a working brand pending incorporation
(final logo by Nolliram). The token names, values, and the paper doctrine are the
stable contract; the name is find-and-replace. See the design system's `README.md`
§ "Continuity & governance".
