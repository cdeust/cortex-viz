# Cortex Atom Memory Graph — Design System

## Design reference: AI Architect (paper-led proof language)

Re-skinned off the shared `ui/shared/` design-system foundation (see
`ui/shared/README.md` for the token contract and re-inking method). The
previous "Cyber Obsidian" language — black canvas, Orbitron display type,
neon green/cyan/magenta, selective bloom, vignette, film grain — has been
retired in full. It stood apart from the rest of the ecosystem's surfaces
and contradicted the brand doctrine (`AI Architect Design System/README.md`
§3): *"Every memory tool wears the same uniform: black canvas, glowing
neurons... This brand refuses the costume."*

**Served at `/atom` by the standalone visualization server**
(`cortex_viz/server/http_standalone_routes.py`, `serve_static(... "atom-viz.html")`),
loading `/dashboard/theme.css`, `/dashboard/panels.css`, and `/dashboard/js/*`.
This surface is live — not superseded by `ui/unified/`.

---

## Layout Architecture

```
+--[SIDEBAR 200px]--+--[MAIN AREA]--------------------------------------+
|                    | [TOP NAV BAR - tabs + search + status]            |
| Cortex             |                                                   |
| atom               | [KPI STRIP - metric cards]                       |
|                    |                                                   |
| > Atom Graph       |                                                   |
| > Timeline         |            [3D FORCE GRAPH]                      |
| > Categories       |            Atom-shell layout                     |
|                    |                                                   |
| [Analytics]        | [BOTTOM BAR - legend + status]                   |
+--------------------+---[DETAIL PANEL 400px]---------------------------+
                     | Type badge | Close                                |
                     | Title                                             |
                     | Properties grid                                   |
                     | Classifiers (tags)                                |
                     | Proximal Links (connections)                      |
                     | Saturation Bar (heat)                             |
                     +---------------------------------------------------+
```

Desktop-only — no responsive breakpoints (dev/ops tool).

---

## Surface posture

Boots on **paper** (cream record, deep data inks — `data-surface="paper"`,
stamped by `/shared/surface-toggle.js` before first paint). The top-right
toggle button switches to `ink` — the legacy warm-dark instrument — and
persists the choice.

---

## Colour — authored against the shared token contract

Chrome (sidebar, top bar, panels, chips, borders) is **greyscale**, authored
against the shared semantic aliases — never raw hex:

`--canvas` · `--surface` · `--surface-card` · `--surface-chip` ·
`--surface-input` · `--border` · `--border-strong` · `--divider` ·
`--text` · `--text-secondary` · `--text-muted` · `--text-faint` ·
`--accent-ink` (the ONE brand accent, terracotta — used for the logo mark,
active nav state, KPI values, and the interaction highlight ring).

**All other colour comes from data** (`ui/dashboard/theme.css` §"Data
family" blocks; consumed by JS via `/shared/palette.js`'s
`CortexPalette.hex(...)`, never a baked literal):

