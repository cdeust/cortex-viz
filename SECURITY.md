# Security Policy

## What cortex-viz accesses

cortex-viz is a visualization bridge over Cortex's data that never writes a
memory, but it does three things that shape its threat model, stated plainly
here:

- **It binds a local HTTP server.** The standalone server (`cortex_viz/`,
  `ui/`) listens on a local port and serves a browser UI. The UI is **91 JS
  files / ~25k lines executed in your browser**. A tampered UI asset runs in
  your browser with whatever that page can reach.
- **It reads the Cortex store and your artifacts.** It connects to the shared
  Cortex PostgreSQL store (`DATABASE_URL`, default
  `postgresql://127.0.0.1:5432/cortex`) and reads `~/.claude` artifacts to
  build the graphs.
- **It writes its own derived caches into that database.** Stated precisely,
  because an earlier version of this file said "does not write to that store"
  and that was wrong: cortex-viz never writes a memory, entity, or
  relationship, but it does create and write five tables of its own,
  `workflow_graph_snapshot`, `workflow_graph_snapshot_scoped`,
  `workflow_graph_layout`, `workflow_graph_layout_lod`, and `session_activity`.
  The last one persists a record of tool calls, file accesses, and skill
  invocations so the graph can stream live. See `PRIVACY.md`.

Because the browser-executed half is the exposed surface, the integrity of
the shipped UI is the thing that matters most, which is what the assurance
below is about.

## Supply-chain assurance

Before issue #37, cortex-viz had **no release workflow at all**: 2.7.1 was
cut by hand, so there was no build to trace an artifact back to. As of #37,
every release is produced by `.github/workflows/release.yml` and nothing
else, and it ships verifiable provenance:

- **Build provenance**: the wheel, the sdist, the SBOM and the UI fingerprint
  manifest each carry a Sigstore-backed build-provenance attestation. Verify a
  downloaded asset binds to this repository and workflow:

  ```bash
  gh attestation verify cortex-viz-ui-manifest.sha256 --repo cdeust/cortex-viz
  ```

- **UI fingerprint**: `cortex-viz-ui-manifest.sha256` is a `sha256sum` of
  every file under `ui/`, so the exact first-party JS your browser executes is
  pinned to the tagged commit and diffable against the previous release. It
  does **not** cover the three.js and 3d-force-graph bundles that four pages
  fetch from the unpkg CDN at page load: those are version-pinned but carry no
  Subresource Integrity hash, so they are outside this guarantee. Tracked in
  [#50](https://github.com/cdeust/cortex-viz/issues/50).

- **SBOM**: `cortex-viz.cdx.json` (CycloneDX, from `uv.lock`) enumerates the
  full Python dependency graph, including the `viz-tile` stack (datashader,
  pyarrow, numba/llvmlite).

- **Checksums**: every GitHub Release asset has a `<asset>.sha256` companion.

- **Continuous analysis**: CodeQL for **Python and JavaScript**
  (`codeql.yml`) and OpenSSF Scorecard (`scorecard.yml`) run on a schedule.
  The Scorecard number is a recorded baseline, not a badge.

**Not published to PyPI.** `cortex-viz` is a marketplace plugin, not a
`pip install` package (PyPI returns 404). The install path is the marketplace
pin in Cortex's manifest, so a tag + GitHub Release does not reach installs by
itself: the pin bump is the delivery step.

**What this does NOT claim.** Provenance proves *who built the artifact and
from which commit*, not that the source is free of defects; and it is worth
nothing to a user who does not run the verification. The marketplace install
path consumes the git tree directly, so its integrity is the tagged commit
plus its attestation.

## Reporting a Vulnerability

Do **not** open a public issue for a security problem. Open a
[private security advisory](https://github.com/cdeust/cortex-viz/security/advisories/new)
with the affected version/commit, reproduction steps, and impact.

### What happens next

cortex-viz has a single maintainer (see `GOVERNANCE.md`), so these are the
commitments one person can actually keep, rather than a service level borrowed
from a larger project:

| Step | Timeline |
|---|---|
| Initial acknowledgement of your report | within **14 days** |
| Assessment: severity, affected versions, whether it is exploitable | within **30 days** of acknowledgement |
| Fix released, or a written plan with a date if the fix is involved | as soon as practical, tracked in the advisory |
| Public disclosure | after the fix ships, in the advisory and the release notes |

You will be kept informed at each step in the advisory thread. If a report goes
unanswered past 14 days, escalate by email to admin@ai-architect.tools.

### Credit

Reporters are **credited by name in the security advisory and the release
notes** unless they ask not to be. Tell us in the report which name or handle
you want used, or that you would rather stay anonymous.

### Scope

In scope: the Python server (`cortex_viz/`), the browser UI (`ui/`), the
release and build workflows, and the plugin packaging. Out of scope: Cortex
itself, the Claude Code host, the plugin marketplace, and anything requiring
prior code execution as the user running the server (see
`docs/ASSURANCE_CASE.md`, section 2).

## OpenSSF Scorecard: checks a single-maintainer repo cannot max out

Scorecard alerts close only when a check reaches its **maximum** score, not
when it improves. Four checks are bounded below maximum by facts about this
repository rather than by anything in the code, so they are dismissed in code
scanning with a pointer to this section rather than left open as if they were
actionable. Each entry states what the checker actually requires — read from
`ossf/scorecard` source, not from the summary text — and what would close it.

| Check | Why it cannot reach maximum here | What would close it |
|---|---|---|
| **Code-Review** | Scores approved changesets. GitHub forbids approving your own pull request, and cortex-viz has one maintainer (`GOVERNANCE.md`), so the approved-changeset count is structurally 0/N. | A second maintainer with review rights. |
| **Branch-Protection** | The top tiers (`checks/evaluation/branch_protection.go`, Tier 4–5) require a non-zero required-reviewer count and CODEOWNERS review. Setting `required_approving_review_count >= 1` solo would deadlock every merge — no one can approve. Protection IS enabled at the tiers that do not deadlock: no force-push, no deletion, PRs required. | Same as Code-Review: a second reviewer. |
| **CII-Best-Practices** | `checks/evaluation/cii_best_practices.go` scores gold=10, silver=7, passing=5, in_progress=2; only **gold** is maximum. Gold requires `contributors_unassociated` — at least two significant contributors not associated with each other. | Two unassociated contributors. |
| **Maintained** | Scores 0 while the repository is younger than 90 days. Purely a function of creation date; no change to the repository affects it. | Time. |

The remaining Scorecard checks are treated as ordinary defects and fixed:
Token-Permissions (top-level `permissions:` in every workflow),
Pinned-Dependencies (no unpinned installer — the workflows use `uv` against the
committed `uv.lock`), Vulnerabilities (dependency advisories tracked and
patched), and Fuzzing (property-based tests, `tests/js/*.property.test.js`).
