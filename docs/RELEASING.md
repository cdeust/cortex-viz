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
   requires `GITHUB_REF_NAME` to equal `v<version>` during a tagged release,
   and validates the built wheel after `uv build`; the release workflow runs
   that same gate before publication.
2. Promote the `## [Unreleased]` CHANGELOG entries to `## [<version>] - <date>`
   and leave `## [Unreleased]` in place, empty, for the next cycle. No gate
   enforces this, and the section is the human-readable release record.
3. Merge the release change and tag that commit as `v<version>`.
4. Push the tag. The release workflow builds and validates the wheel, source
   archive, SBOM, and UI manifest; attests them; publishes to PyPI; then creates
   the GitHub release.
5. Publish `server.json` with the official MCP Registry publisher after the
   matching PyPI version is available.
6. **Bump the marketplace pin.** Set this plugin's `version` to `<version>` in
   `cdeust/Cortex` → `.claude-plugin/marketplace.json`. Claude Code installs
   resolve through that manifest, so until it is bumped the release reaches zero
   installs no matter how green the tag build was. That is the failure mode
   recorded in Cortex #179, which cost six zetetic-team-subagents releases and
   two visualization releases. The release is not done until this step lands.
