**Chip** — a filter pill for the graph/table filter bars. Toggles pressed state; the optional `count` turns terracotta when active.

```jsx
<Chip active={type==='all'} count={87} onClick={()=>setType('all')}>All</Chip>
<Chip active={type==='semantic'} count={12} onClick={()=>setType('semantic')}>Semantic</Chip>
```

Use for mutually-exclusive or multi-select filters. For view switching use `Tabs` instead.
