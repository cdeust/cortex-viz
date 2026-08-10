# Releasing

Releases use the canonical product, plugin, MCP server, Python distribution,
and console identity `hypermnesia-mcp-viz`. The Python import package remains
`cortex_viz`, and the historical GitHub repository URL remains
`cdeust/cortex-viz`; neither is a published compatibility identity.

## One-time PyPI setup

Before the first release, create a pending Trusted Publisher for the
`hypermnesia-mcp-viz` PyPI project with these exact values:

- Owner: `cdeust`
- Repository: `cortex-viz`
- Workflow: `Release.yaml`
- Environment: `pypi`

No long-lived PyPI API token is used. The release job obtains an OpenID Connect
identity through the `pypi` GitHub environment.

## Release flow

1. Ensure `pyproject.toml`, `cortex_viz/identity.py`, `server.json`, the Claude,
   Codex, and Gemini manifests, the Claude marketplace metadata/pin, the lock
   file, the README badge, and CHANGELOG carry the same release version.
   `python -m scripts.check_distribution_artifact` checks the source surfaces,
   requires `GITHUB_REF_NAME` to equal `v<version>` when `GITHUB_REF_TYPE=tag`,
   and validates the built wheel after `uv build`; the release workflow runs
   that same gate before publication.
2. Promote the `## [Unreleased]` CHANGELOG entries to `## [<version>] - <date>`
   and leave `## [Unreleased]` in place, empty, for the next cycle. No gate
   enforces this, and the section is the human-readable release record.
3. Merge the release change and tag that commit as `v<version>`.
4. Push the tag. The release workflow builds and validates the wheel, source
   archive, SBOM, and UI manifest; attests them; publishes to PyPI; then creates
   the GitHub release.
5. **Automatic.** The `publish-registry` job in the release workflow publishes
   `server.json` to the official MCP Registry (`registry.modelcontextprotocol.io`)
   with the `mcp-publisher` CLI, after `test` and `release` succeed — it can
   never run against a PyPI version that does not exist yet. Authentication
   uses `mcp-publisher login github-oidc`: the job's GitHub Actions OIDC token
   is exchanged for a registry credential scoped to `io.github.cdeust/*`, the
   same no-long-lived-credential pattern this repo already uses for PyPI
   Trusted Publishing (step 1's `pypi` environment). No secret is stored or
   required. Before publishing, the job re-checks that the `server.json`
   version exists on PyPI; after publishing, it queries the registry's own
   API and fails the job if the response does not match — a green
   `mcp-publisher publish` exit code is not treated as proof. Source:
   [modelcontextprotocol/registry — GitHub OIDC (CI/CD)](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/cli/commands.md)
   and [publishing from GitHub Actions](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/github-actions.mdx),
   verified 2026-08-10.

   **Recovery path.** If a release's registry publish is missing or stale —
   PyPI and the GitHub Release exist for a tag but the registry does not
   reflect it — re-run `publish-registry` via `workflow_dispatch` on
   `Release.yaml` with the `tag` input set to the existing tag (e.g.
   `v3.1.0`). This does not rebuild or re-publish the package; it only
   repairs the registry entry, and it refuses to run without an explicit tag.
6. **Bump the marketplace pin.** Set this plugin's `version` to `<version>` in
   `cdeust/Cortex` → `.claude-plugin/marketplace.json`. Claude Code installs
   resolve through that manifest, so until it is bumped the release reaches zero
   installs no matter how green the tag build was. That is the failure mode
   recorded in Cortex #179, which cost six zetetic-team-subagents releases and
   two visualization releases. The release is not done until this step lands.