| Data family | Tokens | Mapping |
|---|---|---|
| Memory `store_type` | `--node-episodic/-semantic/-entity` | Reuses the canonical lifecycle-stage tokens directly: episodic → `--stage-labile` (new/raw), semantic → `--stage-recon` (extracted schema — palette.js's own mapping), entity → `--stage-cons` (stable knowledge-graph fact) |
| Relationship/edge kind | `--edge-causal/-cooccurrence/-default/-virtual/-highlight` | causal → `--emo-urgent`, co-occurrence → `--emo-discov`, default/virtual → neutral chrome grey (a weak relation is not itself a datum), highlight → `--accent-ink` |
| Consolidation lifecycle (analytics bars) | `--stage-*` | 1:1 with the design system's own five-stage vocabulary (McClelland et al. 1995; Foster & Wilson 2006) |
| Heat scale | `--heat-hot/-warm/-cool/-cold` | Direct read of the design system's hot→cold data tokens |
| Agent/team identity | `--agent-*` (11 hues) | No canonical token exists for "team member" — hues are evenly spaced 360°/11 ≈ 32.7° apart (deterministic, not approximated from the legacy neons); ink L78%/C0.14, paper L50%/C0.13, per `shared/README.md`'s re-inking rule |
| Category taxonomy (decision/architecture/error/session/knowledge/other) | reuses `--accent-ink`, `--info-ink`, `--danger-ink`, `--emo-discov`, `--node-semantic`, `--text-muted` | Chip background is chrome-neutral (`--surface-chip`); only the icon/label colour carries the data meaning |

---

## Typography

Three voices, per the design system (`AI Architect Design System/README.md` §3):

| Role | Font | Where |
|---|---|---|
| Chrome / labels / nav | `--font-sans` (Inter Tight) | Sidebar nav, buttons, filters, search |
| Metrics / identifiers / proof | `--font-mono` (JetBrains Mono) | KPI values, logo mark, type badges, meta values, node/edge counts, chart labels |
| Prose | `--font-serif` (Newsreader) | Not currently used on this surface (no long-form prose) |

Orbitron has been removed entirely — the previous Google Fonts `<link>` for
Orbitron/JetBrains Mono in `ui/atom-viz.html` is gone; fonts now load via
`/shared/ds.css` → `tokens/fonts.css`.

**Casing:** UPPERCASE reserved for small mono micro-labels (KPI labels, type
badges, section headers) with wide tracking, matching the verification-lexicon
doctrine.

---

## Components

Same component inventory as before (sidebar nav, KPI strip, top nav, 3D atom
graph, detail panel, bottom status bar, analytics panel, timeline/categories
overlays) — chrome only was re-inked. See `ui/dashboard/theme.css` and
`panels.css` for the authored rules.

### Neural Graph (3D) — what changed

- **No selective bloom.** The `EffectComposer`/`UnrealBloomPass` pipeline and
  its script tags were removed from `ui/atom-viz.html`; rendering is a plain
  `renderer.render(scene, camera)` call (`js/raycast.js` `animate()`).
- **No vignette, no film grain.** The post-process shader pass that combined
  both is gone (`js/scene.js`).
- **No glow halo sprites.** Memory/entity nodes are flat `MeshStandardMaterial`
  spheres/octahedrons with a minimal emissive term (0.12–0.15) for legibility
  against the fog — not a bloom source.
- **Lights are neutral.** The three coloured point lights (cyan/magenta/green)
  were replaced with one neutral ambient + one neutral directional light;
  colour comes from the node/edge materials only.
- **Protected-memory ring** is a flat wireframe torus in `--warn-ink` (amber,
  "verified/live"), not a glow.
- **Team/global indicator** is a small flat agent-coloured marker dot, not an
  additive glow sprite.
- **Ambient dust particles** (`js/effects.js`) are neutral chrome grey
  (`--text-faint`), non-additive blending, very low opacity — a depth cue,
  not decorative grain.
- Canvas re-tints its background/fog on `cortex:surface-change` (paper ↔ ink)
  by re-reading `CortexPalette.hex('--canvas')`.

### Detail Panel (Right Slide-in)

Unchanged structure; chrome tokens only.

### Bottom Status Bar

Unchanged structure. The "SYNCHRONIZED" live indicator uses the design
system's one permitted looping animation — the reactor dot (`--glow-ok`,
`dot-blink` 2.2s) — not a decorative loop.

---

## Interaction Patterns

Unchanged from the previous version (hover/click/search/filter behavior) —
see `js/raycast.js`, `js/interaction.js`. Selection highlight ring recolored
to `--accent-ink`; edge highlight recolored to `--edge-highlight`.

---

## File Structure

```
ui/dashboard/
  DESIGN.md          # This file
  theme.css          # Chrome + data-family token definitions
  panels.css         # Panels, overlays, timeline, categories
  js/
    config.js        # Colour bridge: CortexPalette → JMD.* constants
    state.js         # Reactive state + event bus
    scene.js         # Three.js setup (no bloom/vignette/grain)
    nodes.js         # Memory + entity node builders (flat colour)
    edges.js         # Edge lines, fiber tracts, flow particles
    edge_fx.js       # Per-frame edge/particle updates, highlight/reset
    effects.js       # Ambient dust (neutral, non-additive)
    atom.js          # Atom-shell shell layout + shell guides
    raycast.js       # Raycasting, selection, animation loop, direct render
    graph.js         # Force layout glue, data build
    interaction.js   # Tooltip, detail panel, keyboard shortcuts
    timeline.js       # Timeline overlay view
    categories.js    # Categories overlay view
    analytics.js     # Analytics panel charts (data-token colours)
    controls.js      # UI button handlers
    polling.js       # API polling
    stats.js         # Header stats bar
```

---

## API Contract

Unchanged — **Endpoint**: `GET /api/dashboard`. See `cortex_viz/server/http_dashboard_data.py`.
