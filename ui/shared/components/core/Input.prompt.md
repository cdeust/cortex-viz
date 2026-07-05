**Input** — a mono text / search field in the command-palette style; elevated surface, terracotta focus ring. Pass `icon` for an inset leading glyph (e.g. a magnifier for search).

```jsx
<Input placeholder="Search memories…" icon={<SearchIcon />} />
<Input value={q} onChange={e=>setQ(e.target.value)} />
```

For a dropdown use `Select`.
