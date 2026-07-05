**Select** — a native `<select>` skinned to match `Input` (mono, elevated surface, custom caret).

```jsx
<Select
  value={domain}
  onChange={e=>setDomain(e.target.value)}
  options={['all', 'retrieval', 'architecture', 'decision']}
/>
```

`options` accepts strings or `{value,label}`; or pass `<option>` children directly.
