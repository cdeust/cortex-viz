# Assurance case

_Last reviewed: 2026-07-28, against commit `f563f72`._

This document argues why cortex-viz's security requirements are met, and states
where the argument is currently incomplete. It is written for a reader who
wants to disagree with it: every claim points at a file, a workflow, or an
alert count that can be checked.

The structure of the system is in [ARCHITECTURE.md](ARCHITECTURE.md). The
reporting process is in [SECURITY.md](../SECURITY.md).

## 1. What is being protected

cortex-viz stores nothing and mints no credentials. The assets are therefore
other people's:

| Asset | Why it matters here |
|---|---|
| **The user's local filesystem** | The server reads `~/.claude` artifacts and serves files by request-derived path. An unconstrained read is arbitrary local file disclosure. |
| **The Cortex PostgreSQL store** | Read-only by contract. Memory content is the user's private reasoning history. |
| **The browser execution context** | 98 JS files, ~26k lines, execute in the user's browser. Code that runs there reaches whatever that page reaches. |
| **The shipped artifact's integrity** | The install path is a marketplace pin over a git tree. A tampered `ui/` file is a tampered product. |
| **The user's `DATABASE_URL`** | User-supplied configuration that may embed a credential. |
| **cortex-viz's own tables in that database** | It writes five derived-cache tables of its own: `workflow_graph_snapshot`, `workflow_graph_snapshot_scoped`, `workflow_graph_layout`, `workflow_graph_layout_lod`, and `session_activity`. |

**A precision the older docs got wrong.** cortex-viz is read-only with respect
to **Cortex's memory tables**: it never writes a memory, entity, or
relationship, and that is the property the read contract is about. It is
**not** a read-only database client. It creates and writes the five tables
listed above in the same database, and `session_activity` in particular
persists a record of tool calls, file accesses, and skill invocations so the
graph can stream live. `SECURITY.md` and `PRIVACY.md` previously stated a
blanket "does not write" / "read-only"; both were corrected in the same change
that added this document.

## 2. Threat model

The deployment shapes the model: the server binds **127.0.0.1 only** and is
launched by the user's own MCP host. There is no multi-tenancy, no remote
authentication, and no network listener beyond loopback.

