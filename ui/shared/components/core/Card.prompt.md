**Card** — the base bordered surface. Optional `title` + `aside` build the header row; `elevated` for popovers/dialogs, `flush` for edge-to-edge content.

```jsx
<Card title="Hot memories" aside={<Badge tone="accent">26 active</Badge>}>
  …rows…
</Card>

<Card elevated>Floating panel</Card>
```
