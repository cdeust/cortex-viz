# Releasing

Releases use the canonical distribution identity `hypermnesia-mcp-viz`. The
GitHub repository, Cortex Viz product name, Claude Code plugin name, Python
import package, and `cortex-viz` compatibility command do not change.

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

1. Ensure `pyproject.toml`, `cortex_viz/identity.py`, `server.json`, the Claude
   plugin manifest, and the README badge carry the same release version.
   `python -m scripts.check_distribution_artifact` proves all five agree after
   `uv build`; it is the same gate CI and the release workflow run.
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
   two cortex-viz releases. The release is not done until this step lands.
