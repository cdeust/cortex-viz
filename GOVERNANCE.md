# Governance

cortex-viz has **one maintainer**. This document says so plainly rather than
describing committees that do not exist, because a governance document that
overstates the project is worse than none. It distinguishes continuity of the
MIT-licensed project results from preservation of one GitHub repository's
identity.

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
repository admin and the marketplace pin.

The project can nevertheless continue without that person's credentials. The
complete source, history, tests, plugin manifests, release workflow, and design
record are public under MIT. A successor can fork the repository, enable and
manage Issues, accept pull requests into the fork, and publish a tagged release
through the committed workflow under the fork's own GitHub OIDC identity. No
original signing key, package-registry token, domain, private dependency, or
legal assignment is required. Users can install from the successor's public
repository and marketplace URL.

That supplies the three OpenSSF continuity capabilities within a week:

1. fork the complete repository and enable its issue tracker;
2. publish a continuity notice naming the new canonical repository;
3. accept changes through the inherited CI-gated pull-request process; and
4. create a semantic-version tag, let the inherited workflow attest the
   artifacts under the fork identity, and publish the new install URL.

Past releases remain independently verifiable through their SHA-256 companions
and Sigstore attestations. What is **not** preserved without cooperation from
the original account is this repository's URL, existing issue permissions,
marketplace listing, or advisory channel. Adding a second maintainer with admin
rights, tracked in [#48](https://github.com/cdeust/cortex-viz/issues/48), would
preserve that identity and improve the `bus_factor` criterion. It is not
required to continue the MIT-licensed project results and their issue,
change-acceptance, and release process.

## Changing this document

Governance changes through a pull request against this file, like everything
else.
