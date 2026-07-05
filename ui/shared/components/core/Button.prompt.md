**Button** — the primary action primitive; terracotta `primary` for the main action, `secondary`/`ghost` for supporting actions, `danger` for destructive or "blocked" flows.

```jsx
<Button variant="primary" size="md" onClick={run}>Analyze codebase</Button>
<Button variant="secondary" icon={<ResetIcon />}>Reset view</Button>
<Button variant="ghost" size="sm">Cancel</Button>
<Button variant="danger" size="sm">Revert commit</Button>
```

Variants: `primary` (default, terracotta), `secondary` (bordered surface), `ghost` (text-only), `danger`. Sizes: `sm` · `md` · `lg`. Pass `icon` for a leading glyph, `disabled` to dim + block.
