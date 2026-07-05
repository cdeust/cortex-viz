**ReactorDot** — the single looping element in the system: a blinking indicator that a process is live / synchronized. Sits in panel headers and status bars.

```jsx
<ReactorDot />                              {/* terracotta, blinking */}
<ReactorDot tone="ok" label="Synchronized" />
<ReactorDot tone="idle" live={false} label="Paused" />
```