| # | Adversary | Reach | STRIDE | Countered by |
|---|---|---|---|---|
| A1 | **A web page the user visits** | Can issue cross-origin requests to `127.0.0.1:<port>` from the user's browser, including via DNS rebinding | Information disclosure, Tampering | §3.1 |
| A2 | **A crafted request path** (from A1, or any local process) | Reaches static serving, wiki reads, git diff paths | Information disclosure (arbitrary file read) | §3.2 |
| A3 | **Untrusted content rendered in a view** | Memory text, session transcripts, wiki pages, file paths, all rendered into the DOM | Elevation (XSS in the page's context) | §3.3, **open findings** |
| A4 | **A tampered release artifact or dependency** | Substituted wheel, altered `ui/` asset, malicious transitive package | Tampering | §3.4 |
| A5 | **Another local process on the machine** | Can connect to the loopback port | Information disclosure | §3.5, accepted |
| A6 | **A malicious or compromised Cortex store** | Supplies the graph data the views render | Tampering, Elevation (feeds A3) | §3.3 |

**Explicitly out of scope.** A user who already has code execution as the
running account: they can read `~/.claude` and the database directly, with no
need of this software. cortex-viz does not defend against its own operator.

## 3. The argument

### 3.1 Browser-to-server boundary (A1): three independent controls

`cortex_viz/server/http_security.py` implements three checks with different
failure modes, so no single bug opens the boundary:

1. **Host-header allowlist** (`validate_host_header`): the request's `Host`
   must name a loopback host. Counters DNS rebinding (CWE-346, CWE-350), which
   binding to 127.0.0.1 alone does **not** stop. A browser cannot forge `Host`,
   so checking it here is sound.
2. **Origin allowlist with control-character filtering**
   (`resolve_allowed_origin`, `_is_safe_header_value`): counters permissive
   CORS (CWE-942) and response-header splitting (CWE-113). Python's
   `send_header` does not filter CR/LF, so the filter is not redundant with the
   standard library.
3. **Same-origin check on writes** (`enforce_same_origin_write`): counters CSRF
   (CWE-352).

Independence argument: control 1 fails if hostname parsing is wrong, control 2
if origin comparison is wrong, control 3 if the write path skips the check.
A defeat of one does not imply a defeat of the others, and controls 1 and 3
must **both** fail for a cross-site write to land.

### 3.2 Request-derived filesystem paths (A2): contained

Every path built from a request crosses one guard,
`cortex_viz/shared/path_containment.resolve_under`: `~`-expand, resolve
symlinks, then require the RESOLVED path to sit under a separator-terminated
prefix of the resolved base. It returns the proven path or `None`, never a
boolean, so a caller cannot use a value the guard did not sanction. Two
properties are deliberate — resolving before comparing (a symlink planted
inside the base otherwise passes a textual check and dereferences outside
it), and the trailing separator (`/srv/wiki-backup` is not inside
`/srv/wiki`). Both are pinned by tests in `tests/test_path_containment.py`,
and the module carries 0 surviving mutants.

The four readers on top of it:

| Reader | Base |
|---|---|
| `serve_static` (`/js/`, `/css/`) | directory-listing whitelist, then the served directory |
| `serve_shared_asset` (`/shared/`) | the design-system foundation directory |
| `wiki_read._safe_path` | `WIKI_ROOT`, plus a `.md`/`.bib` suffix gate |
| `git_diff_engine._sandboxed_abs_path` | `infrastructure.file_sandbox.readable_roots` |

**The last one closed a real defect, not a false positive.** `/api/file-diff`
and `/api/trace/file` take a filesystem path from the request; the
absolute-path branch passed it to the diff engine with no boundary at all, so
any file inside any git repository anywhere on the machine came back in full
as a `diff_type: "untracked"` patch. Reproduced 2026-07-28 against a throwaway
repository outside every configured root, and pinned by a paired regression
test (same repo, same file, sandbox root listed vs unlisted) in
`tests/test_file_sandbox.py`. Exposure was bounded by §3.1 — a page on the
public internet cannot read the response, because CORS reflects only loopback
origins — but a page served from any other loopback port could.

The readable roots are the places the graph's file nodes actually come from:
the configured development roots, `~/.claude`, and the temp roots agent
scratchpads use. Measured against the live activity spine on 2026-07-28, all
1069 graphed absolute paths fall inside them and none outside, so the boundary
costs no reachable functionality.

The 10 `py/path-injection` alerts (high) first raised 2026-07-25 are all
resolved: one root-cause fix plus three guards rewritten from correct-but-
unmodelled forms (`os.path.commonpath`, `base in target.parents`) into the
`str.startswith` form CodeQL models as a `Path::SafeAccessCheck`. Verified by
running the query locally on the tree before and after — 10 alerts to 0, with
a paired full `python-security-and-quality` run confirming no other rule
changed count (147 to 137).

### 3.3 Untrusted content rendered into the DOM (A3, A6)

Every string a view renders comes from a source the project does not control:
memory content, session transcripts, wiki pages, file paths, commit messages.
The views are vanilla DOM code with no framework escaping by default, so this
is a real class here rather than a theoretical one.

The **2 `js/remote-property-injection` (high)** findings in
`ui/brain/js/search_worker.js` and `ui/brain/js/trigram.js` are closed. Triage
result: not exploitable as rated — both sinks wrote into `Object.create(null)`
maps, which absorb `__proto__`/`constructor` as own properties, and a
reproduction confirmed `Object.prototype` unchanged across every hostile key
the tokenizer can emit. The null prototype was nonetheless load-bearing, and
what it prevented was a search-correctness defect rather than a pollution one:
backed by a plain object, `seen['constructor']` is truthy before any write, so
a node labelled `constructor` would be dropped from the index and become
unfindable. Both maps are now `Set`s. The claim rests on
`tests/js/trigram.test.mjs` "trigram dedup does not collide with inherited
property names" — 5 tests, verified to fail against the object-backed
implementation, so the property is pinned rather than merely currently-true.

Open findings: **4 `js/incomplete-html-attribute-sanitization` (medium)**, in
the same untriaged batch as §3.2 and covered by
[#46](https://github.com/cdeust/cortex-viz/issues/46). This section does not
yet carry a completed argument.

### 3.4 Artifact and dependency integrity (A4)

- **One build path.** `.github/workflows/Release.yml` is the only way a release
  is produced. Before it existed, 2.7.1 was cut by hand, so there was no build
  to trace an artifact to.
- **Build provenance.** The wheel, sdist, SBOM, and UI manifest each carry a
  Sigstore-backed attestation binding the artifact digest to this repository,
  workflow, and commit. Verify with
  `gh attestation verify <file> --repo cdeust/cortex-viz`.
- **UI fingerprint.** `cortex-viz-ui-manifest.sha256` pins every byte under
  `ui/` to the tagged commit, and is itself attested. This is the control that
  matches the asset in §1: the browser-executed half. **Scope limit**: it
  covers first-party assets only, not the CDN-loaded libraries (§6, item 3).
- **SBOM.** CycloneDX generated from `uv.lock`, covering the Python graph
  including the heavy `viz-tile` stack.
- **Pinned actions.** Every GitHub Action is pinned by commit SHA, so a
  re-pointed tag cannot change what runs.
- **Continuous analysis.** CodeQL (`security-and-quality`, both languages) on
  every push and pull request plus a weekly cron, because a new query pack
  lands against unchanged code and inaction never opens a pull request. OpenSSF
  Scorecard weekly.

**Known gap.** No release has yet been produced by this workflow: 2.7.1 predates
it by one day. The pipeline is in place and untested by a real tag. Tracked in
[#47](https://github.com/cdeust/cortex-viz/issues/47).

**Known gap.** Dependency vulnerability monitoring was not automated until now;
`.github/dependabot.yml` in this change adds it. A manual `npm audit` on
2026-07-28 reported 19 advisories (2 critical, 7 high), **all in
`devDependencies`** (the Stryker and vitest test harness). None are in the
shipped wheel or in `ui/`: `package.json` declares no runtime dependencies at
all. Remediation tracked in
[#49](https://github.com/cdeust/cortex-viz/issues/49).

### 3.5 Local process reach (A5): accepted risk

Any process running as the user can connect to the loopback port while the
server is running. This is inherent to a local developer tool with no
authentication, and it is not a privilege gain: such a process can already read
`~/.claude` and the database directly. **Accepted, with two limits**: the
server is short-lived (an idle watchdog stops it) and it is read-only with
respect to the Cortex store. Documented for the user in
[PRIVACY.md](../PRIVACY.md).

## 4. Secure design principles applied

| Principle | How it shows up here |
|---|---|
| **Least privilege** | Cortex's memory tables are read **only**: cortex-viz never writes a memory, entity, or relationship, and the ban on `import mcp_server.*` keeps the separation structural. It is not a read-only database user: it creates and writes five tables of its **own** derived caches in the same database (see §1). Workflow tokens are `permissions: read-all` by default, widened per job only where a job must write (SARIF upload, release assets, attestations). |
| **Economy of mechanism** | No bundler, no framework, no plugin system, no authentication subsystem. There is less to get wrong because there is less. |
| **Fail safe defaults** | Binds 127.0.0.1, never `0.0.0.0`. Unreachable database degrades to a named no-DB mode rather than erroring or inventing data. Unknown static path returns 403 or 404, never a guess. |
| **Complete mediation** | Host, Origin, and same-origin checks run per request in the handler path, not once at startup. |
| **Defence in depth** | §3.1's three independent controls; §3.4's provenance plus fingerprint plus SBOM plus checksums. |
| **Open design** | Public repository, MIT, public issue history. No security property depends on the source being secret. |
| **Explicit degraded modes** | A view that cannot show everything says so, in the coverage indicator, rather than rendering a thinner picture silently. |

## 5. Common implementation weaknesses

| Weakness | Status |
|---|---|
| **Injection (SQL)** | Countered. All store access is parameterised read-only queries through `psycopg`; no string-built SQL. |
| **Injection (command)** | git is invoked with argument lists, never a shell string. |
| **Path traversal (CWE-22)** | Countered. One containment guard behind every request-derived path (§3.2); the unbounded diff-endpoint read it uncovered is fixed and regression-tested; 10 `py/path-injection` alerts closed. |
| **XSS (CWE-79)** | **Open findings**, see §3.3 and [#46](https://github.com/cdeust/cortex-viz/issues/46). |
| **CSRF (CWE-352)** | Countered, `enforce_same_origin_write`. |
| **DNS rebinding (CWE-346/350)** | Countered, `validate_host_header`. |
| **Permissive CORS (CWE-942)** | Countered, origin allowlist. |
| **Header injection (CWE-113)** | Countered, control-character filter. |
| **Hardcoded credentials (CWE-798)** | None. `DATABASE_URL` is user configuration; no secret is committed. Scorecard and CodeQL run continuously against the tree. |
| **Broken cryptography** | Not applicable: cortex-viz implements no cryptography, stores no passwords, and mints no keys or tokens. Signing is delegated to Sigstore through GitHub's attestation action. |
| **Supply-chain tampering** | Countered for first-party artifacts, §3.4. **Not** countered for the CDN-loaded libraries, [#50](https://github.com/cdeust/cortex-viz/issues/50). |

## 6. What this case does not cover

Stated so the boundary of the argument is legible:

1. **Cortex itself.** cortex-viz reads Cortex's store. The integrity and
   confidentiality of that store are Cortex's assurance case, not this one.
2. **The marketplace delivery channel.** The install path is a pin in Cortex's
   plugin manifest, consumed by the Claude Code host. Neither the host nor the
   marketplace is in scope here.
3. **The unpkg CDN fetch.** Four pages (`brain-viz.html`, `atom-viz.html`,
   `methodology-viz.html`) load three.js, OrbitControls, and 3d-force-graph
   from the public unpkg CDN at page load, disclosed in
   [PRIVACY.md](../PRIVACY.md). They are version-pinned but carry **no
   Subresource Integrity hash**, so a compromised CDN could serve different
   bytes into the browser context and no control here would notice. HTTPS
   counters a network attacker, not the CDN itself. This is a real hole in the
   §3.4 fingerprint argument, not a theoretical one, and it is why that
   argument is scoped to *first-party* assets. Tracked in
   [#50](https://github.com/cdeust/cortex-viz/issues/50); the other views are
   offline-safe.
4. **Test-suite adequacy as a security control.** Python statement coverage is
   **34%** measured 2026-07-28, and the JS harness cannot be measured at all by
   v8 (see §7). The suites are a functional gate, not a security argument.
5. **Availability.** Denial of service against a local, user-launched,
   short-lived developer tool is not modelled.

## 7. Verification status

| Control | Verified how | Result |
|---|---|---|
| Loopback binding, Host/Origin/CSRF guards | Source review, `cortex_viz/server/http_security.py` | Implemented |
| Path containment | Source review of `serve_shared_asset` | Implemented at that site, **10 alerts untriaged repo-wide** |
| DOM sanitisation | CodeQL | **6 alerts open** (2 high, 4 medium) |
| Provenance, SBOM, fingerprint | Workflow review, `Release.yml` | Implemented, **never exercised by a tag** |
| Action pinning | Workflow review | All actions SHA-pinned |
| Python test suite | `python -m pytest` on 2026-07-28 | 431 passed, 1 skipped |
| Python statement coverage | `pytest --cov=cortex_viz` on 2026-07-28 | **34%** |
| JS test suite | `npm test` on 2026-07-28 | 175 passed |
| JS statement coverage | `vitest run --coverage` on 2026-07-28 | **Not measurable**: the harness loads UI files with `new Function(code)`, so v8 attributes zero lines to the source files and reports a false 0% |
| JS test strength | Stryker, scoped | Survivors triaged in `tests/js/MUTATION_NOTES.md` |
| Static analysis | CodeQL, both languages, per push and weekly | Running, **192 alerts open** |
| Repository posture | OpenSSF Scorecard | **3.6** as of 2026-07-26 |

## 8. Conclusion, and how far it goes

The boundary that faces the widest adversary (a web page reaching the loopback
server, A1) carries three independent controls with distinct failure modes, and
the supply-chain boundary (A4) carries provenance, fingerprinting, an SBOM, and
pinned actions. Those two arguments stand.

Of the two boundaries that handle **untrusted data**, A2 (request-derived
paths) now stands as well: every site goes through one containment guard, the
findings are triaged to zero, and the one that was a real defect rather than an
analyser artifact is fixed and regression-tested (§3.2). A3 (rendered content)
does not: its static-analysis findings are still **untriaged**, so the argument
for it is incomplete by this document's own standard. cortex-viz should be read
as a **local, single-user developer tool with a credible but unfinished
data-handling argument**, not as a hardened service.

This case is revisited when a trust boundary moves, when a finding is triaged,
or at each release, whichever comes first.
