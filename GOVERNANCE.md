# Governance

cortex-viz has **one maintainer**. This document says so plainly rather than
describing committees that do not exist, because a governance document that
overstates the project is worse than none: it tells a user to expect a
continuity that is not there.

## Roles and responsibilities

| Role | Who | Responsibilities | How the role is held |
|---|---|---|---|
| **Maintainer** (sole) | Clement Deust ([@cdeust](https://github.com/cdeust)), admin@ai-architect.tools | Final say on scope and design. Reviews and merges every pull request. Cuts releases and bumps the marketplace pin. Triages security reports and issues advisories. Owns the release signing identity and repository settings. | GitHub `admin` on `cdeust/cortex-viz` |
| **Contributor** | Anyone who opens a pull request | Follows [CONTRIBUTING.md](CONTRIBUTING.md): tests with new functionality, regression test with a bug fix, CI green. | No permission needed, fork and open a pull request |
| **Reporter** | Anyone who opens an issue or a security advisory | Provides reproduction, version or commit, and impact. | No permission needed |

There are currently **no committers other than the maintainer**. When that
changes, this table changes with it, in the same pull request that grants the
access.

## How decisions are made

- **Ordinary changes** (a fix, a test, a documented feature): the maintainer
  reviews the pull request and merges it, or writes the reason for refusal in
  the pull request.
- **Design and scope changes** (a new view, a new data source, a dependency
  with real weight): discussed in a GitHub issue before implementation, so the
  argument is on the record and searchable. The maintainer decides.
- **Security changes**: handled privately under [SECURITY.md](SECURITY.md)
  until a fix ships, then disclosed in the release notes.

Every decision that changes what the software does is visible in the public
issue and pull request history. Nothing is decided in a private channel except
unfixed vulnerabilities.

## Continuity, stated honestly

**cortex-viz does not currently meet a bus factor of two.** One person holds
repository admin, the release signing identity, and the marketplace pin. If
that person became unavailable, the project could not create releases within a
week, and nobody else could accept a pull request.

What partially mitigates this today:

- The repository is **public** and the code is **MIT licensed**, so the source
  cannot become unavailable and anyone may fork and continue it.
- The **full build path is committed**: `.github/workflows/release.yml` is the
  only way a release is produced, so a fork inherits a working, attested
  release pipeline rather than a hand-cut process that lived in one person's
  shell history.
- **Issue and pull request history is public**, so the reasoning behind the
  design is recoverable without the maintainer.

What is **not** mitigated: nobody else can merge, release, or issue a security
advisory under this repository. Adding a second maintainer with admin rights is
tracked in [#48](https://github.com/cdeust/cortex-viz/issues/48) and is a
prerequisite for the OpenSSF `access_continuity` and `bus_factor` criteria.
Until it is closed, treat cortex-viz as a single-maintainer project and plan
accordingly.

## Changing this document

Governance changes through a pull request against this file, like everything
else.
