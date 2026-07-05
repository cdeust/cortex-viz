**Legend** — the data key that sits under a graph or chart. Dots for node types, thin lines for edge types. Always color from the data tokens, never chrome.

```jsx
<Legend items={[
  { label: 'Episodic', color: 'var(--stage-late)' },
  { label: 'Semantic', color: 'var(--stage-recon)' },
  { label: 'Causal edge', color: 'var(--danger)', line: true },
]} />
```
