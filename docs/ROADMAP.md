# Roadmap

_Last updated: 2026-07-28._

cortex-viz is a single-maintainer project (see [GOVERNANCE.md](../GOVERNANCE.md)),
so this roadmap states direction and known gaps rather than dated commitments.
Anything with an issue number is tracked; anything without one is an intention.

## Where the project is

Current version **2.8.0**. All six views (Graph, Brain, Trace, Knowledge, Wiki,
Board) are bridged to live data. The galaxy builds end to end at 75k+ nodes and
the 3D brain streams the full graph into a cortical mesh. Trace works with no
database at all. The supply-chain wave ([#37](https://github.com/cdeust/cortex-viz/issues/37))
added the release workflow, build-provenance attestation, an SBOM, a UI
fingerprint manifest, CodeQL for both languages, and OpenSSF Scorecard. The
browser UI got its first test harness in
[#35](https://github.com/cdeust/cortex-viz/issues/35).

## Near term: close the assurance gaps

These are the open items that the [assurance case](ASSURANCE_CASE.md) and the
OpenSSF Best Practices answers name as incomplete. They come before new
features.

| Work | Issue | Why it is first |
|---|---|---|
| Triage the 192 open CodeQL alerts, starting with the 10 `py/path-injection` and 2 `js/remote-property-injection` highs | [#46](https://github.com/cdeust/cortex-viz/issues/46) | Untriaged findings on the untrusted-data boundary are the one thing that keeps the assurance case incomplete |
| Cut a release through `release.yml` so the attestation path is exercised | [#47](https://github.com/cdeust/cortex-viz/issues/47) | The pipeline exists but no tag has ever run through it |
| Raise Python statement coverage from 34% toward 80%, and make JS coverage measurable at all | [#44](https://github.com/cdeust/cortex-viz/issues/44) | Both numbers are currently unusable as evidence |
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

- **OpenSSF Best Practices silver**, then gold. cortex-viz is registered as
  [project 13846](https://www.bestpractices.dev/projects/13846). The answers
  and the current blockers are in `.bestpractices.json`; the two remaining
  silver MUSTs are statement coverage (#44) and an exercised signed release
  (#47). Gold additionally needs a second maintainer and two-person review,
  which depends on
  [#48](https://github.com/cdeust/cortex-viz/issues/48).
- **Raise the Scorecard score** from its 3.6 baseline, principally branch
  protection, token permissions, and the dependency-update tool.

## Not planned

- **A separate legacy `cortex-viz` PyPI distribution.** The canonical PyPI and
  MCP Registry name is `hypermnesia-mcp-viz`, matching the published sibling
  `hypermnesia-mcp`; that package includes the legacy `cortex-viz` command, so
  publishing a second distribution would split the release identity without
  adding compatibility.
- **Writing to Cortex's memory tables.** cortex-viz renders, it never
  remembers. That boundary is the point of the extraction and is not up for
  negotiation.
- **A remote or multi-user deployment.** The server binds 127.0.0.1 and has no
  authentication by design. Exposing it would require a different threat model
  and a different product.
