**HeatBar** — visualizes a memory's temperature (importance × recency) as a cold→hot gradient fill. `value` is 0..1.

```jsx
<HeatBar value={0.796} />
<HeatBar value={0.12} label="Saturation" showValue={false} />
```

The fill gradient runs cold → cool → warm → hot, matching the heat data tokens.
