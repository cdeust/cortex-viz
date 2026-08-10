# Roadmap

_Last updated: 2026-08-10._

Hypermnesia MCP Viz is a single-maintainer project (see [GOVERNANCE.md](../GOVERNANCE.md)),
so this roadmap states direction and known gaps rather than dated commitments.
Anything with an issue number is tracked; anything without one is an intention.

## Where the project is

Current version **3.1.1**. All six views (Graph, Brain, Trace, Knowledge, Wiki,
Board) are bridged to live data. The galaxy builds end to end at 75k+ nodes and
the 3D brain streams the full graph into a cortical mesh. Trace works with no
database at all. The supply-chain wave ([#37](https://github.com/cdeust/cortex-viz/issues/37))
added the release workflow, build-provenance attestation, an SBOM, a UI
fingerprint manifest, CodeQL for both languages, and OpenSSF Scorecard. The
browser UI got its first test harness in
[#35](https://github.com/cdeust/cortex-viz/issues/35).

## Near term: improve the bus factor

The 2026-08-03 assurance wave closed the CodeQL backlog (#46), exercised and
independently verified the v2.8.0 release path (#47), raised Python statement
coverage above the required 80% (#44), and earned OpenSSF Best Practices
Silver. One continuity improvement remains before Gold is credible.

| Work | Issue | Why it is first |
|---|---|---|
| Add a second maintainer with admin and release rights | [#48](https://github.com/cdeust/cortex-viz/issues/48) | Preserve the original repository identity and improve the bus factor beyond the documented MIT fork continuity path |

The strict Ruff/ESLint gates in #45 and the JavaScript test-harness dependency
remediation in #49 are complete; they are no longer listed as open work.

## Medium term

- **Coverage honesty everywhere.** The indicator landed for the graph and trace
  views in [#36](https://github.com/cdeust/cortex-viz/issues/36). The remaining
  four views should answer "what is missing from what I am looking at?" the
  same way, rather than rendering a thinner picture silently.
- **Structural debt.** Continue the file and function size work started in
  [#41](https://github.com/cdeust/cortex-viz/issues/41),
  [#17](https://github.com/cdeust/cortex-viz/issues/17), and
  [#23](https://github.com/cdeust/cortex-viz/issues/23).
- **Cross-platform.** Windows launch portability is partly addressed
  ([#13](https://github.com/cdeust/cortex-viz/issues/13)); it needs a real
  verification pass rather than a fix per report.
- **Offline-first brain view.** The 3D brain currently fetches three.js from
  the public unpkg CDN at page load, the one thing that leaves the machine
  ([PRIVACY.md](../PRIVACY.md)). Vendoring it would make every view
  offline-safe and remove a third-party runtime dependency.

## Longer term

- **OpenSSF Best Practices Gold.** Hypermnesia MCP Viz [earned Silver on
  2026-08-03](https://www.bestpractices.dev/projects/13846). The evidence is in
  `.bestpractices.json`. Gold additionally needs a second maintainer and
  two-person review, which depends on
  [#48](https://github.com/cdeust/cortex-viz/issues/48).
- **Raise the Scorecard score** from its 7.4 baseline. The remaining major
  deductions are two-person code review, repository age/contributor diversity,
  and a branch-protection result that the Scorecard token cannot currently
  inspect; token permissions, dependency updates, SAST, fuzzing, packaging, and
  pinned dependencies already score 10.

## Not planned

- **A separate legacy `cortex-viz` PyPI distribution.** The canonical PyPI and
  MCP Registry name is `hypermnesia-mcp-viz`, matching the published sibling
  `hypermnesia-mcp`. The legacy console shim that 2.8.0 exposed is removed as
  of 3.1.0 (the removal was cut into the tree as 3.0.0, which was never
  tagged or published); publishing a second distribution would split the
  release identity instead of completing the migration.
- **Writing to Cortex's memory tables.** Hypermnesia MCP Viz renders, it never
  remembers. That boundary is the point of the extraction and is not up for
  negotiation.
- **A remote or multi-user deployment.** The server binds 127.0.0.1 and has no
  authentication by design. Exposing it would require a different threat model
  and a different product.
