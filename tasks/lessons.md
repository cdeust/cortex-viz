# Lessons

## 2026-08-05 — Trace is an activity view, not a domain catalogue

- A same-view incremental optimisation must preserve the target view's layout
  invariants. Reusing Galaxy's generic append path for Trace without rebuilding
  topology, slots and adjacency is a correctness regression even if it reduces
  render cost.
- A declared live cursor is not a live feature. Pin the cursor initialisation,
  transport routing and visible-session projection with end-to-end tests.
- Render only observed host activity. Add explicit event vocabulary where the
  producer supplies evidence; do not guess API or database access from labels.
- A durable replay cursor and an in-memory wake-up cursor are different
  namespaces. Capture the wake-up position before replay, emit PostgreSQL ids
  on the wire, and deduplicate their overlap by durable id.
- An allowlist of tool names silently turns new MCPs and skills into missing
  history. For an evidence trace, accept every named tool event and classify
  special cases only to enrich them, never to decide whether they existed.

## 2026-08-05 — Stable is not the same as legible

- Preserving the clicked node and camera is only one Trace invariant. A local
  expansion also has to resolve collisions, redistribute the affected cluster
  with visible motion, and keep unrelated clusters stable.
- Do not validate an expansion only with a maximum-radius measurement. Measure
  pairwise overlap at rendered radii and inspect the real large-session case;
  a bounded but overdrawn disk is still a failed layout.
- Transport streaming and visual streaming are separate contracts. A complete
  historical response must be revealed across render frames instead of being
  injected as one opaque batch, while genuinely live events retain causal
  order and are never dropped during the reveal.
- A collision-free final target does not justify a delayed terminal snap. If a
  force animation looks settled before its `end` event, assigning exact target
  coordinates in that callback creates a second visual phase. Measure the
  frame-to-frame trajectory through final pinning and require continuous
  convergence with no motion after the first stable period.
- The locality boundary for a Trace expansion is the affected domain, not the
  selected session. Treating sibling sessions as fixed obstacles only squeezes
  the selected session's children into leftover gaps; adjacent session hubs and
  their already-loaded children must yield coherently while other domains stay
  fixed.
- A Wiki → Trace roundtrip is a layout-parity oracle. If remounting the same
  accumulated payload produces a clearer geometry, the incremental and full
  paths implement different invariants; share the domain packer and test that
  both paths converge to the same targets.
- Cancellation is not composition. When independent streaming deltas share one
  animation token, a replacement animation must inherit unfinished targets or
  it strands the first domain between layouts. Test both back-to-back appends
  before frame one and interleaving after a partial frame.
- Collision freedom must be global even when reflow is local. Keep domain
  anchors fixed, let only canonically displaced children yield, and use a
  radius-derived spatial index so micro-batch streaming does not turn an exact
  collision pass into cumulative cubic work.
- A correct final layout does not justify animating every canonical coordinate
  change. Streamed micro-batches need movement significance/coalescing, or tiny
  slot deltas make the whole domain visibly fidget even though the end state is
  readable.
- Retargeting is not progress if it resets the animation horizon. Rapid clicks
  and streamed appends must update an already-running scheduler without
  restarting its completion clock; termination needs an explicit regression
  test under sustained retargeting.
- A scheduler's last painted timestamp is not a valid start time for a node
  that arrived after that paint. Browser throttling can make it arbitrarily
  stale and collapse the whole transition into one frame; start a new node's
  budget on its first actual rAF while preserving deadlines only for nodes
  whose animation has already begun.
- Accepting Trace geometry does not validate the UI derived from that graph.
  Every incrementally introduced node kind must update its legend entry and
  exercise its selection-to-detail route; otherwise the canvas can look right
  while its explanatory and drill-down surfaces remain stale or inert.
- A collision-free graph can still communicate the wrong process. Trace layout
  must derive its primary axis and stable ordering from directed causal edges;
  packing and collision avoidance are secondary constraints, not the story.
- Test asynchronous detail enrichment in both arrival orders. A panel opened
  from a sparse node must refresh when that same live node later gains its
  authoritative path, and an older failed lookup must not overwrite the newer
  render.
- Topological monotonicity is not visual causality. A radial order can satisfy
  every directed-edge assertion while producing a worse real trajectory and a
  less readable graph. Do not accept another Trace layout from synthetic slot
  tests alone: record the actual browser motion on the PostgreSQL graph, compare
  it with the previous implementation, and make that visual evidence a gate.
