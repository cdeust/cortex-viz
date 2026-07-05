**Tabs** — the primary view switcher used across the products (Graph · Timeline · Knowledge · Wiki). Underline + color mark the active view; nothing louder.

```jsx
const [view, setView] = React.useState('graph');
<Tabs
  value={view}
  onChange={setView}
  items={[
    { id: 'graph', label: 'Neural Graph' },
    { id: 'timeline', label: 'Timeline' },
    { id: 'knowledge', label: 'Knowledge' },
  ]}
/>
```

Controlled only. `items` accepts plain strings or `{id,label}`.
