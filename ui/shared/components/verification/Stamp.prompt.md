**Stamp** — the verdict made physical: a tilted, ink-textured rubber-stamp certification. Use it where a document is *pronounced* proven/blocked (dossier headers, report sign-offs, deck titles); use `ProofBadge` for rows and inline UI.

```jsx
<Stamp label="Proven" sub="n=500 · 2026-07-04" />
<Stamp label="Blocked" tone="danger" sub="2 violations" tilt={2} />
<Stamp label="Unsourced" tone="neutral" clean />
```

Tones: `accent` (terracotta, default) · `ok` · `danger` · `neutral`. `tilt` rotates (default −3°); `clean` removes the ink texture for crisp print.
